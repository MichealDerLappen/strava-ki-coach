"""Garmin Connect Aktivitaeten-Synchronisation + Formkurven-Dashboard.

Urspruenglich auf der Strava-API aufgebaut; seit Juli 2026 werden die
Trainingsdaten direkt von Garmin Connect bezogen.

Meldet sich mit den Garmin-Zugangsdaten aus der .env an, laedt neue
Aktivitaeten herunter, berechnet die Formkurve nach dem Banister-Modell
und generiert ein interaktives Dark-Mode-Dashboard (formkurve.html),
das nach Google Drive synchronisiert wird.
"""

import json
import os
from datetime import datetime, date, timedelta

import requests
from dotenv import load_dotenv

try:
    from garminconnect import Garmin
except ImportError:
    os.system("pip install garminconnect garth")
    from garminconnect import Garmin

try:
    import plotly.graph_objects as go
except ImportError:
    os.system("pip install plotly")
    import plotly.graph_objects as go

from google_drive import upload_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ENV_PATH = os.path.join(BASE_DIR, ".env")
ACTIVITIES_PATH = os.path.join(BASE_DIR, "activities.json")
FORMKURVE_PATH  = os.path.join(BASE_DIR, "formkurve.html")
HEATMAP_PATH    = os.path.join(BASE_DIR, "heatmap.html")
STREAMS_DIR     = os.path.join(BASE_DIR, "streams")
DRIVE_FILENAME  = "activities.json"

# Open-Meteo: Koordinaten fuer die Wettervorhersage (Linz)
WEATHER_LATITUDE = 48.3064
WEATHER_LONGITUDE = 14.2858
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

# Banister-Modell: Grundlagen fuer die Formkurven-Berechnung
FTP = 250  # Functional Threshold Power in Watt
LTHR = 172  # angenommene Laktatschwelle (Herzfrequenz in bpm)
CTL_DAYS = 42  # Zeitkonstante fuer die Fitness (Chronic Training Load)
ATL_DAYS = 7  # Zeitkonstante fuer die Ermuedung (Acute Training Load)

# Intensitätsverteilung – 3-Zonen-Modell
HR_MAX = 190                           # maximale Herzfrequenz (bpm)
LT1 = round(0.82 * HR_MAX)            # Aerobe Schwelle (Zone 1/2-Grenze)
LT2 = round(0.92 * HR_MAX)            # Anaerobe Schwelle (Zone 2/3-Grenze)
INTENSITY_SPORT_TYPES = {"Ride", "Run", "Hike"}  # auf "Ride" reduzierbar

# Garmin-Session wird in diesem Ordner gecacht, damit kein erneutes
# Login bei jedem Aufruf noetig ist.
GARTH_HOME = os.path.join(BASE_DIR, ".garth")

load_dotenv(ENV_PATH)

# OpenRouteService: API-Key fuer echte Routen- und Hoehenmeter-Berechnung
ORS_API_KEY = os.getenv("ORS_API_KEY", "")

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD", "")

if not GARMIN_EMAIL or not GARMIN_PASSWORD:
    raise SystemExit(
        "GARMIN_EMAIL und GARMIN_PASSWORD muessen in der .env Datei "
        "gesetzt sein (siehe .env.example)."
    )

# Garmin-Aktivitaetstypen auf interne Typen abbilden
GARMIN_TYPE_MAP = {
    "cycling": "Ride",
    "road_biking": "Ride",
    "indoor_cycling": "Ride",
    "mountain_biking": "Ride",
    "gravel_cycling": "Ride",
    "running": "Run",
    "trail_running": "Run",
    "treadmill_running": "Run",
    "hiking": "Hike",
    "strength_training": "WeightTraining",
    "walking": "Walk",
    "swimming": "Swim",
    "open_water_swimming": "Swim",
    "yoga": "Workout",
    "fitness_equipment": "Workout",
}


def garmin_login():
    """Meldet sich bei Garmin Connect an. Beim ersten Aufruf werden die
    Zugangsdaten aus der .env verwendet und die Session in GARTH_HOME
    gecacht. Danach genuegt der Cache fuer erneute Anmeldungen."""

    client = Garmin(email=GARMIN_EMAIL, password=GARMIN_PASSWORD)
    try:
        client.login(GARTH_HOME)
        print("Garmin-Session aus Cache geladen.")
    except Exception:
        print("Melde bei Garmin Connect an ...")
        client.login()
        client.garth.dump(GARTH_HOME)
        print("Garmin-Session gespeichert.")
    return client


def garmin_to_internal(raw):
    """Wandelt einen Garmin-Aktivitaetseintrag in das interne Format um."""

    type_key = (raw.get("activityType") or {}).get("typeKey", "")
    activity_type = GARMIN_TYPE_MAP.get(type_key, type_key.title())

    def to_iso(raw_str, suffix="Z"):
        if not raw_str:
            return None
        return raw_str.replace(" ", "T") + suffix

    return {
        "id": raw.get("activityId"),
        "name": raw.get("activityName", ""),
        "type": activity_type,
        "start_date": to_iso(raw.get("startTimeGMT"), "Z"),
        "start_date_local": to_iso(raw.get("startTimeLocal"), ""),
        "distance": raw.get("distance") or 0,
        "moving_time": int(raw.get("movingDuration") or raw.get("duration") or 0),
        "elapsed_time": int(raw.get("duration") or 0),
        "total_elevation_gain": raw.get("elevationGain"),
        "average_watts": raw.get("avgPower"),
        "weighted_average_watts": raw.get("normPower") or raw.get("avgPower"),
        "average_heartrate": raw.get("averageHR") or raw.get("avgHr"),
        "max_heartrate": raw.get("maxHR"),
        "average_cadence": (raw.get("averageBikingCadenceInRevPerMinute")
                            or raw.get("avgRunCadence")),
        "suffer_score": None,
    }


def load_existing_activities():
    if not os.path.exists(ACTIVITIES_PATH):
        return []
    with open(ACTIVITIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_activities(activities):
    with open(ACTIVITIES_PATH, "w", encoding="utf-8") as f:
        json.dump(activities, f, ensure_ascii=False, indent=2)


def latest_start_date(activities):
    if not activities:
        return None
    return max(
        datetime.fromisoformat(a["start_date"].replace("Z", "+00:00"))
        for a in activities
    )


def sync_activities(client):
    """Laedt neue Garmin-Aktivitaeten herunter und haengt sie an activities.json an."""

    existing = load_existing_activities()
    last_date = latest_start_date(existing)
    existing_ids = {a.get("id") for a in existing}

    if last_date is None:
        print("Keine vorhandene activities.json – lade die letzten 100 Aktivitaeten ...")
        raw_list = client.get_activities(0, 100)
    else:
        start_str = (last_date.date() - timedelta(days=1)).isoformat()
        end_str = date.today().isoformat()
        print(f"Lade Aktivitaeten ab {start_str} ...")
        raw_list = client.get_activities_by_date(start_str, end_str)

    new_activities = [
        garmin_to_internal(r)
        for r in raw_list
        if r.get("activityId") not in existing_ids
    ]

    if not new_activities:
        print("Keine neuen Aktivitaeten gefunden.")
        return existing

    updated = sorted(
        existing + new_activities,
        key=lambda a: a["start_date"],
        reverse=True,
    )
    save_activities(updated)
    print(f"{len(new_activities)} neue Aktivitaet(en) gespeichert. Insgesamt {len(updated)}.")
    return updated


def sync_streams(client, activities):
    """Laedt sekundengenaue Power- und HR-Streams fuer alle Ausdaueraktivitaeten
    (Ride, Run, Hike) von Garmin und cached sie unter streams/<activity_id>.json.
    Dateien ohne 'heartrates'-Feld (altes Format) werden neu abgerufen."""

    import time as _time

    os.makedirs(STREAMS_DIR, exist_ok=True)

    candidates = [a for a in activities if a.get("type") in INTENSITY_SPORT_TYPES]

    def needs_fetch(activity):
        path = os.path.join(STREAMS_DIR, f"{activity['id']}.json")
        if not os.path.exists(path):
            return True
        try:
            cached = json.load(open(path, encoding="utf-8"))
            # Re-fetch if old format missing HR or GPS fields
            if ("heartrates" not in cached or "latitudes" not in cached) and not cached.get("reason"):
                return True
        except Exception:
            return True
        return False

    missing = [a for a in candidates if needs_fetch(a)]

    if not missing:
        print("Alle Streams bereits gecacht (inkl. HR).")
        return

    print(f"Lade Streams fuer {len(missing)} Aktivitaet(en) ...")
    for i, act in enumerate(missing):
        stream_path = os.path.join(STREAMS_DIR, f"{act['id']}.json")
        moving_time = max(int(act.get("moving_time") or 3600), 1)
        attempt = 0

        while attempt < 4:
            try:
                detail = client.get_activity_details(
                    act["id"], maxchart=moving_time, maxpoly=0
                )

                descriptors = {
                    d["key"]: d["metricsIndex"]
                    for d in detail.get("metricDescriptors", [])
                }
                pw_idx  = descriptors.get("directPower")
                hr_idx  = descriptors.get("directHeartRate")
                ts_idx  = descriptors.get("directTimestamp")
                lat_idx = descriptors.get("directLatitude")
                lon_idx = descriptors.get("directLongitude")
                ele_idx = descriptors.get("directElevation")

                watts, pw_ts = [], []
                heartrates, hr_ts = [], []
                latitudes, longitudes, elevations, gps_ts = [], [], [], []

                for row in detail.get("activityDetailMetrics", []):
                    m = row["metrics"]
                    ts = m[ts_idx] if ts_idx is not None else None
                    if ts is None:
                        continue
                    ts = float(ts)

                    pw  = m[pw_idx]  if pw_idx  is not None else None
                    hr  = m[hr_idx]  if hr_idx  is not None else None
                    lat = m[lat_idx] if lat_idx is not None else None
                    lon = m[lon_idx] if lon_idx is not None else None
                    ele = m[ele_idx] if ele_idx is not None else None

                    if pw is not None:
                        watts.append(float(pw)); pw_ts.append(ts)
                    if hr is not None:
                        heartrates.append(float(hr)); hr_ts.append(ts)
                    if lat is not None and lon is not None:
                        latitudes.append(float(lat))
                        longitudes.append(float(lon))
                        gps_ts.append(ts)
                        if ele is not None:
                            elevations.append(float(ele))

                with open(stream_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "has_power":        len(watts) > 0,
                        "has_hr":           len(heartrates) > 0,
                        "has_gps":          len(latitudes) > 0,
                        "activity_id":      act["id"],
                        "name":             act["name"],
                        "start_date":       act["start_date"],
                        "timestamps_ms":    pw_ts,
                        "watts":            watts,
                        "hr_timestamps_ms": hr_ts,
                        "heartrates":       heartrates,
                        "gps_timestamps_ms": gps_ts,
                        "latitudes":        latitudes,
                        "longitudes":       longitudes,
                        "elevations":       elevations,
                    }, f)

                print(f"  [{i+1}/{len(missing)}] {act['name']}: "
                      f"{len(watts)}W / {len(heartrates)}HR / {len(latitudes)}GPS Samples")
                break

            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "TooManyRequests" in msg.lower():
                    wait = 30 * (2 ** attempt)
                    print(f"  Rate-limit – warte {wait}s ...")
                    _time.sleep(wait)
                    attempt += 1
                elif "403" in msg or "404" in msg:
                    with open(stream_path, "w", encoding="utf-8") as f:
                        json.dump({"has_power": False, "has_hr": False,
                                   "activity_id": act["id"], "reason": msg[:80]}, f)
                    print(f"  [{i+1}/{len(missing)}] {act['name']}: "
                          f"nicht zugaenglich ({msg[:40]})")
                    break
                else:
                    print(f"  Fehler bei {act['name']}: {exc}")
                    break

        _time.sleep(1.5)


MMP_DURATIONS = [5, 15, 30, 60, 120, 300, 480, 600, 1200, 1800, 3600]


def compute_mmp_curves():
    """Berechnet Mean Maximal Power aus allen gecachten Streams.
    Gibt zwei Kurven zurueck: 'Letzte 42 Tage' und 'Saisonbestwert'."""

    try:
        import numpy as np
    except ImportError:
        os.system("pip install numpy")
        import numpy as np

    if not os.path.exists(STREAMS_DIR):
        return None

    cutoff_42 = (date.today() - timedelta(days=42)).isoformat()
    mmp_season = {d: 0.0 for d in MMP_DURATIONS}
    mmp_42 = {d: 0.0 for d in MMP_DURATIONS}
    count_season = count_42 = 0

    for fname in sorted(os.listdir(STREAMS_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(STREAMS_DIR, fname), encoding="utf-8") as f:
            stream = json.load(f)

        if not stream.get("has_power"):
            continue

        ts = np.array(stream["timestamps_ms"]) / 1000.0
        w  = np.array(stream["watts"])
        if len(ts) < 10:
            continue

        # Auf 1 Hz normalisieren via linearer Interpolation
        t_1hz = np.arange(ts[0], ts[-1], 1.0)
        w_1hz = np.interp(t_1hz, ts, w)

        # Rollendes Mittel via Cumsum – O(n) pro Dauer
        cs = np.concatenate(([0.0], np.cumsum(w_1hz)))
        n  = len(w_1hz)

        start_date = stream.get("start_date", "")[:10]
        is_42 = start_date >= cutoff_42
        count_season += 1
        if is_42:
            count_42 += 1

        for d in MMP_DURATIONS:
            if d > n:
                continue
            best = float(((cs[d:] - cs[:-d]) / d).max())
            if best > mmp_season[d]:
                mmp_season[d] = best
            if is_42 and best > mmp_42[d]:
                mmp_42[d] = best

    if count_season == 0:
        return None

    ftp_season = round(0.95 * mmp_season[1200]) if mmp_season.get(1200) else None
    ftp_42     = round(0.95 * mmp_42[1200])     if mmp_42.get(1200)     else None

    return {
        "durations":      MMP_DURATIONS,
        "mmp_season":     [mmp_season[d] or None for d in MMP_DURATIONS],
        "mmp_42":         [mmp_42[d] or None     for d in MMP_DURATIONS],
        "ftp_season":     ftp_season,
        "ftp_42":         ftp_42,
        "count_season":   count_season,
        "count_42":       count_42,
    }


def compute_hrtss_from_stream(activity_id):
    """Berechnet hrTSS sekundengenau aus dem HR-Stream.
    Formel: Σ (dt_s × (hr/LTHR)²) / 36
    → 1h bei LTHR = 3600/36 × 1² = 100 TSS."""
    path = os.path.join(STREAMS_DIR, f"{activity_id}.json")
    if not os.path.exists(path):
        return None
    try:
        s = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    hrs = s.get("heartrates", [])
    ts  = s.get("hr_timestamps_ms", [])
    if not hrs or not ts or len(hrs) != len(ts):
        return None
    total = 0.0
    for i in range(1, len(hrs)):
        dt = (ts[i] - ts[i - 1]) / 1000.0
        if 0 < dt < 30 and hrs[i] > 0:
            total += dt * (hrs[i] / LTHR) ** 2
    return round(total / 36, 1) if total > 0 else None


def compute_hike_metrics(activity):
    """Berechnet Wander-spezifische Metriken aus GPS+Elevation-Stream.
    Gibt dict mit gain, loss, vam_best, vam_median, downhill_stress zurück."""
    import math

    path = os.path.join(STREAMS_DIR, f"{activity['id']}.json")
    if not os.path.exists(path):
        return {}
    try:
        s = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}

    elevs   = s.get("elevations", [])
    lats    = s.get("latitudes", [])
    lons    = s.get("longitudes", [])
    gps_ts  = s.get("gps_timestamps_ms", [])

    if not elevs or len(elevs) < 2:
        return {}

    def hav_m(la1, lo1, la2, lo2):
        R = 6_371_000.0
        p1, p2 = math.radians(la1), math.radians(la2)
        dp = math.radians(la2 - la1)
        dl = math.radians(lo2 - lo1)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return 2 * R * math.asin(math.sqrt(a))

    gain = loss = 0.0
    cum_dist = 0.0
    vam_vals = []
    slope_times = {"flat": 0, "moderate": 0, "steep": 0, "extreme": 0}
    dist_km_profile = [0.0]
    elev_profile = [elevs[0]]

    n = min(len(elevs), len(lats), len(lons),
            len(gps_ts) if gps_ts else len(elevs))

    for i in range(1, n):
        de = elevs[i] - elevs[i - 1]
        dist_m = hav_m(lats[i-1], lons[i-1], lats[i], lons[i]) if lats else 0
        cum_dist += dist_m
        dist_km_profile.append(round(cum_dist / 1000, 3))
        elev_profile.append(elevs[i])

        if de > 0.3:
            gain += de
        elif de < -0.3:
            loss += abs(de)

        # VAM: nur für Aufstiegssegmente
        if de > 0.5 and gps_ts:
            dt_h = (gps_ts[i] - gps_ts[i - 1]) / 3_600_000.0
            if dt_h > 0:
                vam = de / dt_h
                if 50 < vam < 3000:
                    vam_vals.append(vam)

        # Hangneigung für Slope-Zonen
        if dist_m > 1 and gps_ts:
            grade = abs(de / dist_m) * 100
            dt_s = (gps_ts[i] - gps_ts[i - 1]) / 1000.0
            if 0 < dt_s < 30:
                if grade < 5:
                    slope_times["flat"] += dt_s
                elif grade < 15:
                    slope_times["moderate"] += dt_s
                elif grade < 30:
                    slope_times["steep"] += dt_s
                else:
                    slope_times["extreme"] += dt_s

    moving_hours = (activity.get("moving_time") or 0) / 3600
    vam_sorted = sorted(vam_vals)
    return {
        "elev_gain":        round(gain),
        "elev_loss":        round(loss),
        "downhill_stress":  round(loss * moving_hours),
        "vam_best":         round(max(vam_vals)) if vam_vals else 0,
        "vam_median":       round(vam_sorted[len(vam_sorted)//2]) if vam_vals else 0,
        "slope_times":      slope_times,
        "dist_km":          round(cum_dist / 1000, 1),
        "dist_km_profile":  dist_km_profile,
        "elev_profile":     elev_profile,
    }


def compute_hike_summary(activities):
    """Aggregiert Saison-Statistiken aller Wanderungen."""
    hikes = [a for a in activities if a.get("type") == "Hike"]
    if not hikes:
        return None

    total_gain = total_loss = total_dist_km = 0.0
    all_vam = []
    hike_details = []

    for a in sorted(hikes, key=lambda x: x.get("start_date", ""), reverse=True):
        m = compute_hike_metrics(a)
        hrtss = compute_hrtss_from_stream(a["id"])
        if m:
            total_gain    += m.get("elev_gain", 0)
            total_loss    += m.get("elev_loss", 0)
            total_dist_km += m.get("dist_km", 0)
            if m.get("vam_median"):
                all_vam.append(m["vam_median"])
        hike_details.append({
            "name":      a.get("name", ""),
            "date":      (a.get("start_date_local") or a.get("start_date", ""))[:10],
            "duration":  a.get("moving_time", 0),
            "hrtss":     hrtss,
            **m,
        })

    avg_vam = round(sum(all_vam) / len(all_vam)) if all_vam else 0
    return {
        "total_gain":     round(total_gain),
        "total_loss":     round(total_loss),
        "total_dist_km":  round(total_dist_km, 1),
        "total_hikes":    len(hikes),
        "avg_vam":        avg_vam,
        "hike_details":   hike_details,
    }


def plot_hike_analytics(hike_summary, weather_forecast=None):
    """Erzeugt HTML-Sektionen für das Wander-Modul:
    – Header-Stat-Boxes (Gesamt-Hm, VAM, Distanz/Touren)
    – Höhenprofil der letzten Tour
    – Hangneigungsverteilung (letzten 5 Touren, gestackte Balken)
    – Metriken-Tabelle der letzten 5 Touren
    – Wetter-Warnung (Regen > 40 % → Abstiegs-Hinweis)
    Gibt (header_html, charts_html, weather_warn_html) zurück.
    """
    if not hike_summary:
        return "", "", ""

    details = hike_summary.get("hike_details", [])

    # ── Header-Stat-Boxes ───────────────────────────────────────────────────
    total_gain   = hike_summary.get("total_gain", 0)
    total_loss   = hike_summary.get("total_loss", 0)
    total_dist   = hike_summary.get("total_dist_km", 0)
    total_hikes  = hike_summary.get("total_hikes", 0)
    avg_vam      = hike_summary.get("avg_vam", 0)

    header_html = f"""
    <div class="ftp-header" style="margin-top:24px;">
      <div class="ftp-block">
        <div class="ftp-label">Gesamt Aufstieg</div>
        <div class="ftp-value">{total_gain:,}<span class="ftp-unit"> m</span></div>
        <div class="ftp-sub">Abstieg {total_loss:,} m</div>
      </div>
      <div class="ftp-divider"></div>
      <div class="ftp-block">
        <div class="ftp-label">Ø VAM</div>
        <div class="ftp-value {'ftp-lower' if avg_vam < 600 else ''}">{avg_vam}<span class="ftp-unit"> m/h</span></div>
        <div class="ftp-sub">Aufstiegsgeschwindigkeit</div>
      </div>
      <div class="ftp-divider"></div>
      <div class="ftp-block">
        <div class="ftp-label">Distanz / Touren</div>
        <div class="ftp-value">{total_dist}<span class="ftp-unit"> km</span></div>
        <div class="ftp-sub">{total_hikes} Wanderungen</div>
      </div>
    </div>
"""

    # ── Wetter-Warnung ──────────────────────────────────────────────────────
    weather_warn_html = ""
    if weather_forecast:
        for day_idx, wf in enumerate(weather_forecast[:3]):
            if wf.get("precip_prob", 0) > 40:
                day_name = ["Morgen", "Übermorgen", f"in {day_idx+1} Tagen"][min(day_idx, 2)]
                weather_warn_html = (
                    f'<div class="weather-oracle-box" style="border-left-color:#e74c3c;margin-top:16px;">'
                    f'<h3 style="color:#e74c3c;">⚠️ Nässe-Warnung {day_name}</h3>'
                    f'<p>Regenwahrscheinlichkeit {wf["precip_prob"]} % – '
                    f'erhöhtes Risiko für nasse und rutschige Passagen im Abstieg. '
                    f'Stöcke mitehmen, Abstiegstempo reduzieren.</p>'
                    f'</div>'
                )
                break

    # ── Charts ──────────────────────────────────────────────────────────────
    last5 = [d for d in details if d.get("elev_profile")][:5]
    charts_html = ""

    if last5:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Höhenprofil der letzten Tour
        last = last5[0]
        dist_prof = last.get("dist_km_profile", [])
        elev_prof = last.get("elev_profile", [])

        fig_elev = go.Figure()
        fig_elev.add_trace(go.Scatter(
            x=dist_prof, y=elev_prof,
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(52,152,219,0.18)",
            line=dict(color="#3498db", width=2),
            name="Höhe (m)",
            hovertemplate="%{x:.2f} km · %{y:.0f} m<extra></extra>",
        ))
        fig_elev.update_layout(
            paper_bgcolor="#111418", plot_bgcolor="#111418",
            font=dict(color="#e6e6e6", size=12),
            margin=dict(t=40, b=40, l=60, r=20),
            height=260,
            title=dict(text=f"Höhenprofil: {last['name'] or last['date']}",
                       font=dict(size=14, color="#e6e6e6"), x=0),
            xaxis=dict(title="Distanz (km)", gridcolor="#2d333b",
                       showline=False, zeroline=False),
            yaxis=dict(title="Höhe (m)", gridcolor="#2d333b",
                       showline=False, zeroline=False),
        )
        elev_html = fig_elev.to_html(full_html=False, include_plotlyjs=False,
                                      config={"displayModeBar": False})

        # Hangneigungsverteilung letzter 5 Touren
        slope_names = []
        s_flat = s_mod = s_steep = s_ext = []

        slope_names  = [d.get("name") or d.get("date", "") for d in last5]
        s_flat   = [d.get("slope_times", {}).get("flat", 0)     / 60 for d in last5]
        s_mod    = [d.get("slope_times", {}).get("moderate", 0) / 60 for d in last5]
        s_steep  = [d.get("slope_times", {}).get("steep", 0)    / 60 for d in last5]
        s_ext    = [d.get("slope_times", {}).get("extreme", 0)  / 60 for d in last5]

        fig_slope = go.Figure()
        for vals, label, color in [
            (s_flat,  "Flach (<5 %)",     "#2ecc71"),
            (s_mod,   "Moderat (5–15 %)", "#f1c40f"),
            (s_steep, "Steil (15–30 %)",  "#e67e22"),
            (s_ext,   "Extrem (>30 %)",   "#e74c3c"),
        ]:
            fig_slope.add_trace(go.Bar(
                y=slope_names, x=vals, name=label,
                orientation="h",
                marker_color=color,
                hovertemplate="%{x:.0f} min<extra>" + label + "</extra>",
            ))
        fig_slope.update_layout(
            barmode="stack",
            paper_bgcolor="#111418", plot_bgcolor="#111418",
            font=dict(color="#e6e6e6", size=12),
            margin=dict(t=40, b=40, l=140, r=20),
            height=280,
            title=dict(text="Hangneigungsverteilung (letzte 5 Touren)",
                       font=dict(size=14, color="#e6e6e6"), x=0),
            xaxis=dict(title="Zeit (min)", gridcolor="#2d333b",
                       showline=False, zeroline=False),
            yaxis=dict(gridcolor="#2d333b", showline=False, zeroline=False),
            legend=dict(orientation="h", y=-0.22, x=0,
                        bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        )
        slope_html = fig_slope.to_html(full_html=False, include_plotlyjs=False,
                                        config={"displayModeBar": False})

        # Metriken-Tabelle letzter 5 Touren
        tbl_header = ["Tour", "Datum", "Zeit", "Dist.", "Aufstieg", "Abstieg",
                       "VAM", "hrTSS"]
        rows = []
        for d in last5:
            dur_h   = (d.get("duration") or 0) // 3600
            dur_m   = ((d.get("duration") or 0) % 3600) // 60
            dur_str = f"{dur_h}:{dur_m:02d} h"
            rows.append([
                d.get("name") or "–",
                d.get("date", "–"),
                dur_str,
                f"{d.get('dist_km', 0):.1f} km",
                f"↑ {d.get('elev_gain', 0):,} m",
                f"↓ {d.get('elev_loss', 0):,} m",
                f"{d.get('vam_median', 0)} m/h",
                str(d.get("hrtss") or "–"),
            ])

        fig_tbl = go.Figure(go.Table(
            header=dict(
                values=tbl_header,
                fill_color="#1c2128",
                font=dict(color="#9aa4af", size=12),
                line_color="#2d333b",
                align="left",
            ),
            cells=dict(
                values=list(zip(*rows)) if rows else [[] for _ in tbl_header],
                fill_color="#111418",
                font=dict(color="#e6e6e6", size=12),
                line_color="#2d333b",
                align="left",
            ),
        ))
        fig_tbl.update_layout(
            paper_bgcolor="#111418",
            margin=dict(t=40, b=10, l=0, r=0),
            height=220,
            title=dict(text="Letzte Touren – Kennzahlen",
                       font=dict(size=14, color="#e6e6e6"), x=0),
        )
        tbl_html = fig_tbl.to_html(full_html=False, include_plotlyjs=False,
                                    config={"displayModeBar": False})

        charts_html = (
            "<hr>\n"
            "<h2>Analyse Wanderungen</h2>\n"
            + elev_html
            + "\n"
            + slope_html
            + "\n"
            + tbl_html
        )

    return header_html, charts_html, weather_warn_html


def compute_tss(activity):
    """Berechnet bzw. schaetzt den Trainingsstress-Score (TSS) einer Aktivitaet."""

    moving_time = activity.get("moving_time", 0)

    if activity.get("type") == "Ride":
        normalized_power = activity.get("weighted_average_watts")
        if normalized_power:
            intensity_factor = normalized_power / FTP
            tss = (moving_time * normalized_power * intensity_factor) / (FTP * 3600) * 100
            return round(tss, 1)

    # Hike/Run: sekundengenaues hrTSS aus Stream bevorzugen
    hrtss = compute_hrtss_from_stream(activity.get("id"))
    if hrtss is not None:
        return hrtss

    suffer_score = activity.get("suffer_score")
    if suffer_score:
        return round(suffer_score * 0.6, 1)

    avg_hr = activity.get("average_heartrate")
    if avg_hr and moving_time:
        intensity_factor = avg_hr / LTHR
        tss = (moving_time / 3600) * intensity_factor * 100
        return round(tss, 1)

    return 0.0


def annotate_tss(activities):
    """Versieht jede Aktivitaet mit ihrem berechneten TSS-Wert."""

    for activity in activities:
        activity["tss"] = compute_tss(activity)
    return activities


def compute_form_curve(activities):
    """Berechnet CTL (Fitness), ATL (Ermuedung) und TSB (Form) tageweise
    fortlaufend von der ersten Aktivitaet bis heute (Banister-Modell)."""

    daily_tss = {}
    for activity in activities:
        start = activity.get("start_date_local", activity["start_date"])
        activity_date = datetime.fromisoformat(start.replace("Z", "+00:00")).date()
        daily_tss[activity_date] = daily_tss.get(activity_date, 0) + activity.get("tss", 0)

    if not daily_tss:
        return []

    current_date = min(daily_tss)
    end_date = date.today()

    ctl = atl = 0.0
    history = []

    while current_date <= end_date:
        tsb = ctl - atl
        tss_today = daily_tss.get(current_date, 0)
        ctl += (tss_today - ctl) / CTL_DAYS
        atl += (tss_today - atl) / ATL_DAYS
        history.append({
            "date": current_date.isoformat(),
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(tsb, 1),
        })
        current_date += timedelta(days=1)

    return history


def print_form_summary(history):
    """Gibt die heutigen Formkurven-Metriken farbig im Terminal aus."""

    if not history:
        print("Keine Daten fuer die Formkurve vorhanden.")
        return

    today = history[-1]
    BOLD = "\033[1m"
    BLUE = "\033[94m"
    ORANGE = "\033[93m"
    GREEN = "\033[92m"
    RESET = "\033[0m"

    print(
        f"{BOLD}Formkurve ({today['date']}):{RESET} "
        f"{BLUE}Fitness (CTL): {today['ctl']}{RESET} | "
        f"{ORANGE}Ermuedung (ATL): {today['atl']}{RESET} | "
        f"{GREEN}Form (TSB): {today['tsb']}{RESET}"
    )


def analyze_form(history):
    """Erstellt eine automatisierte Coach-Analyse basierend auf der Formkurve."""

    today = history[-1]
    ctl_today = today["ctl"]
    atl_today = today["atl"]
    tsb_today = today["tsb"]
    acwr_today = round(atl_today / ctl_today, 2) if ctl_today else 0.0

    if tsb_today > 5:
        form_status = (
            "🟢 Frische-Zone (Form ist hoch, dein Koerper ist maximal bereit "
            "fuer harte Belastungen oder einen Wettkampf)."
        )
    elif tsb_today >= -10:
        form_status = (
            "🟡 Optimaler Trainingsreiz (Du bist im perfekten Aufbau-Bereich. "
            "Die Ermuedung ist da, aber kontrolliert)."
        )
    else:
        form_status = (
            "🔴 Akute Ermuedungs-Zone (Die Trainingsbelastung war sehr hoch. "
            "Fokus strikt auf Erholung und Schlaf legen)."
        )

    ctl_week_ago = history[-8]["ctl"] if len(history) > 7 else history[0]["ctl"]
    if ctl_today > ctl_week_ago:
        trend_status = "📈 Deine Fitness (Langzeit-Basis) ist aktuell steigend."
    else:
        trend_status = (
            "📉 Deine Fitness stagniert oder sinkt leicht "
            "(Regenerationsphase oder Trainingspause)."
        )

    return {
        "ctl": ctl_today,
        "atl": atl_today,
        "tsb": tsb_today,
        "acwr": acwr_today,
        "form_status": form_status,
        "trend_status": trend_status,
    }


def recommend_training(history):
    """Ermittelt eine konkrete Trainings-Empfehlung basierend auf dem
    heutigen TSB-Wert, dem akuten Belastungsverhaeltnis (ACWR) und dem
    Wochentag."""

    today = history[-1]
    tsb_today = today["tsb"]
    ctl_today = today["ctl"]
    atl_today = today["atl"]
    acwr_today = round(atl_today / ctl_today, 2) if ctl_today else 0.0
    weekday = date.today().weekday()  # Montag=0 ... Sonntag=6

    # ACWR-Ueberlastungs-Warnung hat oberste Prioritaet, unabhaengig vom TSB.
    if acwr_today > 1.5:
        return {
            "title": f"⚠️ Überlastungs-Warnung (ACWR: {acwr_today})",
            "text": (
                "Du hast dein Trainingsvolumen in den letzten 7 Tagen zu "
                "drastisch im Vergleich zu deiner Langzeitbasis gesteigert. "
                "Das Risiko für Knie- oder Sehnenprobleme ist aktuell stark "
                "erhöht. Schalte dringend einen Gang zurück!"
            ),
            "color": "#e74c3c",
            "acwr": acwr_today,
        }

    # Sweet Spot: kontrollierter Belastungsanstieg bei ausgeglichener Form.
    if 0.8 <= acwr_today <= 1.3 and -10 <= tsb_today <= 10:
        return {
            "title": f"🎯 Perfekter Trainingsreiz (ACWR: {acwr_today})",
            "text": (
                "Du befindest dich sportwissenschaftlich im absoluten "
                "'Sweet Spot'. Deine Formkurve steigt kontrolliert und "
                "hocheffektiv an. Weiter so!"
            ),
            "color": "#2ecc71",
            "acwr": acwr_today,
        }

    if tsb_today > 10:
        return {
            "title": "⚡ Zeit zum Ballern (Wettkampf-Form / Intervalle)",
            "text": (
                "Deine Ermüdung ist komplett verflogen. Perfekter Zeitpunkt für "
                "hochintensive Intervalle (VO2max-Sprints), einen harten "
                "FTP-Test oder eine neue Bestzeit auf deinem Lieblings-Segment!"
            ),
            "color": "#3498db",
            "acwr": acwr_today,
        }

    if tsb_today >= -10:
        if weekday >= 4:  # Freitag, Samstag, Sonntag
            return {
                "title": "🚴‍♂️ Bereit für das dicke Brett (Long Ride)",
                "text": (
                    "Du bist im perfekten Trainingsbereich und es ist "
                    "Wochenende! Zeit für eine epische, lange "
                    "Grundlagenausdauer-Runde im Zone-2-Bereich auf deinem "
                    "Canyon. Achte darauf, an Hügeln nicht zu überziehen."
                ),
                "color": "#f1c40f",
                "acwr": acwr_today,
            }
        return {
            "title": "🏃‍♂️ Kontrollierter Formaufbau (Tempo / Kraftausdauer)",
            "text": (
                "Unter der Woche im Büro-Alltag: Ideal für einen soliden "
                "Tempolauf, ein knackiges Krafttraining oder eine "
                "strukturierte Sweet-Spot-Einheit auf der Rolle, um den "
                "Trainingsreiz hochzuhalten."
            ),
            "color": "#f1c40f",
            "acwr": acwr_today,
        }

    if tsb_today >= -20:
        return {
            "title": "☕ Gemütliches Kurbeln (Active Recovery / Zone 1)",
            "text": (
                "Die Ermüdung in deinen Muskeln ist spürbar. Wenn du "
                "trainierst, dann streng im regenerativen Bereich (Zone 1): "
                "Extrem lockeres Beine-Ausschütteln auf dem Rad oder ein ganz "
                "entspannter Spaziergang. Bloß kein Stress heute!"
            ),
            "color": "#e67e22",
            "acwr": acwr_today,
        }

    return {
        "title": "🛑 Strikte Regeneration (Couch-Tag gefordert!)",
        "text": (
            "Deine Ermüdung ist kritisch hoch. Um deine Sehnen zu schonen und "
            "dein Immunsystem zu schützen, bleibt das Canyon heute stehen. "
            "Fokus auf Dehnen, Blackroll, nahrhaftes Essen und mindestens "
            "8 Stunden Schlaf!"
        ),
        "color": "#e74c3c",
        "acwr": acwr_today,
    }


def fetch_weather_forecast():
    """Laedt die 7-Tage-Wettervorhersage fuer Linz von Open-Meteo (kein API-Key
    notwendig). Liefert pro Tag temp_max (°C), precip_prob (%) und
    wind_speed (km/h). Bei Netzwerkfehlern wird eine leere Liste
    zurueckgegeben, damit das Skript auch offline durchlaeuft."""

    params = {
        "latitude": WEATHER_LATITUDE,
        "longitude": WEATHER_LONGITUDE,
        "daily": "temperature_2m_max,precipitation_probability_max,wind_speed_10m_max",
        "timezone": "Europe/Berlin",
        "forecast_days": 8,
    }

    try:
        response = requests.get(WEATHER_API_URL, params=params, timeout=10)
        response.raise_for_status()
        daily = response.json()["daily"]

        forecast = []
        for i in range(1, 8):
            forecast.append({
                "date": daily["time"][i],
                "temp_max": daily["temperature_2m_max"][i],
                "precip_prob": daily["precipitation_probability_max"][i],
                "wind_speed": daily["wind_speed_10m_max"][i],
            })
        return forecast
    except Exception as exc:
        print(f"Wettervorhersage konnte nicht geladen werden: {exc}")
        return []


def plot_mmp(mmp_data):
    """Erzeugt einen Plotly-Chart der Power-Duration-Kurve und gibt ihn als
    HTML-Snippet zurueck. Gibt einen leeren String zurueck wenn keine Daten."""

    if not mmp_data:
        return ""

    dur  = mmp_data["durations"]
    s42  = mmp_data["mmp_42"]
    sall = mmp_data["mmp_season"]

    def dur_label(d):
        if d < 60:
            return f"{d}s"
        return f"{d // 60}min" if d < 3600 else "60min"

    tick_vals  = [5, 30, 60, 300, 1200, 3600]
    tick_texts = ["5s", "30s", "1min", "5min", "20min", "60min"]

    traces = []

    # Saisonbestwert (gedämpftes Grau) zuerst, damit blau darüber liegt
    valid_all = [(d, v) for d, v in zip(dur, sall) if v]
    if valid_all:
        traces.append(go.Scatter(
            x=[d for d, _ in valid_all],
            y=[v for _, v in valid_all],
            mode="lines+markers",
            name="Saisonbestwert",
            line=dict(color="#6e7a8a", width=2, dash="dot"),
            marker=dict(size=6),
            hovertemplate="%{y:.0f} W @ %{text}<extra>Saisonbestwert</extra>",
            text=[dur_label(d) for d, _ in valid_all],
        ))

    # Letzte 42 Tage (blau)
    valid_42 = [(d, v) for d, v in zip(dur, s42) if v]
    if valid_42:
        traces.append(go.Scatter(
            x=[d for d, _ in valid_42],
            y=[v for _, v in valid_42],
            mode="lines+markers",
            name="Letzte 42 Tage",
            line=dict(color="#3498db", width=2.5),
            marker=dict(size=7, color="#3498db"),
            hovertemplate="%{y:.0f} W @ %{text}<extra>Letzte 42 Tage</extra>",
            text=[dur_label(d) for d, _ in valid_42],
        ))

    annotations = []
    ftp = mmp_data.get("ftp_42") or mmp_data.get("ftp_season")
    mmp_20 = next((v for d, v in zip(dur, s42) if d == 1200 and v), None) or \
             next((v for d, v in zip(dur, sall) if d == 1200 and v), None)
    if mmp_20 and ftp:
        annotations.append(dict(
            x=1200, y=mmp_20,
            text=f"<b>FTP ≈ {ftp} W</b>",
            showarrow=True, arrowhead=2, arrowcolor="#f1c40f",
            font=dict(color="#f1c40f", size=13),
            bgcolor="#1c2128", bordercolor="#f1c40f", borderwidth=1,
            ax=40, ay=-35,
        ))

    fig = go.Figure(traces)
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111418",
        plot_bgcolor="#111418",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60, b=60),
        height=420,
        annotations=annotations,
        xaxis=dict(
            type="log",
            tickvals=tick_vals,
            ticktext=tick_texts,
            title="Dauer",
            gridcolor="#2d333b",
        ),
        yaxis=dict(title="Watt", gridcolor="#2d333b"),
    )

    note = (f"Basiert auf {mmp_data['count_42']} Ride(s) der letzten 42 Tage "
            f"/ {mmp_data['count_season']} Ride(s) gesamt mit Power-Meter.")

    return (
        fig.to_html(full_html=False, include_plotlyjs=False, div_id="mmp-chart")
        + f'<p class="status-text" style="font-size:13px;color:#6e7a8a;">{note}</p>'
    )


def plot_intensity_distribution(activities):
    """Zeitgewichtete Intensitätsverteilung nach 3-Zonen-Modell (Durchschnitts-HF).
    Gibt (html_snippet, p1, p2, p3, skipped) zurück."""
    from datetime import datetime
    from collections import defaultdict

    eligible = [
        a for a in activities
        if a.get("type") in INTENSITY_SPORT_TYPES and a.get("average_heartrate")
    ]
    skipped = sum(
        1 for a in activities
        if a.get("type") in INTENSITY_SPORT_TYPES and not a.get("average_heartrate")
    )

    if not eligible:
        return "", 0, 0, 0, skipped

    z1_min = z2_min = z3_min = 0
    weeks = defaultdict(lambda: [0, 0, 0])
    fallback_count = 0  # Aktivitaeten ohne HR-Stream, nutzen avg_hr

    for a in eligible:
        raw_date = a.get("start_date_local") or a.get("start_date") or ""
        try:
            dt = datetime.fromisoformat(raw_date[:16])
            wk = dt.strftime("%Y-W%V")
        except Exception:
            wk = None

        stream_path = os.path.join(STREAMS_DIR, f"{a['id']}.json")
        hr_seconds = None
        if os.path.exists(stream_path):
            try:
                s = json.load(open(stream_path, encoding="utf-8"))
                hrs = s.get("heartrates", [])
                tss = s.get("hr_timestamps_ms", [])
                if hrs and tss and len(hrs) == len(tss):
                    hr_seconds = []
                    for j in range(len(tss)):
                        dt_sec = (tss[j] - tss[j-1]) / 1000.0 if j > 0 else 1.0
                        dt_sec = max(0.0, min(dt_sec, 10.0))  # Ausreisser kappen
                        hr_seconds.append((hrs[j], dt_sec))
            except Exception:
                pass

        if hr_seconds:
            for hr, secs in hr_seconds:
                mins_frac = secs / 60.0
                if hr < LT1:
                    z1_min += mins_frac
                    if wk: weeks[wk][0] += mins_frac
                elif hr < LT2:
                    z2_min += mins_frac
                    if wk: weeks[wk][1] += mins_frac
                else:
                    z3_min += mins_frac
                    if wk: weeks[wk][2] += mins_frac
        else:
            # Fallback: Durchschnitts-HF × moving_time
            fallback_count += 1
            hr = a.get("average_heartrate", 0)
            mins = (a.get("moving_time") or 0) / 60.0
            if hr < LT1:
                z1_min += mins
                if wk: weeks[wk][0] += mins
            elif hr < LT2:
                z2_min += mins
                if wk: weeks[wk][1] += mins
            else:
                z3_min += mins
                if wk: weeks[wk][2] += mins

    total = z1_min + z2_min + z3_min
    if total == 0:
        return "", 0, 0, 0, skipped

    p1 = round(100 * z1_min / total)
    p2 = round(100 * z2_min / total)
    p3 = 100 - p1 - p2

    sorted_weeks = sorted(weeks.keys())

    # Gesamt-Ansicht: horizontaler Stacked-Bar
    overall_traces = [
        go.Bar(name="Zone 1 – Locker", orientation="h",
               x=[z1_min], y=["Gesamtzeit"], marker_color="#2ecc71",
               text=[f"{p1}%"], textposition="inside", textfont=dict(size=13),
               hovertemplate=f"{z1_min} min ({p1}%)<extra>Zone 1 – Locker</extra>",
               visible=True),
        go.Bar(name="Zone 2 – Mittel", orientation="h",
               x=[z2_min], y=["Gesamtzeit"], marker_color="#f1c40f",
               text=[f"{p2}%"], textposition="inside", textfont=dict(size=13),
               hovertemplate=f"{z2_min} min ({p2}%)<extra>Zone 2 – Mittel</extra>",
               visible=True),
        go.Bar(name="Zone 3 – Hart", orientation="h",
               x=[z3_min], y=["Gesamtzeit"], marker_color="#e74c3c",
               text=[f"{p3}%" if p3 > 0 else ""], textposition="inside",
               textfont=dict(size=13),
               hovertemplate=f"{z3_min} min ({p3}%)<extra>Zone 3 – Hart</extra>",
               visible=True),
    ]

    # Wochenweise-Ansicht: vertikaler Stacked-Bar
    weekly_traces = [
        go.Bar(name="Zone 1 – Locker",
               x=sorted_weeks, y=[weeks[w][0] for w in sorted_weeks],
               marker_color="#2ecc71",
               hovertemplate="%{y} min<extra>Zone 1 – Locker</extra>",
               visible=False),
        go.Bar(name="Zone 2 – Mittel",
               x=sorted_weeks, y=[weeks[w][1] for w in sorted_weeks],
               marker_color="#f1c40f",
               hovertemplate="%{y} min<extra>Zone 2 – Mittel</extra>",
               visible=False),
        go.Bar(name="Zone 3 – Hart",
               x=sorted_weeks, y=[weeks[w][2] for w in sorted_weeks],
               marker_color="#e74c3c",
               hovertemplate="%{y} min<extra>Zone 3 – Hart</extra>",
               visible=False),
    ]

    fig = go.Figure(overall_traces + weekly_traces)
    fig.update_layout(
        barmode="stack",
        template="plotly_dark",
        paper_bgcolor="#111418",
        plot_bgcolor="#111418",
        height=280,
        margin=dict(t=60, b=50, l=90, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
        xaxis=dict(title="Minuten", gridcolor="#2d333b"),
        yaxis=dict(gridcolor="#2d333b"),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=0.0, y=1.22, xanchor="left",
            showactive=True,
            bgcolor="#1c2128", bordercolor="#444",
            font=dict(color="#ddd"),
            buttons=[
                dict(label="Gesamt",
                     method="update",
                     args=[{"visible": [True, True, True, False, False, False]},
                           {"height": 280,
                            "xaxis": {"title": "Minuten", "gridcolor": "#2d333b"},
                            "yaxis": {"title": "", "gridcolor": "#2d333b"}}]),
                dict(label="Wochenweise",
                     method="update",
                     args=[{"visible": [False, False, False, True, True, True]},
                           {"height": 420,
                            "xaxis": {"title": "Kalenderwoche", "gridcolor": "#2d333b"},
                            "yaxis": {"title": "Minuten", "gridcolor": "#2d333b"}}]),
            ],
        )],
    )

    # Interpretationstext
    if p3 == 0 and p2 > 25:
        verdict = (f"Hoher Mittelanteil ({p2}%), keine messbaren Belastungsspitzen in Z3. "
                   f"Typisch für durchschnittliche HF als Zonenmaß – Intervalle werden "
                   f"durch den Gesamtdurchschnitt unsichtbar.")
    elif p1 >= 70 and p3 >= 10:
        verdict = f"Gut polarisiert: viel Grundlage ({p1}%) mit klarem Hartanteil ({p3}%)."
    elif p1 >= 70:
        verdict = f"Viel Grundlagenarbeit ({p1}%), kaum Intensität ({p3}%)."
    else:
        verdict = f"Gemischte Verteilung ohne klares Muster."

    skipped_note = f" {skipped} Aktivität(en) ohne HF-Daten übersprungen." if skipped else ""
    fallback_note = (f" {fallback_count} Aktivität(en) ohne HR-Stream nutzen Durchschnitts-HF"
                     f" als Näherung." if fallback_count else "")

    disclaimer = (
        '<p class="status-text" style="font-size:11px;color:#6e7a8a;margin-bottom:4px;">'
        f'Zonenverteilung sekundengenau aus HR-Zeitreihe (LT1={LT1} bpm, LT2={LT2} bpm, '
        f'HFmax={HR_MAX}).{fallback_note}'
        '</p>'
    )
    interp = (
        '<p class="status-text" style="font-size:13px;color:#aaa;margin-top:6px;">'
        f'Aktuell {p1}% locker / {p2}% mittel / {p3}% hart – {verdict}{skipped_note}'
        '</p>'
    )

    return (
        disclaimer
        + fig.to_html(full_html=False, include_plotlyjs=False, div_id="intensity-chart")
        + interp,
        p1, p2, p3, skipped,
    )


def plot_formkurve(history, activities, weather_forecast=None, mmp_data=None,
                   heatmap_embed=None, hike_heatmap_embed=None,
                   hike_summary=None):
    """Erstellt ein interaktives Dashboard der Formkurve (CTL, ATL, TSB)
    inkl. Coach-Analyse als 'formkurve.html'."""

    if not history:
        return

    if weather_forecast is None:
        weather_forecast = []

    dates = [h["date"] for h in history]
    ctl = [h["ctl"] for h in history]
    atl = [h["atl"] for h in history]
    tsb = [h["tsb"] for h in history]
    tsb_pos = [v if v >= 0 else 0 for v in tsb]
    tsb_neg = [v if v < 0 else 0 for v in tsb]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates, y=tsb_pos,
        name="Form (TSB) positiv",
        mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        fill="tozeroy",
        fillcolor="rgba(46, 204, 113, 0.25)",
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=tsb_neg,
        name="Form (TSB) negativ",
        mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        fill="tozeroy",
        fillcolor="rgba(231, 76, 60, 0.25)",
        hoverinfo="skip",
        showlegend=False,
    ))

    fig.add_trace(go.Scatter(
        x=dates, y=ctl,
        name="Fitness (CTL)",
        mode="lines",
        line=dict(color="#2ecc71", width=3),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=atl,
        name="Ermuedung (ATL)",
        mode="lines",
        line=dict(color="#e74c3c", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=tsb,
        name="Form (TSB)",
        mode="lines",
        line=dict(color="#f1c40f", width=2.5),
    ))

    # Zeitleiste der Aktivitaeten als feine Striche am unteren Rand des Charts.
    marker_y = min(min(ctl), min(atl), min(tsb)) - 5
    activity_dates = []
    activity_text = []
    for activity in activities:
        activity_dates.append(activity["start_date"][:10])
        distance_km = activity.get("distance", 0) / 1000
        activity_text.append(
            f"{activity.get('name', '')}<br>"
            f"{activity.get('type', '')}<br>"
            f"{distance_km:.1f} km"
        )

    fig.add_trace(go.Scatter(
        x=activity_dates,
        y=[marker_y] * len(activity_dates),
        name="Aktivitaeten",
        mode="markers",
        marker=dict(symbol="line-ns-open", size=10, color="rgba(220, 220, 220, 0.6)", line=dict(width=1.5)),
        text=activity_text,
        hovertemplate="%{text}<extra></extra>",
    ))

    # Platzhalter-Traces fuer die Live-Prognose aus dem Zukunfts-Simulator
    # (Indizes werden unten im JavaScript ueber Plotly.restyle aktualisiert).
    today_date = dates[-1]
    forecast_dates = [today_date] * 8
    forecast_ctl = [ctl[-1]] * 8
    forecast_atl = [atl[-1]] * 8
    forecast_tsb = [tsb[-1]] * 8

    fig.add_trace(go.Scatter(
        x=forecast_dates, y=forecast_ctl,
        name="Fitness (Prognose)",
        mode="lines",
        line=dict(color="#2ecc71", width=3, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=forecast_dates, y=forecast_atl,
        name="Ermuedung (Prognose)",
        mode="lines",
        line=dict(color="#e74c3c", width=1.5, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=forecast_dates, y=forecast_tsb,
        name="Form (Prognose)",
        mode="lines",
        line=dict(color="#f1c40f", width=2.5, dash="dot"),
    ))

    fig.add_hline(y=0, line_width=1, line_dash="dash", line_color="grey")

    fig.update_layout(
        title="Formkurve (Banister-Modell)",
        template="plotly_dark",
        hovermode="x unified",
        xaxis_title="Datum",
        yaxis_title="Trainingsstress",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        paper_bgcolor="#111418",
        plot_bgcolor="#111418",
        margin=dict(t=120),
        height=700,
    )

    fig.update_xaxes(
        rangeslider=dict(visible=True, bgcolor="#1c2128", bordercolor="#2d333b", borderwidth=1),
        rangeselector=dict(
            bgcolor="#1c2128",
            activecolor="#3498db",
            bordercolor="#2d333b",
            borderwidth=1,
            font=dict(color="#e6e6e6"),
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(label="Alles", step="all"),
            ],
        ),
    )

    analysis = analyze_form(history)
    recommendation = recommend_training(history)
    weather_json = json.dumps(weather_forecast)

    with open(os.path.join(TEMPLATES_DIR, "dashboard.css"), encoding="utf-8") as f:
        dashboard_css = f.read().replace("__RECOMMENDATION_COLOR__", recommendation["color"])

    with open(os.path.join(TEMPLATES_DIR, "dashboard.js"), encoding="utf-8") as f:
        dashboard_js = (
            f.read()
            .replace("__FTP__", str(FTP))
            .replace("__CTL_TODAY__", str(analysis["ctl"]))
            .replace("__ATL_TODAY__", str(analysis["atl"]))
            .replace("__WEATHER_FORECAST_JSON__", weather_json)
        )

    # Eine Karte mit zwei Slidern (Dauer & Leistung) fuer jeden der naechsten 7 Tage.
    german_weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    day_cards = []
    for day in range(1, 8):
        plan_date = date.today() + timedelta(days=day)
        label = f"{german_weekdays[plan_date.weekday()]}, {plan_date.strftime('%d.%m.')}"

        weather = weather_forecast[day - 1] if day - 1 < len(weather_forecast) else None
        weather_html = ""
        warnings_html = ""
        card_classes = "day-card"

        if weather:
            temp = weather["temp_max"]
            precip = weather["precip_prob"]
            wind = weather["wind_speed"]

            if precip > 60:
                icon = "🌧️"
            elif wind > 25:
                icon = "💨"
            elif precip > 20:
                icon = "⛅"
            else:
                icon = "☀️"

            weather_html = f"""
                <div class="day-weather">
                    <div class="weather-icon">{icon}</div>
                    <div class="weather-details">
                        <div>{temp:.0f}°C</div>
                        <div>💧 {precip:.0f}%</div>
                        <div>💨 {wind:.0f} km/h</div>
                    </div>
                </div>"""

            if precip > 60:
                card_classes += " day-card-rain"
                warnings_html += '\n            <div class="day-warning">🌧️ Regenjacke einpacken!</div>'
            if wind > 25:
                card_classes += " day-card-wind"
                warnings_html += '\n            <div class="day-warning">💨 Starker Wind! Aero-Position halten oder Windschatten suchen</div>'

        day_cards.append(f"""        <div class="{card_classes}" data-day="{day}" data-weekday="{plan_date.weekday()}">
            <div class="day-card-header">
                <span>{label}</span>{weather_html}
            </div>
            <div class="compact-slider">
                <label>Dauer (min)</label>
                <div class="slider-row">
                    <input type="range" class="day-duration" data-day="{day}" min="0" max="360" step="5" value="0">
                    <span class="compact-value" id="durationValue{day}">0 min</span>
                </div>
            </div>
            <div class="compact-slider">
                <label>Leistung (W)</label>
                <div class="slider-row">
                    <input type="range" class="day-power" data-day="{day}" min="100" max="400" step="5" value="180">
                    <span class="compact-value" id="powerValue{day}">180 W</span>
                </div>
            </div>
            <div class="day-tss">TSS: <span id="tssValue{day}">0.0</span></div>
            <div class="training-suggestion" id="trainingSuggestion{day}"></div>{warnings_html}
        </div>""")
    day_cards_html = "\n".join(day_cards)
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="formkurve-chart")

    ftp_est = (mmp_data.get("ftp_42") or mmp_data.get("ftp_season")) if mmp_data else None

    if heatmap_embed:
        heatmap_section = (
            "    <hr>\n"
            "    <h2>Heatmap aller Rides</h2>\n"
            "    <p class=\"status-text\" style=\"font-size:12px;color:#6e7a8a;\">"
            "Layer-Umschalter oben rechts in der Karte: Häufigkeit / Geschwindigkeit."
            "</p>\n"
            + "    " + heatmap_embed
        )
    else:
        heatmap_section = ""

    hike_header_html, hike_charts_html, hike_weather_warn = plot_hike_analytics(
        hike_summary, weather_forecast=weather_forecast
    )

    if hike_heatmap_embed:
        hike_heatmap_section = (
            "    <h2>Heatmap aller Wanderungen</h2>\n"
            "    <p class=\"status-text\" style=\"font-size:12px;color:#6e7a8a;\">"
            "Layer-Umschalter oben rechts in der Karte: Häufigkeit / Geschwindigkeit."
            "</p>\n"
            + "    " + hike_heatmap_embed
        )
    else:
        hike_heatmap_section = ""

    if hike_header_html or hike_heatmap_section or hike_charts_html:
        hike_section = (
            hike_header_html
            + hike_weather_warn
            + hike_charts_html
            + "\n"
            + hike_heatmap_section
        )
    else:
        hike_section = "<p class=\"status-text\">Keine Wanderungen mit GPS-Daten verfügbar.</p>"

    mmp_chart = plot_mmp(mmp_data)
    if mmp_chart:
        mmp_section = (
            "<h2>Power-Duration-Kurve (Mean Maximal Power)</h2>\n"
            + mmp_chart
            + "\n    <hr>"
        )
    else:
        mmp_section = ""

    intensity_html, p1, p2, p3, skipped_hr = plot_intensity_distribution(activities)
    if intensity_html:
        intensity_section = (
            "<h2>Intensitätsverteilung (Polarisierung)</h2>\n"
            + intensity_html
            + "\n    <hr>"
        )
        print(f"Intensitätsverteilung: {p1}% locker / {p2}% mittel / {p3}% hart "
              f"({skipped_hr} Akt. ohne HF übersprungen)")
    else:
        intensity_section = ""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Formkurve</title>
<style>
{dashboard_css}
</style>
</head>
<body>
    <div class="sport-selector">
        <select id="sportSelect" onchange="switchSport(this.value)">
            <option value="cycling">🚴 Radfahren</option>
            <option value="hiking">🥾 Wanderungen</option>
        </select>
    </div>

    <div id="cycling-content">
    <div class="ftp-header">
        <div class="ftp-block">
            <div class="ftp-label">FTP konfiguriert</div>
            <div class="ftp-value">{FTP} <span class="ftp-unit">W</span></div>
            <div class="ftp-sub">Basis für TSS-Berechnung</div>
        </div>
        <div class="ftp-divider"></div>
        <div class="ftp-block">
            <div class="ftp-label">FTP-Schätzung (MMP 20 min)</div>
            <div class="ftp-value{' ftp-lower' if ftp_est and ftp_est < FTP else ''}">{ftp_est if ftp_est else "–"} <span class="ftp-unit">{"W" if ftp_est else ""}</span></div>
            <div class="ftp-sub">95 % des 20-min-Bestwerts aus {mmp_data['count_season'] if mmp_data else 0} Ride(s)</div>
        </div>
    </div>
    <div class="highlight-box" id="optimalWindowBox">Berechne optimales Trainingsfenster ...</div>
    <div class="recommendation-box{' acwr-warning' if recommendation['acwr'] > 1.5 else ''}">
        <h3>Coach-Empfehlung für deine nächste Einheit</h3>
        <p><strong>{recommendation['title']}</strong></p>
        <p>{recommendation['text']}</p>
    </div>
    {chart_html}
    <hr>
    <h2>Coach-Analyse deines aktuellen Zustands</h2>
    <div class="metrics">
        <div class="metric-box">
            <div class="label">Fitness (CTL)</div>
            <div class="value ctl">{analysis['ctl']}</div>
            <div class="tooltip-text">Chronic Training Load (Fitness): Spiegelt deine langfristige Trainingsbelastung der letzten 42 Tage wider. Ein höherer Wert bedeutet ein stärkeres Ausdauer-Fundament.</div>
        </div>
        <div class="metric-box">
            <div class="label">Ermuedung (ATL)</div>
            <div class="value atl">{analysis['atl']}</div>
            <div class="tooltip-text">Acute Training Load (Ermüdung): Bildet den kurzfristigen Stress deiner Trainingseinheiten der letzten 7 Tage ab. Steigt nach harten Einheiten schnell an und sinkt bei Pause ebenso schnell.</div>
        </div>
        <div class="metric-box">
            <div class="label">Form (TSB)</div>
            <div class="value tsb">{analysis['tsb']}</div>
            <div class="tooltip-text">Training Stress Balance (Form / Frische): Berechnet aus CTL minus ATL. Ein leicht negativer bis ausgeglichener Wert zeigt optimalen Trainingsreiz. Ein positiver Wert bedeutet hohe Frische und Rennform.</div>
        </div>
        <div class="metric-box metric-box-small">
            <div class="label">ACWR</div>
            <div class="value acwr">{analysis['acwr']}</div>
            <div class="tooltip-text">Acute:Chronic Workload Ratio (ACWR): Verhältnis von ATL (akute Belastung, 7 Tage) zu CTL (chronische Basis, 42 Tage). Werte zwischen 0,8 und 1,3 gelten als optimaler "Sweet Spot". Werte über 1,5 deuten auf eine zu schnelle Belastungssteigerung und erhöhtes Verletzungsrisiko hin.</div>
        </div>
    </div>
    <p class="status-text">{analysis['form_status']}</p>
    <p class="status-text">{analysis['trend_status']}</p>

    <hr>
{mmp_section}
{intensity_section}
    <h2>Wochenplanung (Vorschau naechste 7 Tage)</h2>
    <p class="status-text">
        Stelle fuer jeden Tag der kommenden Woche die geplante Dauer und
        Leistung ein, um live zu sehen, wie sich deine Form (TSB) entwickelt.
    </p>

    <div class="weather-oracle-box" id="weatherOracleBox">
        <h3>🌦️ Die 3 besten Outdoor-Trainingstage dieser Woche</h3>
        <p id="weatherOracleDays">Berechne...</p>
        <p id="weatherOracleTip"></p>
    </div>

    <div class="day-filter">
        <span class="day-filter-label">Ansicht filtern:</span>
        <button class="filter-btn active" data-weekday="0">Mo</button>
        <button class="filter-btn active" data-weekday="1">Di</button>
        <button class="filter-btn active" data-weekday="2">Mi</button>
        <button class="filter-btn active" data-weekday="3">Do</button>
        <button class="filter-btn active" data-weekday="4">Fr</button>
        <button class="filter-btn active" data-weekday="5">Sa</button>
        <button class="filter-btn active" data-weekday="6">So</button>
    </div>

    <div class="day-grid" id="dayGrid">
{day_cards_html}
    </div>

    <div class="sim-results">
        <div class="sim-box">
            <div class="label">Tiefpunkt TSB (geplante Woche)</div>
            <div class="value" id="simLow">-</div>
        </div>
    </div>

    <script>
{dashboard_js}
    </script>

{heatmap_section}
    </div><!-- /cycling-content -->

    <div id="hiking-content" style="display:none;">
{hike_section}
    </div><!-- /hiking-content -->

</body>
</html>
"""

    with open(FORMKURVE_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Formkurve gespeichert unter {FORMKURVE_PATH}")


def generate_heatmap(activities, sport_types=None):
    """Heatmap für beliebige Sportarten mit zwei umschaltbaren Layern:
    - Häufigkeit: grün (1×) → rot (am häufigsten)
    - Geschwindigkeit: blau (langsam) → rot (schnell), berechnet aus GPS+Zeit"""
    import math
    import folium
    from folium.plugins import HeatMap

    if sport_types is None:
        sport_types = {"Ride"}
    ride_ids = {a["id"] for a in activities if a.get("type") in sport_types}
    GRID = 4  # 4 Dezimalstellen ≈ ~11m Rasterzellen

    def haversine_m(lat1, lon1, lat2, lon2):
        R = 6_371_000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    cell_rides: dict[tuple, int] = {}          # cell → unique ride count
    cell_speeds: dict[tuple, list] = {}         # cell → list of speed samples (km/h)
    all_lats, all_lons = [], []

    for fname in sorted(os.listdir(STREAMS_DIR)):
        if not fname.endswith(".json"):
            continue
        aid = int(fname.replace(".json", ""))
        if aid not in ride_ids:
            continue
        try:
            stream = json.load(open(os.path.join(STREAMS_DIR, fname), encoding="utf-8"))
        except Exception:
            continue

        lats = stream.get("latitudes", [])
        lons = stream.get("longitudes", [])
        ts   = stream.get("gps_timestamps_ms", [])
        if not lats or not lons:
            continue

        all_lats.extend(lats)
        all_lons.extend(lons)

        # Häufigkeits-Layer: jede Zelle einmal pro Ride
        visited = set()
        for lat, lon in zip(lats, lons):
            cell = (round(lat, GRID), round(lon, GRID))
            visited.add(cell)
        for cell in visited:
            cell_rides[cell] = cell_rides.get(cell, 0) + 1

        # Geschwindigkeits-Layer: aus aufeinanderfolgenden Punkten berechnen
        if ts and len(ts) == len(lats):
            for i in range(1, len(lats)):
                dt_s = (ts[i] - ts[i - 1]) / 1000.0
                if not (0.5 < dt_s < 30):
                    continue
                dist_m = haversine_m(lats[i-1], lons[i-1], lats[i], lons[i])
                speed_kmh = (dist_m / 1000.0) / (dt_s / 3600.0)
                if not (3.0 < speed_kmh < 80.0):  # Rauschen und Stopps filtern
                    continue
                cell = (round(lats[i], GRID), round(lons[i], GRID))
                if cell not in cell_speeds:
                    cell_speeds[cell] = []
                cell_speeds[cell].append(speed_kmh)

    if not cell_rides:
        print("Keine GPS-Daten für Heatmap.")
        return

    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    # Logarithmische Gewichtung: 1× sehr schwach, Vielfachfahrten heben sich klar ab.
    # log(1+1)/log(1+max) = 0 … log(max+1)/log(max+1) = 1
    import math as _math
    max_count = max(cell_rides.values())
    log_max = _math.log(max_count + 1)
    freq_data = [
        [lat, lon, _math.log(count + 1) / log_max]
        for (lat, lon), count in cell_rides.items()
    ]

    speed_data = []
    if cell_speeds:
        avg_speeds = {cell: sum(v) / len(v) for cell, v in cell_speeds.items()}
        p10 = sorted(avg_speeds.values())[int(len(avg_speeds) * 0.10)]
        p90 = sorted(avg_speeds.values())[int(len(avg_speeds) * 0.90)]
        spread = max(p90 - p10, 1.0)
        for (lat, lon), spd in avg_speeds.items():
            w = max(0.0, min(1.0, (spd - p10) / spread))
            speed_data.append([lat, lon, w])

    # Karte
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=12,
        tiles="CartoDB dark_matter",
        prefer_canvas=True,
    )

    # Layer 1 – Häufigkeit
    freq_group = folium.FeatureGroup(name="Häufigkeit", show=True)
    HeatMap(
        freq_data,
        min_opacity=0.05,   # 1×-Routen kaum sichtbar
        radius=4,           # eng an der GPS-Genauigkeit (~11m Raster)
        blur=2,             # kaum Weichzeichnung → scharfe Linien
        gradient={
            0.0:  "rgba(0,200,80,0.0)",   # 0-Gewicht: unsichtbar
            0.15: "rgba(0,200,80,0.6)",   # 1× gefahren: schwaches Grün
            0.45: "#f1c40f",              # mehrfach: Gelb
            0.75: "#e67e22",              # oft: Orange
            1.0:  "#e74c3c",              # am häufigsten: Rot
        },
    ).add_to(freq_group)
    freq_group.add_to(m)

    # Layer 2 – Geschwindigkeit
    if speed_data:
        speed_group = folium.FeatureGroup(name="Geschwindigkeit", show=False)
        HeatMap(
            speed_data,
            min_opacity=0.05,
            radius=4,
            blur=2,
            gradient={0.0: "#3498db", 0.35: "#2ecc71", 0.65: "#f1c40f", 1.0: "#e74c3c"},
        ).add_to(speed_group)
        speed_group.add_to(m)

        avg_all = sum(s for _, _, s in speed_data) / len(speed_data)
        speed_note = f"Ø ~{p10:.0f}–{p90:.0f} km/h (10.–90. Perzentile)"
    else:
        speed_note = "Keine Geschwindigkeitsdaten"

    folium.LayerControl(collapsed=False, position="topright").add_to(m)

    # Legende
    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:#1c2128;border:1px solid #2d333b;border-radius:10px;
                padding:14px 20px;font-family:sans-serif;font-size:13px;color:#e6e6e6;
                line-height:1.8;">
        <b>Häufigkeit</b><br>
        <span style="color:#2ecc71;">■</span> 1× gefahren &nbsp;
        <span style="color:#e74c3c;">■</span> am häufigsten<br><br>
        <b>Geschwindigkeit</b><br>
        <span style="color:#3498db;">■</span> langsam &nbsp;
        <span style="color:#e74c3c;">■</span> schnell<br>
        <span style="font-size:11px;color:#6e7a8a;">{speed_note}</span>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend_html))

    m.save(HEATMAP_PATH)
    print(f"Heatmap: {max_count} max. Rides/Zelle, "
          f"{len(cell_rides)} Zellen, {len(speed_data)} mit Speed-Daten")

    # Embed-Snippet für formkurve.html: vollständiges HTML als srcdoc-iframe
    import html as _html
    full_html = m.get_root().render()
    return (
        f'<iframe srcdoc="{_html.escape(full_html)}" '
        f'style="width:100%;height:540px;border:none;border-radius:12px;" '
        f'loading="lazy"></iframe>'
    )


def main():
    client = garmin_login()
    activities = sync_activities(client)
    sync_streams(client, activities)

    annotate_tss(activities)
    save_activities(activities)

    history = compute_form_curve(activities)
    print_form_summary(history)

    mmp_data = compute_mmp_curves()
    if mmp_data:
        ftp_est = mmp_data.get("ftp_42") or mmp_data.get("ftp_season")
        print(f"MMP-Kurve: {mmp_data['count_42']} Rides (42d) / "
              f"{mmp_data['count_season']} gesamt – FTP-Schaetzung: {ftp_est} W")

    weather_forecast = fetch_weather_forecast()
    heatmap_embed = generate_heatmap(activities, sport_types={"Ride"})
    hike_heatmap_embed = generate_heatmap(activities, sport_types={"Hike"})

    hike_summary = compute_hike_summary(activities)
    if hike_summary:
        print(f"Wanderungen: {hike_summary['total_hikes']} Touren · "
              f"{hike_summary['total_gain']:,} m Aufstieg · "
              f"{hike_summary['total_dist_km']} km · "
              f"Ø VAM {hike_summary['avg_vam']} m/h")

    plot_formkurve(history, activities, weather_forecast, mmp_data=mmp_data,
                   heatmap_embed=heatmap_embed, hike_heatmap_embed=hike_heatmap_embed,
                   hike_summary=hike_summary)

    upload_file(ACTIVITIES_PATH, DRIVE_FILENAME)
    upload_file(FORMKURVE_PATH, "formkurve.html", mimetype="text/html")
    upload_file(HEATMAP_PATH, "heatmap.html", mimetype="text/html")


if __name__ == "__main__":
    main()

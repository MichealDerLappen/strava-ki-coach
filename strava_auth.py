"""Strava OAuth2 authorization flow + Aktivitaeten-Synchronisation.

Starts a local webserver on port 8000, opens the Strava authorization page
in the browser, receives the redirect with the authorization code, exchanges
it for access/refresh tokens and stores them in the .env file.

Anschliessend werden neue Aktivitaeten von Strava abgerufen, lokal in
'activities.json' gespeichert und nach Google Drive hochgeladen.
"""

import json
import os
import time
import webbrowser
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key

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
FORMKURVE_PATH = os.path.join(BASE_DIR, "formkurve.html")
DRIVE_FILENAME = "activities.json"

# Open-Meteo: Koordinaten fuer die Wettervorhersage (Linz)
WEATHER_LATITUDE = 48.3064
WEATHER_LONGITUDE = 14.2858
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

# Banister-Modell: Grundlagen fuer die Formkurven-Berechnung
FTP = 266  # Functional Threshold Power in Watt
LTHR = 172  # angenommene Laktatschwelle (Herzfrequenz in bpm)
CTL_DAYS = 42  # Zeitkonstante fuer die Fitness (Chronic Training Load)
ATL_DAYS = 7  # Zeitkonstante fuer die Ermuedung (Acute Training Load)

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
REDIRECT_URI = "http://localhost:8000/authorization"
SCOPE = "activity:read_all"
PAGE_SIZE = 200

load_dotenv(ENV_PATH)

# OpenRouteService: API-Key fuer echte Routen- und Hoehenmeter-Berechnung
ORS_API_KEY = os.getenv("ORS_API_KEY", "")

CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit(
        "STRAVA_CLIENT_ID und STRAVA_CLIENT_SECRET muessen in der .env Datei "
        "gesetzt sein (siehe .env.example)."
    )


class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)

        if "error" in query:
            self._respond("Autorisierung abgelehnt. Du kannst dieses Fenster schliessen.")
            self.server.auth_code = None
            self.server.error = query["error"][0]
            return

        code = query.get("code", [None])[0]
        if code is None:
            self._respond("Kein Code erhalten.")
            return

        self.server.auth_code = code
        self._respond("Autorisierung erfolgreich! Du kannst dieses Fenster schliessen.")

    def _respond(self, message):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body><h1>{message}</h1></body></html>".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def get_authorization_code():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
    }
    auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    server = HTTPServer(("localhost", 8000), AuthHandler)
    server.auth_code = None
    server.error = None

    print(f"Oeffne Browser zur Autorisierung:\n{auth_url}")
    webbrowser.open(auth_url)

    print("Warte auf Autorisierung (http://localhost:8000) ...")
    while server.auth_code is None and server.error is None:
        server.handle_request()

    server.server_close()

    if server.error:
        raise SystemExit(f"Autorisierung fehlgeschlagen: {server.error}")

    return server.auth_code


def exchange_token(code):
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    return response.json()


def refresh_token(refresh_token_value):
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token_value,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    return response.json()


def save_tokens(token_data):
    set_key(ENV_PATH, "STRAVA_ACCESS_TOKEN", token_data["access_token"])
    set_key(ENV_PATH, "STRAVA_REFRESH_TOKEN", token_data["refresh_token"])
    set_key(ENV_PATH, "STRAVA_EXPIRES_AT", str(token_data["expires_at"]))
    os.environ["STRAVA_ACCESS_TOKEN"] = token_data["access_token"]
    os.environ["STRAVA_REFRESH_TOKEN"] = token_data["refresh_token"]
    os.environ["STRAVA_EXPIRES_AT"] = str(token_data["expires_at"])


def ensure_access_token():
    """Gibt einen gueltigen Access-Token zurueck und fuehrt bei Bedarf den
    Autorisierungs- bzw. Refresh-Flow aus."""

    access_token = os.environ.get("STRAVA_ACCESS_TOKEN")
    refresh_token_value = os.environ.get("STRAVA_REFRESH_TOKEN")
    expires_at = int(os.environ.get("STRAVA_EXPIRES_AT") or 0)

    if not access_token or not refresh_token_value:
        code = get_authorization_code()
        token_data = exchange_token(code)
        save_tokens(token_data)
        print(f"Tokens erfolgreich in {ENV_PATH} gespeichert.")
        return token_data["access_token"]

    if expires_at <= int(time.time()):
        print("Access-Token ist abgelaufen, erneuere ihn ...")
        token_data = refresh_token(refresh_token_value)
        save_tokens(token_data)
        return token_data["access_token"]

    return access_token


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


def fetch_activities(access_token, after=None, per_page=PAGE_SIZE):
    """Ruft Aktivitaeten ab (neueste zuerst) und paginiert dabei so lange,
    bis eine Abfrage eine leere Liste zurueckgibt."""

    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    page = 1

    while True:
        params = {"per_page": per_page, "page": page}
        if after is not None:
            params["after"] = int(after.timestamp())

        response = requests.get(ACTIVITIES_URL, headers=headers, params=params)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        activities.extend(batch)
        page += 1

    return activities


def sync_activities(access_token):
    """Laedt neue Aktivitaeten herunter und haengt sie an 'activities.json' an."""

    existing = load_existing_activities()
    last_date = latest_start_date(existing)

    if last_date is None:
        print(f"Keine vorhandene {ACTIVITIES_PATH} gefunden, lade die komplette "
              f"Strava-Historie ...")
        new_activities = fetch_activities(access_token, after=None)
    else:
        print(f"Lade Aktivitaeten neuer als {last_date.isoformat()} ...")
        new_activities = fetch_activities(access_token, after=last_date)

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


def compute_tss(activity):
    """Berechnet bzw. schaetzt den Trainingsstress-Score (TSS) einer Aktivitaet."""

    moving_time = activity.get("moving_time", 0)

    if activity.get("type") == "Ride":
        normalized_power = activity.get("weighted_average_watts")
        if normalized_power:
            intensity_factor = normalized_power / FTP
            tss = (moving_time * normalized_power * intensity_factor) / (FTP * 3600) * 100
            return round(tss, 1)

    # Aktivitaeten ohne Leistungsdaten (Hike, WeightTraining, Run, ...):
    # bevorzugt Stravas Suffer Score, sonst grobe Schaetzung ueber die
    # durchschnittliche Herzfrequenz im Verhaeltnis zur Laktatschwelle.
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


def plot_formkurve(history, activities, weather_forecast=None):
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
            .replace("__ORS_API_KEY__", ORS_API_KEY)
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
            <div class="day-tss">TSS: <span id="tssValue{day}">0.0</span></div>{warnings_html}
            <details class="route-planner" data-day="{day}">
                <summary>🗺️ Routen-Planer</summary>
                <div class="map" id="map{day}"></div>
                <div class="route-info">
                    <div class="route-target" id="routeTargetValue{day}"></div>
                    <div class="route-distance">Geschaetzte Distanz: <span id="distanceValue{day}">0.0 km</span></div>
                    <div class="route-waypoints">Moegliche Wendepunkte: <span id="waypointsValue{day}">-</span></div>
                </div>
                <button class="route-btn" id="routeBtn{day}" onclick="loadRealRoute({day})">🗺️ Echte Route laden</button>
                <div class="route-elevation">Echte Hoehenmeter: <span id="elevationValue{day}">-</span></div>
            </details>
        </div>""")
    day_cards_html = "\n".join(day_cards)
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="formkurve-chart")

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Formkurve</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
{dashboard_css}
</style>
</head>
<body>
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
</body>
</html>
"""

    with open(FORMKURVE_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Formkurve gespeichert unter {FORMKURVE_PATH}")


def main():
    access_token = ensure_access_token()
    activities = sync_activities(access_token)

    annotate_tss(activities)
    save_activities(activities)

    history = compute_form_curve(activities)
    print_form_summary(history)

    weather_forecast = fetch_weather_forecast()
    plot_formkurve(history, activities, weather_forecast)

    upload_file(ACTIVITIES_PATH, DRIVE_FILENAME)
    upload_file(FORMKURVE_PATH, "formkurve.html", mimetype="text/html")


if __name__ == "__main__":
    main()

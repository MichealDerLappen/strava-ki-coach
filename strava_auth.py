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

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ACTIVITIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activities.json")
FORMKURVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formkurve.html")
DRIVE_FILENAME = "activities.json"

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
        "form_status": form_status,
        "trend_status": trend_status,
    }


def plot_formkurve(history):
    """Erstellt ein interaktives Dashboard der Formkurve (CTL, ATL, TSB)
    inkl. Coach-Analyse als 'formkurve.html'."""

    if not history:
        return

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
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Formkurve</title>
<style>
    body {{
        background-color: #111418;
        color: #e6e6e6;
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        margin: 0;
        padding: 0 24px 48px;
    }}
    h2 {{
        font-weight: 600;
        margin-top: 40px;
    }}
    hr {{
        border: none;
        border-top: 1px solid #2d333b;
        margin: 32px 0;
    }}
    .metrics {{
        display: flex;
        gap: 20px;
        flex-wrap: wrap;
        margin-bottom: 24px;
    }}
    .metric-box {{
        background-color: #1c2128;
        border: 1px solid #2d333b;
        border-radius: 12px;
        padding: 20px 32px;
        text-align: center;
        min-width: 150px;
        position: relative;
        cursor: help;
    }}
    .metric-box .label {{
        font-size: 14px;
        color: #9aa4af;
        margin-bottom: 8px;
    }}
    .metric-box .value {{
        font-size: 32px;
        font-weight: 700;
    }}
    .tooltip-text {{
        visibility: hidden;
        opacity: 0;
        position: absolute;
        bottom: 105%;
        left: 50%;
        transform: translateX(-50%);
        width: 260px;
        background-color: #222222;
        color: #f5f5f5;
        border: 1px solid #444444;
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 13px;
        font-weight: 400;
        line-height: 1.5;
        text-align: left;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
        transition: opacity 0.2s ease-in-out;
        pointer-events: none;
        z-index: 20;
    }}
    .metric-box:hover .tooltip-text {{
        visibility: visible;
        opacity: 1;
    }}
    .ctl {{ color: #2ecc71; }}
    .atl {{ color: #e74c3c; }}
    .tsb {{ color: #f1c40f; }}
    .status-text {{
        font-size: 18px;
        line-height: 1.6;
        margin: 12px 0;
    }}
    .slider-row {{
        display: flex;
        align-items: center;
        gap: 16px;
        margin: 18px 0;
    }}
    .slider-row label {{
        min-width: 260px;
        color: #9aa4af;
        font-size: 15px;
    }}
    .slider-row input[type="range"] {{
        flex: 1;
        accent-color: #3498db;
    }}
    .slider-value {{
        min-width: 140px;
        text-align: right;
        font-weight: 600;
        font-size: 15px;
        color: #e6e6e6;
    }}
    .sim-results {{
        display: flex;
        gap: 20px;
        flex-wrap: wrap;
        margin-top: 24px;
    }}
    .sim-box {{
        background-color: #1c2128;
        border: 1px solid #2d333b;
        border-radius: 12px;
        padding: 20px 32px;
        text-align: center;
        flex: 1;
        min-width: 200px;
    }}
    .sim-box .label {{
        font-size: 14px;
        color: #9aa4af;
        margin-bottom: 8px;
    }}
    .sim-box .value {{
        font-size: 24px;
        font-weight: 700;
        color: #3498db;
    }}
    .slider-row select {{
        background-color: #1c2128;
        color: #e6e6e6;
        border: 1px solid #2d333b;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 14px;
    }}
    .highlight-box {{
        background-color: #1c2128;
        border: 2px solid #f1c40f;
        border-radius: 12px;
        padding: 20px 28px;
        margin-bottom: 24px;
        text-align: center;
        font-size: 20px;
        font-weight: 600;
    }}
    .highlight-box.fresh {{
        border-color: #2ecc71;
        font-size: 22px;
    }}
</style>
</head>
<body>
    <div class="highlight-box" id="optimalWindowBox">Berechne optimales Trainingsfenster ...</div>
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
    </div>
    <p class="status-text">{analysis['form_status']}</p>
    <p class="status-text">{analysis['trend_status']}</p>

    <hr>
    <h2>Zukunfts-Simulator: Was-waere-wenn?</h2>
    <p class="status-text">
        Stelle eine geplante Einheit ein und waehle aus, an welchem Tag sie
        stattfinden soll, um zu sehen, wie sich deine Form (TSB) in den
        naechsten 7 Tagen entwickelt.
    </p>

    <div class="slider-row">
        <label for="durationSlider">Geplante Dauer (Minuten)</label>
        <input type="range" id="durationSlider" min="0" max="360" step="5" value="90">
        <div class="slider-value" id="durationValue">90 min</div>
    </div>
    <div class="slider-row">
        <label for="powerSlider">Geplante Leistung (Durchschnitts-Watt)</label>
        <input type="range" id="powerSlider" min="100" max="400" step="5" value="180">
        <div class="slider-value" id="powerValue">180 W</div>
    </div>
    <div class="slider-row">
        <label for="dayOffsetSelect">Geplanter Trainingstag</label>
        <select id="dayOffsetSelect">
            <option value="0">Heute</option>
            <option value="1" selected>Morgen</option>
            <option value="2">In 2 Tagen</option>
            <option value="3">In 3 Tagen</option>
            <option value="4">In 4 Tagen</option>
            <option value="5">In 5 Tagen</option>
            <option value="6">In 6 Tagen</option>
            <option value="7">In 7 Tagen</option>
        </select>
    </div>

    <div class="sim-results">
        <div class="sim-box">
            <div class="label">Simulierter TSS</div>
            <div class="value" id="simTss">-</div>
        </div>
        <div class="sim-box">
            <div class="label">Tiefpunkt TSB (naechste 7 Tage)</div>
            <div class="value" id="simLow">-</div>
        </div>
        <div class="sim-box">
            <div class="label">Erholung (TSB wieder positiv)</div>
            <div class="value" id="simRecovery">-</div>
        </div>
    </div>

    <script>
        const FTP = {FTP};
        const CTL_TODAY = {analysis['ctl']};
        const ATL_TODAY = {analysis['atl']};
        const CTL_DECAY = Math.exp(-1 / 42);
        const ATL_DECAY = Math.exp(-1 / 7);

        const durationSlider = document.getElementById("durationSlider");
        const powerSlider = document.getElementById("powerSlider");
        const dayOffsetSelect = document.getElementById("dayOffsetSelect");
        const durationValue = document.getElementById("durationValue");
        const powerValue = document.getElementById("powerValue");
        const simTss = document.getElementById("simTss");
        const simLow = document.getElementById("simLow");
        const simRecovery = document.getElementById("simRecovery");
        const optimalWindowBox = document.getElementById("optimalWindowBox");

        const WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];

        function formatDate(daysFromToday) {{
            const d = new Date();
            d.setDate(d.getDate() + daysFromToday);
            return WEEKDAYS[d.getDay()] + ", " + d.toLocaleDateString("de-DE");
        }}

        function simulate() {{
            const minutes = parseInt(durationSlider.value, 10);
            const watts = parseInt(powerSlider.value, 10);
            const dayOffset = parseInt(dayOffsetSelect.value, 10);

            durationValue.textContent = minutes + " min";
            powerValue.textContent = watts + " W";

            const intensityFactor = watts / FTP;
            const durationSeconds = minutes * 60;
            const simulatedTss = (durationSeconds * watts * intensityFactor) / (FTP * 3600) * 100;
            simTss.textContent = simulatedTss.toFixed(1);

            // Die Belastung des gewaehlten Tages wirkt sich auf den Folgetag aus.
            const impactDay = dayOffset + 1;

            let ctl = CTL_TODAY;
            let atl = ATL_TODAY;

            let lowestTsb = Infinity;
            let lowestDay = null;
            let recoveryDay = null;

            for (let day = 1; day <= 7; day++) {{
                const dayTss = (day === impactDay) ? simulatedTss : 0;
                ctl = ctl * CTL_DECAY + dayTss * (1 - CTL_DECAY);
                atl = atl * ATL_DECAY + dayTss * (1 - ATL_DECAY);
                const tsb = ctl - atl;

                if (tsb < lowestTsb) {{
                    lowestTsb = tsb;
                    lowestDay = day;
                }}
                if (recoveryDay === null && day >= impactDay && tsb > 0) {{
                    recoveryDay = day;
                }}
            }}

            simLow.textContent = lowestTsb.toFixed(1) + " (" + formatDate(lowestDay) + ")";
            simRecovery.textContent = recoveryDay !== null
                ? formatDate(recoveryDay)
                : "nicht innerhalb von 7 Tagen";
        }}

        function computeOptimalWindow() {{
            const tsbToday = CTL_TODAY - ATL_TODAY;

            if (tsbToday >= 0) {{
                optimalWindowBox.innerHTML = "<strong>🔥 Du bist absolut frisch! Zeit fuer das naechste Training!! 🔥</strong>";
                optimalWindowBox.classList.add("fresh");
                return;
            }}

            let ctl = CTL_TODAY;
            let atl = ATL_TODAY;

            for (let day = 1; day <= 14; day++) {{
                ctl = ctl * CTL_DECAY;
                atl = atl * ATL_DECAY;
                const tsb = ctl - atl;
                if (tsb >= 0) {{
                    optimalWindowBox.innerHTML = "Optimales Zeitfenster fuer die naechste harte Einheit: <strong>" + formatDate(day) + "</strong>";
                    return;
                }}
            }}

            optimalWindowBox.innerHTML = "Optimales Zeitfenster fuer die naechste harte Einheit liegt mehr als 14 Tage in der Zukunft.";
        }}

        durationSlider.addEventListener("input", simulate);
        powerSlider.addEventListener("input", simulate);
        dayOffsetSelect.addEventListener("change", simulate);
        simulate();
        computeOptimalWindow();
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
    plot_formkurve(history)

    upload_file(ACTIVITIES_PATH, DRIVE_FILENAME)
    upload_file(FORMKURVE_PATH, "formkurve.html", mimetype="text/html")


if __name__ == "__main__":
    main()

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


def plot_formkurve(history):
    """Erstellt ein interaktives Dashboard der Formkurve (CTL, ATL, TSB)
    als 'formkurve.html'."""

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
    )

    fig.write_html(FORMKURVE_PATH)
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


if __name__ == "__main__":
    main()

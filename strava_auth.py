"""Strava OAuth2 authorization flow.

Starts a local webserver on port 8000, opens the Strava authorization page
in the browser, receives the redirect with the authorization code, exchanges
it for access/refresh tokens and stores them in the .env file.
"""

import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
REDIRECT_URI = "http://localhost:8000/authorization"
SCOPE = "activity:read_all"

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


def save_tokens(token_data):
    set_key(ENV_PATH, "STRAVA_ACCESS_TOKEN", token_data["access_token"])
    set_key(ENV_PATH, "STRAVA_REFRESH_TOKEN", token_data["refresh_token"])
    set_key(ENV_PATH, "STRAVA_EXPIRES_AT", str(token_data["expires_at"]))


def main():
    code = get_authorization_code()
    token_data = exchange_token(code)
    save_tokens(token_data)
    print(f"Tokens erfolgreich in {ENV_PATH} gespeichert.")


if __name__ == "__main__":
    main()

"""Einmaliges Skript zum Download der KOMPLETTEN Strava-Aktivitaetshistorie.

Nutzt die bestehenden Tokens aus strava_auth.py, ruft alle jemals
aufgezeichneten Aktivitaeten seitenweise (rueckwaerts in der Zeit) ab,
speichert sie in 'activities.json' und laedt die Datei nach Google Drive
hoch (vorhandene Datei wird ueberschrieben).
"""

import json
import os
import time

import requests
from dotenv import load_dotenv

from strava_auth import (
    ACTIVITIES_URL,
    ACTIVITIES_PATH,
    DRIVE_FILENAME,
    PAGE_SIZE,
    ensure_access_token,
    save_activities,
)
from google_drive import upload_file

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

REQUEST_DELAY_SECONDS = 1


def fetch_all_activities(access_token):
    """Ruft alle Aktivitaeten seitenweise ab (neueste zuerst), bis eine
    Abfrage eine leere Liste zurueckgibt."""

    headers = {"Authorization": f"Bearer {access_token}"}
    all_activities = []
    page = 1

    while True:
        params = {"per_page": PAGE_SIZE, "page": page}
        print(f"Lade Seite {page} ...")

        response = requests.get(ACTIVITIES_URL, headers=headers, params=params)
        response.raise_for_status()
        batch = response.json()

        if not batch:
            print("Keine weiteren Aktivitaeten gefunden. Fertig.")
            break

        all_activities.extend(batch)
        print(f"  {len(batch)} Aktivitaet(en) erhalten (insgesamt {len(all_activities)}).")

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return all_activities


def main():
    access_token = ensure_access_token()

    print("Starte Download der kompletten Strava-Historie ...")
    activities = fetch_all_activities(access_token)

    activities.sort(key=lambda a: a["start_date"], reverse=True)

    save_activities(activities)
    print(f"{len(activities)} Aktivitaet(en) in {ACTIVITIES_PATH} gespeichert.")

    upload_file(ACTIVITIES_PATH, DRIVE_FILENAME)


if __name__ == "__main__":
    main()

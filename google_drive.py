"""Hilfsfunktionen zum Hoch- bzw. Aktualisieren einer Datei in Google Drive."""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "drive_token.json")

# drive.file: Zugriff nur auf Dateien, die diese App selbst erstellt/oeffnet.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise SystemExit(
                    f"Google-OAuth-Zertifikat nicht gefunden: {CREDENTIALS_PATH}\n"
                    "Bitte im Google Cloud Dashboard ein OAuth-Client-ID-Zertifikat "
                    "vom Typ 'Desktop-App' erstellen, als 'credentials.json' "
                    "herunterladen und in diesem Projektordner ablegen."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_file(local_path, drive_filename):
    """Laedt `local_path` nach Google Drive hoch und ueberschreibt eine
    bereits vorhandene Datei mit dem Namen `drive_filename`, falls sie existiert."""

    service = get_drive_service()

    escaped_name = drive_filename.replace("'", "\\'")
    results = (
        service.files()
        .list(
            q=f"name = '{escaped_name}' and trashed = false",
            spaces="drive",
            fields="files(id, name)",
        )
        .execute()
    )
    files = results.get("files", [])

    media = MediaFileUpload(local_path, mimetype="application/json")

    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"Datei '{drive_filename}' in Google Drive aktualisiert (ID {file_id}).")
    else:
        file_metadata = {"name": drive_filename}
        created = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        print(f"Datei '{drive_filename}' neu in Google Drive hochgeladen (ID {created['id']}).")

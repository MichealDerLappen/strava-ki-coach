# strava-ki-coach

Tools rund um die Strava-API als Basis für einen KI-gestützten Trainingscoach.

## Setup

1. Virtuelle Umgebung erstellen und Abhängigkeiten installieren:

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. `.env.example` nach `.env` kopieren und die eigene Strava-App-Client-ID und
   das Client-Secret eintragen (siehe https://www.strava.com/settings/api):

   ```bash
   cp .env.example .env
   ```

## Autorisierung & Synchronisation

```bash
python strava_auth.py
```

Beim ersten Aufruf öffnet das Skript die Strava-Autorisierungsseite im
Browser, empfängt den Redirect lokal auf Port 8000 und speichert
Access-Token, Refresh-Token und Ablaufzeitpunkt in `.env`. Bei weiteren
Aufrufen wird der Access-Token bei Bedarf automatisch erneuert.

Anschließend lädt das Skript neue Aktivitäten von Strava herunter:

- Existiert noch keine `activities.json`, werden die letzten 10 Aktivitäten
  geladen.
- Existiert die Datei bereits, werden nur Aktivitäten geladen, die neuer als
  die zuletzt gespeicherte (`start_date`) sind, und an die Liste angehängt.

## Google Drive Upload

Nach der Synchronisation wird `activities.json` automatisch nach Google
Drive hochgeladen (eine dort bereits vorhandene Datei mit demselben Namen
wird überschrieben).

Voraussetzung dafür:

1. Im [Google Cloud Dashboard](https://console.cloud.google.com/) ein
   Projekt anlegen (oder ein bestehendes verwenden) und die **Google Drive
   API** aktivieren.
2. Unter "APIs & Services" → "Credentials" ein **OAuth-Client-ID-Zertifikat
   vom Typ "Desktop-App"** erstellen und als `credentials.json`
   herunterladen.
3. `credentials.json` in diesem Projektordner ablegen (die Datei ist in
   `.gitignore` und wird nicht committet).

Beim ersten Upload öffnet sich ein Browserfenster zur Google-Autorisierung;
das resultierende Token wird in `drive_token.json` gespeichert und bei
weiteren Läufen automatisch wiederverwendet bzw. erneuert.

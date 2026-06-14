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

## Autorisierung

```bash
python strava_auth.py
```

Das Skript öffnet die Strava-Autorisierungsseite im Browser, empfängt den
Redirect lokal auf Port 8000 und speichert Access-Token, Refresh-Token und
Ablaufzeitpunkt in `.env`.

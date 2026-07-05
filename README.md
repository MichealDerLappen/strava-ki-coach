# strava-ki-coach

KI-gestützter Trainingscoach auf Basis von Garmin Connect-Daten.

> **Hinweis:** Das Projekt wurde ursprünglich mit der Strava-API entwickelt
> (OAuth2-Flow, lokaler Redirect-Server, Strava-Aktivitäts-Endpunkte). Seit
> Juli 2026 werden die Trainingsdaten direkt von Garmin Connect bezogen –
> die gesamte Dashboard- und Formkurven-Logik blieb dabei unverändert.

## Setup

1. Virtuelle Umgebung erstellen und Abhängigkeiten installieren:

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. `.env.example` nach `.env` kopieren und Garmin-Zugangsdaten eintragen:

   ```bash
   cp .env.example .env
   ```

   Dann in `.env` ausfüllen:

   ```
   GARMIN_EMAIL=deine_garmin_email@example.com
   GARMIN_PASSWORD=dein_garmin_passwort
   ```

3. Optional für den Routen-Planer: einen kostenlosen API-Key bei
   [OpenRouteService](https://openrouteservice.org/dev/#/signup) erstellen
   und als `ORS_API_KEY` in `.env` eintragen.

## Synchronisation & Dashboard generieren

```bash
python strava_auth.py
```

Das Skript meldet sich bei Garmin Connect an (beim ersten Aufruf mit
Benutzername/Passwort, danach aus dem gecachten Session-Token unter `.garth/`),
lädt neue Aktivitäten herunter und speichert sie in `activities.json`.

- Existiert noch keine `activities.json`, werden die letzten 100 Aktivitäten
  geladen.
- Existiert die Datei bereits, werden nur Aktivitäten seit dem letzten
  gespeicherten Datum abgerufen und angehängt.

Anschließend wird die Formkurve berechnet und `formkurve.html` generiert.

## Trainingsanalyse & Formkurve (Banister-Modell)

Bei jedem Lauf wird eine Trainings-Formkurve nach dem Banister-Modell berechnet:

- **TSS pro Aktivität**: Für Radfahrten aus Normalized Power und FTP (266 W).
  Für Aktivitäten ohne Leistungsdaten aus Herzfrequenz vs. Laktatschwelle
  (172 bpm). Für ältere Strava-Exporte aus dem Suffer Score.
- **CTL (Fitness)**: exponentiell gewichteter gleitender Durchschnitt über 42 Tage.
- **ATL (Ermüdung)**: exponentiell gewichteter gleitender Durchschnitt über 7 Tage.
- **TSB (Form)**: `CTL - ATL`.
- **ACWR**: `ATL / CTL` – Verhältnis akuter zu chronischer Belastung.

### Interaktives Dashboard `formkurve.html`

CSS und JavaScript liegen als eigene Dateien unter `templates/dashboard.css`
und `templates/dashboard.js` und werden beim Generieren mit den aktuellen
Werten befüllt und in `formkurve.html` eingebettet (einzelne, eigenständige
Datei für Google Drive). Enthalten sind:

- **Optimales Trainingsfenster**: Box ganz oben – berechnet, wann TSB wieder
  ≥ 0 ist. Reagiert live auf die Wochenplanung.
- **Coach-Empfehlung mit ACWR-Überlastungsschutz**: Empfehlungs-Box mit
  erweiterter Logik. ACWR > 1,5 → rote "⚠️ Überlastungs-Warnung"; ACWR
  0,8–1,3 bei ausgeglichenem TSB → "🎯 Perfekter Trainingsreiz". Sonst
  TSB- und wochentagsbasierte Empfehlung.
- **Formkurven-Chart**: Liniendiagramm (700 px) mit CTL, ATL, TSB und
  Aktivitäts-Zeitleiste. `hovermode="x unified"`, Range Slider, Zoom-Buttons.
- **Coach-Analyse**: Metric-Boxes für CTL, ATL, TSB und ACWR (lila, mit
  Tooltip). Statustext (Frische-/Aufbau-/Ermüdungszone) und Fitness-Trend.
- **Wochenplanung (Zukunfts-Simulator)**: 7 Tages-Karten mit Dauer-/Leistungs-
  Slidern. Live-Neuberechnung von TSS/CTL/ATL/TSB als gestrichelte Prognose
  im Chart.
- **Wetter-Vorhersage (Open-Meteo)**: Höchsttemperatur, Regenwahrschein-
  lichkeit und Wind für jeden Plantag (kein API-Key nötig). Regen > 60 % →
  bläuliche Karte + "🌧️ Regenjacke einpacken!"; Wind > 25 km/h →
  "💨 Starker Wind!".
- **🌦️ Wetter-Coach-Orakel**: Box mit den 3 besten Outdoor-Trainingstagen
  der Woche (gewichtet nach Temp/Regen/Wind) und motivierendem TSB-Tipp.
- **Wochentags-Filter**: Mo–So-Buttons zum Ein-/Ausblenden einzelner
  Tages-Karten (ausgeblendete Werte fließen weiterhin in die Simulation ein).
- **Routen-Planer**: Einklappbares Accordion pro Tages-Karte. Leaflet-Karte
  (Dark-Mode-Tiles, lazy initialisiert), Live-Radius-Kreis aus Dauer/Leistung,
  Wendepunkt-Vorschläge aus dem Linzer Umland. Optional: echte Strecke +
  Höhenmeter via OpenRouteService ("🗺️ Echte Route laden"), automatische
  Neu-Berechnung bei Slider-Änderung.

## Google Drive Upload

Nach der Synchronisation werden `activities.json` und `formkurve.html`
automatisch nach Google Drive hochgeladen.

Voraussetzung:

1. Im [Google Cloud Dashboard](https://console.cloud.google.com/) ein Projekt
   anlegen und die **Google Drive API** aktivieren.
2. Unter "APIs & Services" → "Credentials" ein **OAuth-Client-ID-Zertifikat
   vom Typ "Desktop-App"** erstellen und als `credentials.json` herunterladen.
3. `credentials.json` in diesem Projektordner ablegen (gitignored).

Beim ersten Upload öffnet sich ein Browserfenster zur Google-Autorisierung;
das Token wird in `drive_token.json` gespeichert und danach automatisch
wiederverwendet.

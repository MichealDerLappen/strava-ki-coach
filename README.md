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

- **TSS pro Aktivität**: Für Radfahrten aus Normalized Power und FTP (250 W).
  Für Aktivitäten ohne Leistungsdaten aus Herzfrequenz vs. Laktatschwelle
  (172 bpm). Für ältere Strava-Exporte aus dem Suffer Score.
- **CTL (Fitness)**: exponentiell gewichteter gleitender Durchschnitt über 42 Tage.
- **ATL (Ermüdung)**: exponentiell gewichteter gleitender Durchschnitt über 7 Tage.
- **TSB (Form)**: `CTL - ATL`.
- **ACWR**: `ATL / CTL` – Verhältnis akuter zu chronischer Belastung.

### FTP-Anzeige

Ganz oben im Dashboard zwei Werte nebeneinander:

- **FTP konfiguriert** (blau): die in `strava_auth.py` gesetzte Konstante `FTP`,
  Basis für alle TSS-Berechnungen.
- **FTP-Schätzung MMP** (gelb, wenn niedriger): 95 % des 20-min-Bestwerts aus
  der Power-Duration-Kurve. Zeigt, ob der konfigurierte Wert realistisch ist.

Um den FTP anzupassen, genügt eine Änderung von `FTP = 266` in `strava_auth.py`
– beim nächsten Lauf werden alle TSS-Werte neu berechnet.

### Power-Duration-Kurve (Mean Maximal Power)

Für Rides mit Leistungsmesser wird eine MMP-Kurve berechnet und im Dashboard
angezeigt. Sie zeigt die beste mittlere Leistung (W) über alle gängigen Dauer-
stufen (5 s bis 60 min) auf einer logarithmischen Zeitachse:

- **Blaue Linie**: Bestwerte der letzten 42 Tage.
- **Gepunktete graue Linie**: Saisonbestwert (alle gecachten Rides).
- **FTP-Annotation**: 95 % des 20-min-Bestwerts als automatische FTP-Schätzung.

Die Hochauflösungs-Daten (Garmin-Detail-API, ~1 Hz) werden pro Aktivität
in `streams/<id>.json` gecacht. Jede Datei enthält:

- `watts` + `timestamps_ms` – Leistung (Rides mit Powermeter)
- `heartrates` + `hr_timestamps_ms` – Herzfrequenz
- `latitudes`, `longitudes`, `gps_timestamps_ms` – GPS-Track
- `elevations` – Höhenprofil

Strava-importierte Aktivitäten in Garmin Connect sind über die API nicht
zugänglich – nur native Garmin-Aktivitäten liefern Streams.

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
- **Heatmap aller Rides**: Interaktive Leaflet-Karte ganz unten im Dashboard
  (Dark-Theme, verschiebbar, zoombar). Layer-Umschalter oben rechts:
  – *Häufigkeit*: grün (1× gefahren) → rot (am häufigsten)
  – *Geschwindigkeit*: blau (langsam) → rot (schnell), berechnet aus GPS+Zeit
  Beide Layer gleichzeitig einschaltbar. Auch als eigenständige `heatmap.html`
  auf Google Drive verfügbar.
- **Intensitätsverteilung (Polarisierung)**: Sekundengenaue Zonenverteilung
  (Z1 locker / Z2 mittel / Z3 hart) auf Basis der HR-Zeitreihe aus den Stream-
  Caches. Zwei Ansichten: Gesamtverteilung (horizontaler Stacked-Bar) und
  Wochenweise (gestapelte Balken je Kalenderwoche). Umschalter oben links im
  Chart. Aktivitäten ohne HR-Stream fallen auf Durchschnitts-HF zurück.
  Schwellen: LT1 = 82 % HFmax, LT2 = 92 % HFmax (in `strava_auth.py` editierbar).
- **Trainingstyp-Empfehlung**: Jede Tages-Karte zeigt einen farbigen Badge
  mit dem empfohlenen Einheittyp (Ruhetag / Grundlage / Sweet Spot / Tempo /
  Intervalle / Long Ride). Berechnet aus der TSB-Prognose für diesen Tag,
  dem Wochentag und der Wettervorhersage (Regen → Indoor). Aktualisiert sich
  live wenn die Slider bewegt werden.

## Automatischer täglicher Run (macOS LaunchAgent)

Das Script läuft täglich um 06:00 Uhr automatisch via macOS LaunchAgent.
Die Plist-Datei liegt unter:

```
~/Library/LaunchAgents/com.strava-ki-coach.daily.plist
```

**Einmalige Einrichtung** (nach Clone auf neuem Mac):

```bash
launchctl load ~/Library/LaunchAgents/com.strava-ki-coach.daily.plist
```

**Log** (Ausgabe des letzten Runs):

```bash
tail -f ~/strava-ki-coach/launchagent.log
```

**Deaktivieren:**

```bash
launchctl unload ~/Library/LaunchAgents/com.strava-ki-coach.daily.plist
```

Wenn der Mac um 06:00 schläft, holt macOS den Run nach dem Aufwachen nach.

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

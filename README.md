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

## Komplette Historie herunterladen

```bash
python fetch_history.py
```

Einmaliges Skript, das die Strava-Tokens aus `strava_auth.py` wiederverwendet
und die **komplette** Aktivitätshistorie zeitlich rückwärts abruft (seitenweise
mit `per_page=200`, 1 Sekunde Pause zwischen den Anfragen, um das Strava
Rate-Limit nicht zu verletzen). Das Ergebnis wird in `activities.json`
gespeichert und nach Google Drive hochgeladen.

## Trainingsanalyse & Formkurve (Banister-Modell)

Bei jedem Lauf von `strava_auth.py` wird zusätzlich eine
Trainings-Formkurve nach dem Banister-Modell berechnet:

- **TSS pro Aktivität**: Für Radfahrten aus `weighted_average_watts`
  (Normalized Power) und der eingestellten FTP (266 W) nach der Formel
  `TSS = (Dauer_s * NP * IF) / (FTP * 3600) * 100` mit `IF = NP / FTP`.
  Für Aktivitäten ohne Leistungsdaten (Laufen, Wandern, Krafttraining, ...)
  wird der TSS aus Stravas `suffer_score` (`* 0.6`) oder, falls nicht
  vorhanden, grob aus der durchschnittlichen Herzfrequenz im Verhältnis zur
  angenommenen Laktatschwelle (172 bpm) geschätzt. Der Wert wird je
  Aktivität als `tss`-Feld in `activities.json` gespeichert.
- **CTL (Fitness)**: exponentiell gewichteter gleitender Durchschnitt des
  täglichen TSS über 42 Tage.
- **ATL (Ermüdung)**: exponentiell gewichteter gleitender Durchschnitt des
  täglichen TSS über 7 Tage.
- **TSB (Form)**: `CTL - ATL`, täglich fortlaufend von der ersten
  Aktivität bis heute berechnet.

Die heutigen Werte werden farbig im Terminal ausgegeben, z. B.:

```
Formkurve (2026-06-14): Fitness (CTL): 25.0 | Ermuedung (ATL): 28.6 | Form (TSB): -7.8
```

### Interaktives Dashboard `formkurve.html`

Aus der Formkurve wird ein interaktives Dark-Mode-Dashboard als
`formkurve.html` generiert (Plotly, bei Bedarf automatisch via `pip`
installiert) und nach Google Drive synchronisiert. Enthalten sind:

- **Optimales Trainingsfenster**: hervorgehobene Box ganz oben, die anhand
  der heutigen CTL/ATL-Werte berechnet, an welchem der nächsten 14 Tage der
  TSB wieder ≥ 0 ist (bzw. die Sondermeldung "🔥 Du bist absolut frisch!",
  falls das bereits heute der Fall ist).
- **Formkurven-Chart**: Liniendiagramm mit CTL (grün), ATL (rot) und TSB
  (gelb, mit grün/rot gefüllter Fläche zur Nulllinie für positive/negative
  Form), `hovermode="x unified"` für eine kombinierte Hover-Anzeige.
- **Zeitleiste der Aktivitäten**: feine Striche am unteren Rand des Charts,
  ein Hover zeigt Name, Sportart und Distanz (km) der jeweiligen Aktivität.
- **Zoom & Navigation**: Range Slider unter dem Chart sowie
  Schnellauswahl-Buttons ("1M", "3M", "YTD", "Alles").
- **Coach-Analyse**: große Boxen mit den heutigen CTL-/ATL-/TSB-Werten,
  automatisch generierte Status- (Frische-/Aufbau-/Ermüdungszone) und
  Trendtexte (Fitness steigend/sinkend), sowie Hover-Tooltips mit
  sportwissenschaftlichen Erklärungen zu CTL, ATL und TSB.
- **Wochenplanung (Zukunfts-Simulator)**: Für jeden der nächsten 7 Tage gibt
  es eine eigene Karte mit zwei Slidern für geplante Dauer (0–360 min,
  Schrittweite 5) und Leistung (100–400 W, Schrittweite 5). Bei jeder
  Änderung wird der komplette 7-Tage-Verlauf neu berechnet (TSS je Tag aus
  Dauer/Leistung, anschließend fortlaufende Anwendung der CTL-/ATL-Zerfalls-
  formeln), live als gestrichelte Prognose-Linien ins Chart eingeblendet und
  der Tiefpunkt des TSB in der geplanten Woche angezeigt. Die Box
  "Optimales Trainingsfenster" reagiert dynamisch auf die Planung und nennt
  den Tag, an dem die Form (TSB) wieder positiv wird.

## Google Drive Upload

Nach der Synchronisation werden `activities.json` und `formkurve.html`
automatisch nach Google Drive hochgeladen (dort bereits vorhandene Dateien
mit demselben Namen werden überschrieben).

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

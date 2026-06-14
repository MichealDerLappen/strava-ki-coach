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

3. Optional fuer den Routen-Planer: einen kostenlosen API-Key bei
   [OpenRouteService](https://openrouteservice.org/dev/#/signup) erstellen
   und als `ORS_API_KEY` in `.env` eintragen (siehe
   [Routen-Planer mit echten Strecken & Hoehenmetern](#routen-planer-mit-echten-strecken--hoehenmetern)).

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
- **Formkurven-Chart**: Liniendiagramm (Höhe 700px) mit CTL (grün), ATL (rot)
  und TSB (gelb, mit grün/rot gefüllter Fläche zur Nulllinie für
  positive/negative Form), `hovermode="x unified"` für eine kombinierte
  Hover-Anzeige.
- **Zeitleiste der Aktivitäten**: feine Striche am unteren Rand des Charts,
  ein Hover zeigt Name, Sportart und Distanz (km) der jeweiligen Aktivität.
- **Zoom & Navigation**: Range Slider unter dem Chart sowie
  Schnellauswahl-Buttons ("1M", "3M", "YTD", "Alles").
- **Coach-Analyse**: große Boxen mit den heutigen CTL-/ATL-/TSB-Werten sowie
  dem ACWR (Acute:Chronic Workload Ratio, `ATL / CTL`, gerundet auf zwei
  Nachkommastellen), automatisch generierte Status- (Frische-/Aufbau-/
  Ermüdungszone) und Trendtexte (Fitness steigend/sinkend), sowie
  Hover-Tooltips mit sportwissenschaftlichen Erklärungen zu CTL, ATL, TSB und
  ACWR.
- **Coach-Empfehlung mit ACWR-Überlastungsschutz**: Die Empfehlungs-Box ganz
  oben berücksichtigt neben dem TSB jetzt auch das ACWR. Steigt das ACWR über
  1,5, erscheint unabhängig vom TSB die Warnung "⚠️ Überlastungs-Warnung
  (ACWR: X.XX)" mit dringender Empfehlung, das Training zu reduzieren, und
  die Box färbt sich alarmierend rot. Liegt das ACWR zwischen 0,8 und 1,3 und
  ist der TSB ausgeglichen (-10 bis 10), erscheint stattdessen das Lob
  "🎯 Perfekter Trainingsreiz (ACWR: X.XX)".
- **Wochenplanung (Zukunfts-Simulator)**: Für jeden der nächsten 7 Tage gibt
  es eine eigene Karte mit zwei Slidern für geplante Dauer (0–360 min,
  Schrittweite 5) und Leistung (100–400 W, Schrittweite 5). Bei jeder
  Änderung wird der komplette 7-Tage-Verlauf neu berechnet (TSS je Tag aus
  Dauer/Leistung, anschließend fortlaufende Anwendung der CTL-/ATL-Zerfalls-
  formeln), live als gestrichelte Prognose-Linien ins Chart eingeblendet und
  der Tiefpunkt des TSB in der geplanten Woche angezeigt. Die Box
  "Optimales Trainingsfenster" reagiert dynamisch auf die Planung und nennt
  den Tag, an dem die Form (TSB) wieder positiv wird.
- **Wetter-Vorhersage (Open-Meteo)**: Für jeden der 7 Plantage wird die
  Wettervorhersage für Linz (Höchsttemperatur, Regenwahrscheinlichkeit,
  Windgeschwindigkeit) von der kostenlosen
  [Open-Meteo-API](https://open-meteo.com/) geladen (kein API-Key nötig) und
  als Icon + Kurzinfo in der jeweiligen Tages-Karte angezeigt. Bei
  Regenwahrscheinlichkeit > 60 % wird die Karte dezent bläulich eingefärbt
  und ein Hinweis "🌧️ Regenjacke einpacken!" eingeblendet, bei Windstärken
  > 25 km/h ein Hinweis "💨 Starker Wind! Aero-Position halten oder
  Windschatten suchen". Bei fehlender Internetverbindung läuft das Skript
  ohne Wetterdaten weiter.
- **🌦️ Wetter-Coach-Orakel**: Box oberhalb der Wochenplanung, die die 7
  Wettertage anhand von Temperatur, Regenwahrscheinlichkeit und Wind bewertet
  und die 3 besten Tage für eine Ausfahrt nennt. Dazu gibt es einen
  motivierenden Tipp, der das Wetter des besten Tages mit dem dort
  simulierten TSB abgleicht (z. B. "Nutze das Kaiserwetter am Samstag für
  deine Königseinheit!").
- **Wochentags-Filter**: Über der Wochenplanung lassen sich einzelne
  Tages-Karten per Mo–So-Buttons ein- und ausblenden, um die Ansicht
  übersichtlicher zu halten. Die Werte ausgeblendeter Tage fließen
  weiterhin in die Formkurven-Prognose ein.
- **Routen-Planer mit echten Strecken & Höhenmetern**: Jede Tages-Karte
  enthält ein einklappbares "🗺️ Routen-Planer"-Accordion (standardmäßig
  geschlossen, um die Ansicht aufzuräumen). Darin liegt eine kleine
  Leaflet-Karte (Dark-Mode-Tiles), zentriert auf den Linzer Hauptplatz, mit
  einem Kreis, dessen Radius live aus Dauer und Leistung geschätzt wird
  (`Geschwindigkeit = 22 + (Watt / FTP) * 11`,
  `Distanz = Dauer/60 * Geschwindigkeit`, Wendepunkt-Radius = Distanz / 2).
  Die Karte wird erst beim ersten Aufklappen initialisiert. Anhand des
  Radius werden passende reale Orte im Umland von Linz als
  Wendepunkt-Vorschläge angezeigt. Über den Button "🗺️ Echte Route laden"
  wird (sofern `ORS_API_KEY` in `.env` gesetzt ist) eine echte Strecke samt
  Höhenprofil von [OpenRouteService](https://openrouteservice.org/) geladen,
  als Linie auf der Karte eingezeichnet und die echten Höhenmeter
  ("Echte Höhenmeter: XXX hm") angezeigt. Werden Dauer/Leistung danach
  verändert, wird die Route automatisch (mit kurzer Verzögerung) neu
  geladen.

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

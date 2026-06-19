Lies PROMPT.md in diesem Repo. Das ist die vollstaendige Spezifikation fuer ein Open-Source Wetter- und Energie-Dashboard mit Mikroklima-Vorhersage.

Implementiere das Projekt in der folgenden Reihenfolge. Pruefe nach jedem Schritt, ob alles funktioniert, bevor du zum naechsten gehst.

## Schritt 1: Backend-Grundgeruest

Erstelle backend/requirements.txt, backend/Dockerfile und backend/main.py (FastAPI).
Aktualisiere docker-compose.yml (liegt bereits vor) so, dass InfluxDB + Backend starten.
Erstelle ein init-Script oder Startup-Code, der die drei InfluxDB-Buckets anlegt (weather, energy, forecasts).
Endpoint GET /health soll funktionieren.
Pruefe: `docker compose up --build` startet ohne Fehler, `curl localhost:8000/health` antwortet.

## Schritt 2: Ecowitt Collector

Erstelle backend/collectors/ecowitt_collector.py mit:
- POST /api/ecowitt/webhook (Ecowitt Custom Server Protokoll, Form-Daten parsen, metrisch konvertieren, in InfluxDB schreiben)
- Hintergrund-Task: API-Poller als Fallback (alle 5 Min via Ecowitt API v3)
Pruefe: Sende einen Test-POST an /api/ecowitt/webhook und pruefe, ob Daten in InfluxDB landen.

## Schritt 3: Growatt Collector

Erstelle backend/collectors/growatt_collector.py mit:
- Hintergrund-Task: Pollt alle 5 Min die ShinePhone API (growattServer Library)
- Schreibt battery_soc, pv_power, load_power etc. in InfluxDB Bucket `energy`
- Graceful: Wenn GROWATT_USERNAME leer in .env, Collector ueberspringen (nicht crashen)
Pruefe: Starte Backend mit Growatt-Credentials in .env, pruefe ob Daten in InfluxDB erscheinen.

## Schritt 4: Open-Meteo Collector

Erstelle backend/collectors/openmeteo_collector.py mit:
- Hintergrund-Task: Holt taeglich die 7-Tage-Vorhersage + stuendliche shortwave_radiation
- Speichert in InfluxDB Bucket `forecasts`
- WICHTIG: shortwave_radiation stuendlich speichern (fuer PSH-Berechnung)
Pruefe: Starte Backend, warte auf ersten Lauf, pruefe Daten in InfluxDB.

## Schritt 5: API Endpoints

Implementiere in backend/main.py alle Endpoints aus PROMPT.md:
- GET /api/current (aktuelle Wetter + Batterie aus InfluxDB)
- GET /api/forecast (Open-Meteo 7-Tage, spaeter mit Mikroklima-Korrektur)
- GET /api/forecast/hourly (naechste 24h)
- GET /api/forecast/solar (PSH-Berechnung: sum(shortwave_radiation)/1000, NICHT sunshine_duration)
- GET /api/history?days=7
- GET /api/battery (SOC + PV + Load Zeitreihe)
- GET /api/energy/today
- GET /api/energy/autonomy (SOC * Kapazitaet / avg. Last)
- GET /api/microclimate (Korrektur-Statistiken, leer wenn < 30 Tage Daten)
CORS aktivieren.
Pruefe: Alle Endpoints mit curl testen, JSON-Responses pruefen.

## Schritt 6: Frontend Dashboard

Erstelle frontend/index.html als Single-File PWA (siehe PROMPT.md Sektionen 1-8).
Laedt config.js via <script>. Wenn CONFIG fehlt: Setup-Hinweis, kein Crash.
Dark Theme (#0f0f1a), Akzent Cyan #00d4aa, System-Font-Stack, responsive 360px-1400px.
Chart.js 4.x via CDN (https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js).

Sektionen in dieser Reihenfolge:
1. Header (Station, Update-Zeit, Sunrise/Sunset)
2. Batterie-Widget (SOC als Fuellstand-Grafik, gruen/gelb/rot, Laden/Entladen, PV, Last)
3. Aktuelles Wetter (Temp, Wind mit SVG-Kompass, Druck, Solar, Regen)
4. 7-Tage-Vorhersage (Karten, horizontal scrollbar, WMO-Icons als SVG/Emoji, Mikroklima-Badge)
5. Stuendliche Vorhersage (naechste 24h Streifen)
6. Diagramme (Temp, Wind, Regen, Druck, SOC, PV-Ertrag)
7. Off-Grid-Sektion (PSH nicht sunshine_duration!, Wind-Stunden, Regen, Autonomie)
8. Mikroklima-Statistik (Trefferquote, Lernfortschritt)

KRITISCH fuer Solarertrag: PSH = sum(shortwave_radiation[hourly]) / 1000. Nie sunshine_duration verwenden. Ergebnis z.B. 4.2 PSH, Ertrag = PSH * PV_KWP * PV_EFFICIENCY.

Pruefe: Dashboard im Browser oeffnen, alle Sektionen sichtbar, responsive testen.

## Schritt 7: PWA + Service Worker

Erstelle frontend/manifest.json und frontend/sw.js (siehe PROMPT.md).
Generiere einfache SVG-Icons (192x192, 512x512) als frontend/icons/icon-192.png und icon-512.png.
Cache-First fuer Assets, Network-First fuer API-Calls mit Cache-Fallback.
Pruefe: Chrome DevTools → Application → Manifest und Service Worker aktiv.

## Schritt 8: Historischer Import

Erstelle backend/import_historical.py mit:
- CLI: `python import_historical.py --ecowitt data.csv` (Ecowitt CSV parsen)
- CLI: `python import_historical.py --openmeteo --start 2023-06-01 --end 2026-06-01` (Open-Meteo Historical Forecast API abfragen und archivieren)
- Beides in die jeweiligen InfluxDB-Buckets schreiben.
Pruefe: Dummy-CSV erstellen und importieren, Open-Meteo-Abruf testen.

## Schritt 9: Mikroklima-Modell

Erstelle backend/forecast/microclimate.py mit:
- Temperatur-Bias pro Monat (Durchschnitt forecast vs. measured)
- Niederschlags-Wahrscheinlichkeit (bedingt, nach Windrichtung)
- Wind-Skalierung pro Richtung
- Minimum 30 Tage Daten vor Aktivierung
- Konfidenz-Score basierend auf Datenmenge
Integriere in GET /api/forecast (korrigierte Werte + Badge-Daten).
Pruefe: Mit importierten historischen Daten Korrekturfaktoren berechnen.

## Schritt 10: Scriptable Widget aktualisieren

Aktualisiere extras/scriptable-widget.js (liegt bereits vor) so, dass es zu den tatsaechlichen API-Responses passt.

## Kontext

- Standort: Villa Yacanto, Sierras de Cordoba, Argentinien, 32.1559 S, 64.7916 W, 1000 m
- Ecowitt WH2900, MAC C4:5B:BE:6E:46:15
- Growatt SPF 5000 ES, 3.6 kWp PV, 9.6 kWh LiFePO4 Batterie
- Growatt nutzt ShinePhone/Legacy API (SPF unterstuetzt V1 API nicht)
- Deployment: Contabo VPS mit Docker
- Open Source: MIT Lizenz, Code-Kommentare Englisch, UI-Strings mehrsprachig (de/en/es)
- Kein Tracking, keine Analytics, keine Cookies, kein Build-System
- Alle Credentials aus .env (Backend) bzw. config.js (Frontend), nie hardcoded

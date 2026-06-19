# Prompt: Wetter El Durazno (Full Stack)

## Vision

Open-Source-Wetterstation + Energie-Dashboard mit **Mikroklima-Vorhersage**: Eine PWA, die Ecowitt-Wetterdaten und Growatt-Inverterdaten sammelt, speichert und daraus lernt, um eine individuelle Wettervorhersage zu erstellen, die genauer ist als regionale Modelle.

Anwendungsfall: Die Station steht in den Sierras de Cordoba auf 1.000 m. Regionale Vorhersagen treffen oft nicht zu, weil das Mikroklima 5 km entfernt schon anders ist. Nach Monaten der Datensammlung erkennt das System Korrekturfaktoren (z. B. "wenn Open-Meteo Regen meldet, regnet es hier nur in 30% der Faelle").

## Architektur

```
┌─────────────────────────────────────────────────────┐
│                    FRONTEND (PWA)                    │
│  index.html + manifest.json + sw.js                 │
│  Installierbar auf Handy, Offline-faehig            │
├─────────────────────────────────────────────────────┤
│                   BACKEND (FastAPI)                  │
│  REST API: /api/current, /api/forecast,             │
│            /api/history, /api/battery,              │
│            /api/microclimate                        │
├──────────────┬──────────────┬───────────────────────┤
│  Ecowitt     │  Growatt     │  Open-Meteo           │
│  Collector   │  Collector   │  Collector            │
│  (Webhook +  │  (ShinePhone │  (Forecast +          │
│   API Poll)  │   API Poll)  │   Historical)         │
├──────────────┴──────────────┴───────────────────────┤
│                   InfluxDB 2.x                      │
│  Buckets: weather, energy, forecasts                │
└─────────────────────────────────────────────────────┘
```

## Dateistruktur

```
wetter-el-durazno/
├── backend/
│   ├── main.py                    # FastAPI Server + API Endpoints
│   ├── collectors/
│   │   ├── ecowitt_collector.py   # Ecowitt Webhook-Empfaenger + API-Poller
│   │   ├── growatt_collector.py   # Growatt ShinePhone API (Battery SOC, PV, Load)
│   │   └── openmeteo_collector.py # Open-Meteo Forecast-Archiv (taeglich speichern)
│   ├── forecast/
│   │   └── microclimate.py        # Mikroklima-Korrekturmodell
│   ├── import_historical.py       # Historische Daten importieren (Ecowitt CSV + Open-Meteo)
│   └── requirements.txt
├── frontend/
│   ├── index.html                 # PWA Dashboard (Single File, laedt config.js)
│   ├── manifest.json              # Web App Manifest (Add to Homescreen)
│   ├── sw.js                      # Service Worker (Offline + Cache)
│   └── icons/                     # PWA Icons (192x192, 512x512)
├── docker-compose.yml             # InfluxDB + Backend
├── config.example.js              # Frontend-Konfiguration
├── config.example.env             # Backend-Konfiguration
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

## Phase 1: Backend + Datensammlung

### 1.1 Docker Setup (docker-compose.yml)

Services:
- **influxdb**: InfluxDB 2.x, Port 8086, persistenter Volume-Mount
- **backend**: Python FastAPI, Port 8000, haengt von influxdb ab

InfluxDB Buckets:
- `weather`: Ecowitt-Stationsdaten (Temp, Wind, Regen, Solar, Druck, Feuchte)
- `energy`: Growatt-Daten (Battery SOC, PV Power, Load, Charge/Discharge)
- `forecasts`: Open-Meteo-Vorhersagen (archiviert, fuer Vergleich mit Realitaet)

### 1.2 Ecowitt Collector (backend/collectors/ecowitt_collector.py)

Zwei Modi:

**Webhook-Empfaenger (bevorzugt):**
- FastAPI-Endpoint `POST /api/ecowitt/webhook`
- Ecowitt-Station pusht alle 60s via "Custom Server" (Ecowitt-Protokoll)
- Parst die Form-Daten (tempf, humidity, windspeedmph, rainratein etc.)
- Konvertiert zu metrisch und schreibt in InfluxDB Bucket `weather`

**API-Poller (Fallback):**
- Pollt `https://api.ecowitt.net/api/v3/device/real_time` alle 5 Min
- Benoetigt ECOWITT_APP_KEY, ECOWITT_API_KEY, ECOWITT_MAC aus .env
- Schreibt in InfluxDB Bucket `weather`

Felder in InfluxDB (measurement: `station`):
- temperature_outdoor (°C)
- humidity_outdoor (%)
- temperature_indoor (°C)
- humidity_indoor (%)
- temperature_feels_like (°C)
- dewpoint (°C)
- wind_speed (km/h)
- wind_gust (km/h)
- wind_direction (°)
- pressure_relative (hPa)
- pressure_absolute (hPa)
- rain_rate (mm/h)
- rain_daily (mm)
- solar_radiation (W/m²)
- uv_index

### 1.3 Growatt Collector (backend/collectors/growatt_collector.py)

- Library: `growattServer` (pip install growattServer)
- Verwendet die **ShinePhone/Legacy API** (classic password auth), weil SPF 5000 ES die V1 API nicht unterstuetzt
- Login: `api = growattServer.GrowattApi()`, `api.login(username, password)`
- Daten holen: `api.plant_list()` → `api.storage_params(plant_id, inverter_sn)` oder `api.mix_detail(plant_id, inverter_sn)` (SPF kann als "storage" oder "mix" registriert sein)
- Pollt alle 5 Minuten (Growatt-Server-Update-Intervall)
- Login-Session cachen und bei 401 neu einloggen

Felder in InfluxDB (measurement: `energy`, bucket: `energy`):
- battery_soc (%)
- battery_voltage (V)
- battery_power (W, positiv = laden, negativ = entladen)
- pv_power (W)
- pv_energy_today (kWh)
- load_power (W)
- load_energy_today (kWh)
- inverter_status (string)
- inverter_temperature (°C)

**Wichtig:** Growatt-Credentials (Username/Passwort) sind persoenlich. Im Open-Source-Code nur Platzhalter. Login-Daten ausschliesslich aus .env lesen.

### 1.4 Open-Meteo Collector (backend/collectors/openmeteo_collector.py)

- Speichert taeglich die aktuelle 7-Tage-Vorhersage in InfluxDB Bucket `forecasts`
- Wird spaeter mit den tatsaechlich eingetretenen Wetterdaten verglichen
- Kein API-Key noetig
- Endpoint: `https://api.open-meteo.com/v1/forecast`

**Wichtige Felder im API-Call:**
- daily: temperature_2m_max, temperature_2m_min, precipitation_sum, windspeed_10m_max, windgusts_10m_max, weathercode, sunrise, sunset, sunshine_duration
- hourly: shortwave_radiation, temperature_2m, windspeed_10m, winddirection_10m, windgusts_10m, precipitation, precipitation_probability, cloudcover, weathercode
- `shortwave_radiation` (W/m², stuendlich) ist der Schluessel fuer die Solarertrag-Berechnung (siehe Off-Grid-Sektion)
- Tag: `lead_days` (0 = heute, 1 = morgen, ... 6)

### 1.5 FastAPI Endpoints (backend/main.py)

```
GET  /api/current          # Aktuelle Wetterdaten + Batterie-Status
GET  /api/forecast         # Open-Meteo 7-Tage + Mikroklima-Korrektur
GET  /api/forecast/hourly  # Stuendliche Vorhersage (24h)
GET  /api/forecast/solar   # PSH + PV-Ertragsprognose pro Tag (7 Tage)
GET  /api/history?days=7   # Historische Wetterdaten aus InfluxDB
GET  /api/battery          # Batterie SOC + PV + Load Zeitreihe
GET  /api/microclimate     # Korrektur-Statistiken (Trefferquoten)
GET  /api/energy/today     # Tages-Energiebilanz
GET  /api/energy/autonomy  # Geschaetzte Restautonomie in Stunden
POST /api/ecowitt/webhook  # Ecowitt Custom Server Empfaenger
GET  /health               # Healthcheck
```

**`/api/forecast/solar` Berechnung:**
```python
# Fuer jeden Tag der naechsten 7 Tage:
# 1. Stuendliche shortwave_radiation (W/m²) von Open-Meteo holen
# 2. PSH = sum(hourly_radiation) / 1000
# 3. estimated_kwh = PSH * PV_KWP * PV_EFFICIENCY
# 4. production_window = Stunden wo radiation > 100 W/m²
# 5. Wenn historische Growatt-Daten vorhanden: Korrekturfaktor anwenden
```

**`/api/energy/autonomy` Berechnung:**
```python
# current_soc (%) * BATTERY_CAPACITY_KWH / avg_load_kw = hours_remaining
# avg_load_kw aus den letzten 24h Growatt-Daten
```

CORS aktivieren fuer Frontend-Zugriff.

## Phase 2: Frontend (PWA)

### 2.1 Dashboard (frontend/index.html)

Einzelne HTML-Datei. Laedt `config.js` (Nutzerkonfiguration) und kommuniziert mit dem Backend.

**Config-Handling:**
- `<script src="config.js"></script>` erwartet globales `CONFIG`
- Wenn CONFIG fehlt: Setup-Hinweis anzeigen, nicht crashen
- CONFIG.BACKEND_URL: URL des FastAPI Backends (z. B. `https://wetter.example.com/api`)

**Design:**
- Dark Theme (Hintergrund #0f0f1a, Karten #1a1a2e, Akzent #00d4aa Cyan/Teal)
- System-Font-Stack (kein externer Font-Load)
- Responsive: 360px (Handy) bis 1400px+ (Desktop)
- Chart.js 4.x via CDN fuer Diagramme

**Sektionen:**

1. **Header:** Stationsname, Letzte Aktualisierung, Sonnenauf-/untergang

2. **Batterie-Widget:** Prominente Anzeige:
   - Grosse SOC-Anzeige als visueller Fuellstand (Batterie-Icon mit Fuellgrad)
   - Farbe: gruen (>50%), gelb (20-50%), rot (<20%)
   - Aktuell laden/entladen mit Leistung (z. B. "Laden 1.2 kW" / "Entladen 0.8 kW")
   - PV-Leistung aktuell
   - Last aktuell
   - Tagesertrag PV / Tagesverbrauch

3. **Aktuelles Wetter:** (von Backend /api/current)
   - Aussentemperatur + gefuehlte Temp
   - Luftfeuchtigkeit + Taupunkt
   - Wind (Geschwindigkeit + Boeen + Richtung als SVG-Kompass)
   - Luftdruck (relativ)
   - Solar-Strahlung + UV-Index
   - Regenrate + Tagesregen

4. **7-Tage-Vorhersage:** (von Backend /api/forecast)
   - Kartenreihe: Tag, WMO-Icon, Min/Max-Temp, Niederschlag, Wind
   - Zeigt Mikroklima-Korrektur als kleinen Badge (z. B. "lokal -2°C" oder "Regen 30% statt 70%")
   - Horizontal scrollbar auf Handy

5. **Stuendliche Vorhersage:** (naechste 24h)
   - Streifen: Stunde, Icon, Temp, Regenwahrscheinlichkeit, Wind

6. **Diagramme:** (letzte 7 Tage + naechste 7 Tage)
   - Temperatur (Min/Max Bereichs-Chart)
   - Wind (Durchschnitt + Boeen)
   - Niederschlag (Balken, Vorhersage vs. gemessen)
   - Luftdruck (Linie)
   - Batterie SOC (Linie, letzte 7 Tage)
   - PV-Ertrag (Flaechen-Chart, letzte 7 Tage)

7. **Off-Grid-Sektion:** (wenn CONFIG.SHOW_OFFGRID true)

   **Solarertrag-Prognose (korrekte Berechnung, NICHT sunshine_duration):**

   `sunshine_duration` von Open-Meteo zaehlt nur Stunden mit Direktstrahlung > 120 W/m² und ist fuer Solaranlagen unbrauchbar (ergibt z. B. 9,6h an einem Wintertag, obwohl die Anlage effektiv nur 4-5h produziert).

   Stattdessen **Peak Sun Hours (PSH)** berechnen:
   ```
   PSH = Summe(shortwave_radiation[h] fuer alle Stunden des Tages) / 1000
   ```
   Dabei ist `shortwave_radiation` die stuendliche Global Horizontal Irradiance (GHI) in W/m² von Open-Meteo. Division durch 1000 W/m² ergibt die aequivalenten Volllaststunden.

   Beispiel: An einem klaren Wintertag bei 32° S kommen typisch 3.5 bis 5.0 PSH zusammen, im Sommer 6 bis 7 PSH. Das entspricht der realen Erfahrung.

   Anzeige pro Tag:
   - **PSH** (Peak Sun Hours): z. B. "4,2 PSH"
   - **Geschaetzter PV-Ertrag**: PSH x CONFIG.PV_KWP x CONFIG.PV_EFFICIENCY
     Beispiel: 4,2 PSH x 3,6 kWp x 0,75 = 11,3 kWh
   - **Produktionsfenster**: Stunden mit shortwave_radiation > 100 W/m² (wann die Anlage tatsaechlich laeuft)
   - Farbliche Bewertung: gruen (> 4 PSH), gelb (2-4 PSH), rot (< 2 PSH)

   Wenn Growatt-Daten vorhanden: tatsaechlichen PV-Ertrag neben Prognose anzeigen (Soll/Ist-Vergleich). Daraus langfristig einen Korrekturfaktor lernen (Verschattung, Panelausrichtung, Alterung).

   **Wind-Potenzial:**
   - Stunden mit Wind > CONFIG.WIND_THRESHOLD_KMH pro Tag
   - Vorherrschende Windrichtung
   - Fuer VAWT/HAWT Kleinwindkraft relevant

   **Regen/Hydro-Prognose:**
   - Kumulative Regenmenge naechste 7 Tage (mm)
   - Relevanz fuer Mikro-Wasserkraft und Zisternen
   - Farbliche Bewertung: blau (> 20 mm), grau (5-20 mm), rot (< 5 mm)

   **Tages-Energiebilanz (wenn Growatt aktiv):**
   - Balkendiagramm: PV-Ertrag vs. Verbrauch vs. Batterie-Delta
   - Geschaetzte Autonomie: "Batterie reicht noch ~X Stunden" (basierend auf aktuellem SOC und durchschnittlichem Verbrauch)

8. **Mikroklima-Statistik:** (wenn genuegend Daten vorhanden)
   - Trefferquote der Vorhersage (Regen ja/nein)
   - Durchschnittliche Temperatur-Abweichung
   - Typische Wind-Korrektur
   - "Lernfortschritt": Wie viele Tage Vergleichsdaten

### 2.2 PWA Setup

**manifest.json:**
```json
{
  "name": "Wetter Dashboard",
  "short_name": "Wetter",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f0f1a",
  "theme_color": "#00d4aa",
  "icons": [
    { "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

**Service Worker (sw.js):**
- Cache-First fuer statische Assets (HTML, JS, CSS, Icons)
- Network-First fuer API-Calls (mit Cache-Fallback fuer Offline)
- Periodischer Background-Fetch (wenn Browser unterstuetzt)

**Installierbar:**
- "Add to Homescreen" auf Android
- Standalone-Modus (keine Browser-Leiste)
- Auf iOS als Web-Clip installierbar

### 2.3 Handy-Widgets

Da native Widgets eine native App erfordern (und das den Scope sprengt), folgende Alternatekn:

**Android:**
- PWA-Shortcut auf Homescreen (manifest.json `shortcuts`)
- Fuer echte Widgets: Tasker/KWGT Integration via API (Doku bereitstellen)

**iOS:**
- Scriptable App (JavaScript): Beispiel-Script bereitstellen, das die API abfragt und ein Widget rendert (`ios-widget.js`)
- Shortcuts App: Shortcut-Template fuer Wetter-Abfrage

Erstelle ein Beispiel-Script fuer iOS Scriptable: `extras/scriptable-widget.js`

## Phase 3: Historischer Import + Mikroklima-Modell

### 3.1 Historischer Import (backend/import_historical.py)

Importiert zwei Datenquellen und fuegt sie in InfluxDB zusammen:

**Ecowitt-Exportdaten:**
- User laedt CSV-Export aus der Ecowitt-App/Web herunter (3 Jahre Daten)
- Script parst die CSV (verschiedene Formate je nach Export-Version)
- Schreibt in InfluxDB Bucket `weather` mit historischen Timestamps

**Open-Meteo Historical Forecast API:**
- Fuer denselben Zeitraum die archivierten Vorhersagen holen
- Endpoint: `https://historical-forecast-api.open-meteo.com/v1/forecast`
- Ermoeglicht sofortigen Vergleich: Was hat Open-Meteo vorhergesagt vs. was wurde lokal gemessen
- Schreibt in InfluxDB Bucket `forecasts`

### 3.2 Mikroklima-Korrekturmodell (backend/forecast/microclimate.py)

Einfaches statistisches Modell (kein ML-Framework noetig):

**Datenbasis:** Paare von (Open-Meteo-Vorhersage, lokale Messung) fuer jeden Tag.

**Korrekturfaktoren:**
1. **Temperatur-Bias:** Durchschnittliche Abweichung pro Monat und Tageszeit
   - z. B. "Im Juni ist es lokal morgens 2°C kaelter als Open-Meteo vorhersagt"
2. **Niederschlags-Wahrscheinlichkeit:** Bedingte Wahrscheinlichkeit
   - P(lokal Regen | Open-Meteo sagt Regen) und P(lokal Regen | Open-Meteo sagt kein Regen)
   - Aufgeschluesselt nach Windrichtung (z. B. bei Westwind regnet es eher)
3. **Wind-Skalierung:** Verhaeltnis lokal/regional pro Windrichtung
   - Topographie beeinflusst Wind stark; manche Richtungen werden kanalisiert

**Anwendung:**
- Open-Meteo-Vorhersage holen
- Korrekturfaktoren anwenden
- Korrigierte Vorhersage an Frontend liefern
- Konfidenz anzeigen (basierend auf Datenmenge)

**Minimum-Datenmenge:** 30 Tage vor Aktivierung, 90+ Tage fuer zuverlaessige saisonale Korrektur.

## Phase 4: Konfiguration

### Backend (.env)

```env
# InfluxDB
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=mein-token
INFLUXDB_ORG=wetter
INFLUXDB_BUCKET_WEATHER=weather
INFLUXDB_BUCKET_ENERGY=energy
INFLUXDB_BUCKET_FORECASTS=forecasts

# Ecowitt
ECOWITT_APP_KEY=
ECOWITT_API_KEY=
ECOWITT_MAC=

# Growatt (ShinePhone Login)
GROWATT_USERNAME=
GROWATT_PASSWORD=
GROWATT_PLANT_ID=
GROWATT_INVERTER_SN=

# Standort
LATITUDE=-32.1559
LONGITUDE=-64.7916
ALTITUDE=1000
TIMEZONE=America/Argentina/Cordoba

# Server
BACKEND_PORT=8000
```

### Frontend (config.js)

```javascript
const CONFIG = {
    STATION_NAME: 'Meine Wetterstation',
    BACKEND_URL: 'https://wetter.example.com/api',
    UNITS: 'metric',
    LANGUAGE: 'de',
    REFRESH_INTERVAL: 300,
    SHOW_OFFGRID: true,
    SHOW_BATTERY: true,
    SHOW_MICROCLIMATE: true,
    WIND_THRESHOLD_KMH: 10,
    BATTERY_CAPACITY_KWH: 9.6,
    PV_KWP: 3.6,              // installierte PV-Leistung in kWp
    PV_EFFICIENCY: 0.75,       // System-Wirkungsgrad (0.70-0.85 typisch)
};
```

## Technische Anforderungen

- Python 3.11+, FastAPI, uvicorn, influxdb-client, growattServer, httpx
- Frontend: Vanilla HTML/CSS/JS, Chart.js 4.x via CDN
- Docker + Docker Compose fuer Deployment
- Kein Tracking, keine Analytics, keine Cookies
- Sauberer, kommentierter Code (Englisch), bereit fuer Open-Source
- Alle Credentials ausschliesslich aus .env / config.js

## Qualitaetskriterien

- [ ] Backend startet mit `docker compose up`
- [ ] Ecowitt-Webhook empfaengt und speichert Daten
- [ ] Growatt-Collector holt Battery SOC und PV-Daten
- [ ] Frontend zeigt Batterie-Widget mit Fuellstand
- [ ] PWA installierbar auf Android (Homescreen)
- [ ] Offline-Modus zeigt letzte bekannte Daten
- [ ] Historischer Import liest Ecowitt-CSV + Open-Meteo-Archiv
- [ ] Mikroklima-Korrektur nach 30+ Tagen aktiv
- [ ] Dashboard zeigt Korrektur-Badge in Vorhersage
- [ ] iOS Scriptable Widget funktioniert
- [ ] Responsive auf 360px und 1400px
- [ ] Alle Texte in CONFIG.LANGUAGE

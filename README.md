# Weather Off-Grid

Open-Source-Wetter- und Energie-Dashboard mit **Mikroklima-Vorhersage** fuer Off-Grid-Systeme.

Unterstuetzt Ecowitt-Wetterstationen, Growatt-Inverter und ist erweiterbar fuer andere Hardware. Sammelt lokale Wetterdaten, vergleicht sie mit regionalen Vorhersagen und lernt daraus Korrekturfaktoren fuer dein Mikroklima. Dazu Batterie-Anzeige und Off-Grid-Energiebilanz. Als PWA installierbar auf dem Handy.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

## Warum?

Regionale Wettervorhersagen treffen in Berglagen oder besonderen Mikroklimata oft nicht zu. Wer 5 km von der naechsten Ortschaft entfernt lebt, kennt das: Dort regnet es, hier scheint die Sonne.

Dieses Dashboard sammelt deine lokalen Messdaten, archiviert parallel die regionalen Vorhersagen und berechnet nach einigen Wochen automatische Korrekturfaktoren. Je laenger es laeuft, desto genauer wird die lokale Vorhersage.

## Features

**Wetter**
- Live-Daten von Ecowitt-Stationen (Temperatur, Wind, Regen, Solar, Luftdruck)
- 7-Tage-Vorhersage via Open-Meteo (kostenlos, kein API-Key)
- Historische Diagramme (Temperatur, Wind, Niederschlag, Luftdruck)
- Mikroklima-Korrektur: lernt aus dem Vergleich Vorhersage vs. Realitaet

**Energie**
- Growatt-Inverter: Battery SOC, PV-Leistung, Last, Tagesertrag
- Batterie-Widget mit visuellem Fuellstand
- PV-Ertrags-Diagramme

**Off-Grid** (optional)
- Sonnenstunden-Prognose fuer Solarertrag
- Wind-Stunden fuer Kleinwindkraft (VAWT/HAWT)
- Regen-Prognose fuer Mikro-Wasserkraft / Zisternen

**App**
- PWA: installierbar auf Android und iOS
- Offline-Modus mit gecachten Daten
- iOS Scriptable Widget (Beispiel-Script enthalten)

## Architektur

```
Frontend (PWA)  ──→  Backend (FastAPI)  ──→  InfluxDB
                          ↑
              ┌───────────┼───────────┐
          Ecowitt     Growatt     Open-Meteo
          Station     Inverter    Forecast
```

Alles laeuft per Docker Compose auf einem Server.

## Schnellstart

### Voraussetzungen

- Server mit Docker + Docker Compose (VPS, Raspberry Pi, NAS)
- Ecowitt-Wetterstation mit WiFi (WH2900, GW1000, GW2000 o. ae.)
- Optional: Growatt-Inverter mit Datalogger (ShinePhone-Zugang)

### 1. Repo klonen und konfigurieren

```bash
git clone https://github.com/DEIN-USER/weather-off-grid.git
cd weather-off-grid
cp config.example.env .env
cp config.example.js frontend/config.js
```

### 2. Credentials eintragen

**.env** (Backend):
```env
ECOWITT_APP_KEY=dein-key          # von api.ecowitt.net
ECOWITT_API_KEY=dein-key
ECOWITT_MAC=AA:BB:CC:DD:EE:FF
GROWATT_USERNAME=dein-username     # ShinePhone Login
GROWATT_PASSWORD=dein-passwort
LATITUDE=-32.1559                 # dein Standort
LONGITUDE=-64.7916
```

**frontend/config.js** (Frontend):
```javascript
const CONFIG = {
    STATION_NAME: 'Meine Wetterstation',
    BACKEND_URL: 'https://weather.example.com/api',
    // ...
};
```

### 3. Starten

```bash
docker compose up -d
```

Dashboard unter `http://dein-server:8000` oeffnen.

### 4. Ecowitt Custom Server einrichten (optional, empfohlen)

In der Ecowitt-App: Geraet → Weiteres → Custom Server:
- Protokoll: Ecowitt
- Server: IP oder Domain deines Servers
- Port: 8000
- Pfad: /api/ecowitt/webhook

Damit pusht die Station alle 60 Sekunden Live-Daten direkt ans Backend.

### 5. Historische Daten importieren (optional)

```bash
# Ecowitt-CSV-Export in import/ ablegen
docker compose exec backend python import_historical.py --ecowitt import/ecowitt_export.csv
# Open-Meteo-Vergleichsdaten automatisch holen
docker compose exec backend python import_historical.py --openmeteo --years 3
```

## Einstellungen & Multi-Standort

Ab Version 1.1 muss die `.env` nicht mehr von Hand editiert werden: Über die
**Settings-Seite** (`/settings.html`, Zahnrad-Icon im Header) lassen sich alle
Werte im Browser bearbeiten. Sie werden persistent in `data/settings.json`
gespeichert (Docker-Volume `settings-data`).

- **Setup-Wizard** beim ersten Start (Standort → Ecowitt → Growatt → Energie),
  jeder Schritt mit "Verbindung testen".
- **Mehrere Standorte**: Jeder Standort hat eigene Hardware, eigene Daten,
  eigene Mikroklima-Korrektur. Umschalten über das Dropdown im Header
  (`/#standort-id`). Standorte ohne Hardware sind erlaubt (nur Open-Meteo).
- **Hot-Reload**: Änderungen übernimmt das Backend ohne Neustart (Collectors
  werden pro Standort neu gestartet).
- **PIN-Schutz**: Setze `ADMIN_PIN` in der `.env`, um die Settings-Seite zu
  schützen. Leer = offen (nur fürs lokale Netz gedacht).
- Beim ersten Start ohne `settings.json` werden die `.env`-Werte als
  Default-Standort übernommen - bestehende Single-Site-Installationen laufen
  unverändert weiter.

Jeder Daten-Endpoint akzeptiert `?site=<id>` (z. B. `/api/current?site=el-durazno`);
ohne Parameter wird der Default-Standort verwendet. `config.js` ist optional -
ist das Backend erreichbar, holt das Frontend seine Konfiguration über
`GET /api/settings/frontend`; `config.js` dient nur noch als Offline-Fallback.

## Konfigurationsreferenz

### Backend (.env)

| Variable | Beschreibung |
|----------|-------------|
| `ECOWITT_APP_KEY` | Ecowitt Application Key |
| `ECOWITT_API_KEY` | Ecowitt API Key |
| `ECOWITT_MAC` | MAC-Adresse der Station |
| `GROWATT_USERNAME` | ShinePhone Benutzername |
| `GROWATT_PASSWORD` | ShinePhone Passwort |
| `LATITUDE` | Breitengrad (negativ = Sued) |
| `LONGITUDE` | Laengengrad (negativ = West) |
| `ALTITUDE` | Hoehe in Metern |
| `TIMEZONE` | IANA Timezone |

### Frontend (config.js)

| Parameter | Typ | Beschreibung |
|-----------|-----|-------------|
| `STATION_NAME` | String | Anzeigename |
| `BACKEND_URL` | String | URL des FastAPI Backends |
| `SHOW_BATTERY` | Boolean | Batterie-Widget anzeigen |
| `SHOW_OFFGRID` | Boolean | Off-Grid-Sektion anzeigen |
| `SHOW_MICROCLIMATE` | Boolean | Mikroklima-Korrekturen anzeigen |
| `BATTERY_CAPACITY_KWH` | Number | Batteriekapazitaet (fuer Prozent-Berechnung) |

## Unterstuetzte Hardware

### Wetterstationen (Ecowitt-Protokoll)

| Modell | Frequenz | Status |
|--------|----------|--------|
| Ecowitt WH2900 | 433 MHz | Getestet |
| Ecowitt GW1000/1100 | 433/868 MHz | Kompatibel (ungetestet) |
| Ecowitt GW2000 | 433/868 MHz | Kompatibel (ungetestet) |
| Ecowitt HP2551/HP3501 | 433 MHz | Kompatibel (ungetestet) |
| Froggit-Stationen | 433 MHz | Kompatibel (Ecowitt OEM) |

### Inverter

| Modell | API | Status |
|--------|-----|--------|
| Growatt SPF 5000 ES | ShinePhone (Legacy) | Getestet |
| Growatt SPF 3000-6000 Serie | ShinePhone (Legacy) | Kompatibel (ungetestet) |
| Growatt MIN/MIC/MOD Serie | OpenAPI V1 | Geplant |
| Growatt MIX/SPH Serie | ShinePhone (Legacy) | Kompatibel (ungetestet) |

> **Andere Hardware?** Die Architektur ist modular: neue Collectors koennen als Python-Module in `backend/collectors/` hinzugefuegt werden. Pull Requests willkommen!

## Mikroklima-Modell

Nach ~30 Tagen Datensammlung beginnt das System, Korrekturfaktoren zu berechnen:

| Korrektur | Methode | Beispiel |
|-----------|---------|---------|
| Temperatur-Bias | Durchschnittl. Abweichung pro Monat | "Im Winter morgens 2°C kaelter" |
| Niederschlag | Bedingte Wahrscheinlichkeit | "Wenn Open-Meteo Regen sagt: lokal 30%" |
| Wind-Skalierung | Verhaeltnis pro Windrichtung | "Westwind lokal 1.4x staerker" |

Die Korrekturen werden im Dashboard als Badge angezeigt und verbessern sich mit mehr Daten.

## Tech-Stack

| Komponente | Technologie |
|-----------|-------------|
| Backend | Python 3.11+, FastAPI, uvicorn |
| Datenbank | InfluxDB 2.x |
| Growatt-API | growattServer (PyPI) |
| Wetter-API | Open-Meteo (kostenlos) |
| Frontend | Vanilla HTML/CSS/JS, Chart.js 4.x |
| Deployment | Docker Compose |

## Mitmachen

Beitraege willkommen! Siehe [CONTRIBUTING.md](CONTRIBUTING.md).

## Entstehung

Entstanden fuer die Off-Grid-Wettuerueberwachung auf einem Grundstueck in den Sierras de Cordoba, Argentinien. Code generiert mit [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Prompt in [`PROMPT.md`](PROMPT.md)).

## Lizenz

[MIT](LICENSE)

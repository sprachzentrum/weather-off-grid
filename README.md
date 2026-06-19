# Wetter El Durazno

Wetterstation-Dashboard für das Off-Grid-Grundstück "El Durazno" bei Villa Yacanto, Calamuchita, Córdoba, Argentinien (~1.000 m ü. M.).

Kombiniert **Live-Daten** einer Ecowitt WH2900 Wetterstation mit der **7-Tage-Vorhersage** von Open-Meteo und zeigt Off-Grid-relevante Kennzahlen (Solarertrag, Windpotenzial, Regen/Hydro).

## Features

- Aktuelle Wetterdaten von Ecowitt WH2900 (Temperatur, Wind, Regen, Solar, Luftdruck)
- 7-Tage-Vorhersage via Open-Meteo (kostenlos, kein API-Key)
- Stündliche Vorhersage (nächste 24h)
- Historische Diagramme (letzte 7 Tage)
- Off-Grid-Sektion: Sonnenstunden, Wind-Stunden (VAWT), Regen-Prognose (Mikro-Hydro)
- Dark Theme, responsive (Handy + Desktop)
- Einzelne HTML-Datei, kein Build-System

## Standort

| Parameter | Wert |
|-----------|------|
| Koordinaten | 32.1559° S, 64.7916° W |
| Höhe | ~1.000 m ü. M. |
| Station | Ecowitt WH2900, 433 MHz |
| MAC | C4:5B:BE:6E:46:15 |
| Firmware | EasyWeatherV1.7.6 |

## Setup

### 1. Ecowitt API-Keys generieren

1. Gehe zu [api.ecowitt.net](https://api.ecowitt.net)
2. Erstelle einen Account (oder logge dich ein)
3. Unter "API Key" einen neuen Application Key erstellen
4. Den API Key notieren

### 2. Konfiguration

Kopiere `.env.example` nach `.env` und trage deine Keys ein:

```bash
cp .env.example .env
```

Dann die Keys in `wetter-el-durazno.html` oben in den Konfigurationsblock eintragen:

```javascript
const CONFIG = {
    ECOWITT_APP_KEY: 'dein-application-key',
    ECOWITT_API_KEY: 'dein-api-key',
    // ...
};
```

### 3. Deployment

Die Datei `wetter-el-durazno.html` direkt auf einem Webserver ablegen. Kein Node.js oder Build-Step nötig.

```bash
scp wetter-el-durazno.html user@server:/var/www/html/wetter/
```

## APIs

| API | Zweck | Auth |
|-----|-------|------|
| [Open-Meteo](https://open-meteo.com/en/docs) | Vorhersage + Historie | Keine (kostenlos) |
| [Ecowitt API v3](https://doc.ecowitt.net/web/#/apiv3) | Live-Stationsdaten | Application Key + API Key |

## Tech-Stack

- Vanilla HTML + CSS + JavaScript (Single File)
- [Chart.js 4.x](https://www.chartjs.org/) via CDN (Diagramme)
- Open-Meteo API (Forecast)
- Ecowitt API v3 (Live-Daten)

## Energiesystem-Kontext

Das Dashboard ist Teil eines Off-Grid-Energiemonitoring-Systems:

- **Solar:** 8x 450W Panels, Growatt SPF 5000 ES Inverter
- **Wind:** AECEVAN VAWT Turbine (DC auf 48V Batterie)
- **Hydro:** geplante Mikro-Wasserkraft am Bach (~16 m Fallhöhe, ~4.8 L/s)
- **Batterie:** 9.6 kWh LiFePO4 (2x Growatt Hope 4.8 kWh)

Die Off-Grid-Sektion des Dashboards zeigt, welche Energiequelle an welchem Tag voraussichtlich wie viel beiträgt.

## Entwicklung

Das Dashboard wurde mit Hilfe von Claude Code generiert. Der verwendete Prompt liegt in [`PROMPT.md`](PROMPT.md).

## Lizenz

MIT

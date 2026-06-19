# Wetter Dashboard

Open-Source-Wetterstation-Dashboard für **Ecowitt**-Stationen mit **Open-Meteo**-Vorhersage und Off-Grid-Energierelevanz.

Einzelne HTML-Datei, kein Backend, kein Build-System. Einfach konfigurieren und auf jedem Webserver ablegen.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

## Features

- **Live-Daten** von Ecowitt-Stationen (Temperatur, Wind, Regen, Solar, Luftdruck)
- **7-Tage-Vorhersage** via [Open-Meteo](https://open-meteo.com/) (kostenlos, kein API-Key)
- **Stündliche Vorhersage** (nächste 24 Stunden)
- **Historische Diagramme** (letzte 7 Tage: Temperatur, Wind, Niederschlag, Luftdruck)
- **Off-Grid-Sektion** (optional): Sonnenstunden-Prognose, Wind-Potenzial für Kleinwindkraft, Regen-Prognose für Mikro-Wasserkraft
- Dark Theme, responsive (Handy + Desktop)
- Auto-Refresh alle 5 Minuten
- Offline-Fallback (letzte Daten im LocalStorage)

## Getestete Stationen

| Modell | Frequenz | Status |
|--------|----------|--------|
| Ecowitt WH2900 | 433 MHz | Getestet |

> Andere Ecowitt-Modelle mit API-Zugang (GW1000, GW1100, GW2000, HP2551 etc.) sollten ebenfalls funktionieren. Pull Requests mit Erfahrungsberichten sind willkommen!

## Schnellstart

### 1. Ecowitt API-Keys generieren

1. Registriere dich auf [api.ecowitt.net](https://api.ecowitt.net)
2. Erstelle unter "API Key" einen **Application Key**
3. Generiere einen **API Key**
4. Notiere die MAC-Adresse deiner Station (findbar in der Ecowitt-App unter Geräteeinstellungen)

### 2. Konfiguration

Kopiere die Beispieldatei und trage deine Daten ein:

```bash
cp config.example.js config.js
```

Bearbeite `config.js`:

```javascript
const CONFIG = {
    // -- Deine Station --
    STATION_NAME: 'Meine Wetterstation',
    LATITUDE: -32.1559,
    LONGITUDE: -64.7916,
    ALTITUDE: 1000,
    TIMEZONE: 'America/Argentina/Cordoba',

    // -- Ecowitt API --
    ECOWITT_APP_KEY: 'dein-application-key',
    ECOWITT_API_KEY: 'dein-api-key',
    ECOWITT_MAC: 'AA:BB:CC:DD:EE:FF',

    // -- Optionen --
    UNITS: 'metric',
    LANGUAGE: 'de',
    REFRESH_INTERVAL: 300,
    SHOW_OFFGRID: true,
};
```

### 3. Deployment

Die Dateien auf einen beliebigen Webserver kopieren:

```bash
scp index.html config.js user@server:/var/www/html/wetter/
```

Oder lokal testen:

```bash
python3 -m http.server 8080
# dann http://localhost:8080 aufrufen
```

## Konfigurationsreferenz

| Parameter | Typ | Beschreibung |
|-----------|-----|-------------|
| `STATION_NAME` | String | Anzeigename im Dashboard |
| `LATITUDE` | Number | Breitengrad (negativ = Süd) |
| `LONGITUDE` | Number | Längengrad (negativ = West) |
| `ALTITUDE` | Number | Höhe in Metern ü. M. |
| `TIMEZONE` | String | [IANA Timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `ECOWITT_APP_KEY` | String | Application Key von api.ecowitt.net |
| `ECOWITT_API_KEY` | String | API Key von api.ecowitt.net |
| `ECOWITT_MAC` | String | MAC-Adresse der Station |
| `UNITS` | String | `metric` (°C, km/h, mm, hPa) oder `imperial` |
| `LANGUAGE` | String | `de`, `en`, oder `es` |
| `REFRESH_INTERVAL` | Number | Auto-Refresh in Sekunden (min. 60) |
| `SHOW_OFFGRID` | Boolean | Off-Grid-Sektion anzeigen (Solar/Wind/Hydro) |

## APIs

| API | Zweck | Kosten |
|-----|-------|--------|
| [Open-Meteo](https://open-meteo.com/en/docs) | Vorhersage + Historie | Kostenlos (non-commercial), ab 100 EUR/Monat (commercial) |
| [Ecowitt API v3](https://doc.ecowitt.net/web/#/apiv3) | Live-Stationsdaten | Kostenlos |

## Tech-Stack

- Vanilla HTML + CSS + JavaScript
- [Chart.js 4.x](https://www.chartjs.org/) via CDN
- Keine Dependencies, kein Build-System, kein Backend

## Off-Grid-Sektion

Die optionale Off-Grid-Sektion zeigt Kennzahlen, die für die Planung von autarken Energiesystemen relevant sind:

| Kennzahl | Quelle | Relevanz |
|----------|--------|----------|
| Sonnenstunden / Tag | Open-Meteo `sunshine_duration` | Solarertrag-Schätzung |
| Stunden Wind > 10 km/h | Open-Meteo stündliche Winddaten | Kleinwindkraft (VAWT/HAWT) |
| Kumulative Regenprognose | Open-Meteo `precipitation_sum` | Mikro-Wasserkraft, Zisternen |

Aktivieren mit `SHOW_OFFGRID: true` in `config.js`.

## Mitmachen

Beiträge sind willkommen! Siehe [CONTRIBUTING.md](CONTRIBUTING.md).

Besonders gesucht:

- Tests mit anderen Ecowitt-Modellen
- Übersetzungen (aktuell: DE, EN, ES)
- Verbesserungen am Off-Grid-Modul
- Barrierefreiheit (Accessibility)

## Entstehung

Dieses Projekt entstand für die Off-Grid-Wetterüberwachung auf einem Grundstück in den Sierras de Córdoba, Argentinien. Der ursprüngliche Code wurde mit [Claude Code](https://docs.anthropic.com/en/docs/claude-code) generiert (Prompt in [`PROMPT.md`](PROMPT.md)).

## Lizenz

[MIT](LICENSE)

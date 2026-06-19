# Prompt: Wetter Dashboard (Open Source)

## Aufgabe

Baue ein konfigurierbares Wetter-Dashboard, das:

1. **Live-Daten** von einer Ecowitt-Wetterstation anzeigt (via Ecowitt API v3)
2. **7-Tage-Vorhersage** von Open-Meteo einbindet (kostenlos, kein API-Key nötig)
3. **Historische Daten** als Diagramme darstellt (Temperatur, Wind, Regen der letzten 7 Tage)
4. **Off-Grid-Relevanz** optional einblendet (Solar, Wind, Hydro)

Das Projekt ist Open Source. Alle stationsspezifischen Daten (Koordinaten, API-Keys, MAC) werden aus einer separaten `config.js` gelesen, die nicht im Repo liegt.

## Architektur

```
index.html       ← Dashboard (Hauptdatei)
config.js        ← Nutzerkonfiguration (nicht im Repo, in .gitignore)
config.example.js ← Vorlage zum Kopieren
```

`index.html` lädt `config.js` via `<script src="config.js"></script>` und erwartet ein globales `CONFIG`-Objekt. Wenn `config.js` fehlt oder `CONFIG` nicht definiert ist, zeigt das Dashboard einen freundlichen Setup-Hinweis statt zu crashen.

## Konfiguration (CONFIG-Objekt)

```javascript
const CONFIG = {
    STATION_NAME: 'Meine Wetterstation',
    LATITUDE: -32.1559,
    LONGITUDE: -64.7916,
    ALTITUDE: 1000,
    TIMEZONE: 'America/Argentina/Cordoba',
    ECOWITT_APP_KEY: '...',
    ECOWITT_API_KEY: '...',
    ECOWITT_MAC: 'AA:BB:CC:DD:EE:FF',
    UNITS: 'metric',       // oder 'imperial'
    LANGUAGE: 'de',         // 'de', 'en', 'es'
    REFRESH_INTERVAL: 300,  // Sekunden
    SHOW_OFFGRID: true,
    WIND_THRESHOLD_KMH: 10,
};
```

## APIs

### Open-Meteo (Vorhersage + Historie)
- Doku: https://open-meteo.com/en/docs
- Kein API-Key nötig
- Forecast-Endpoint: `https://api.open-meteo.com/v1/forecast`
- Koordinaten und Timezone aus CONFIG lesen
- Beispiel-Call:
```
https://api.open-meteo.com/v1/forecast?latitude={CONFIG.LATITUDE}&longitude={CONFIG.LONGITUDE}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,windgusts_10m_max,weathercode,sunrise,sunset,sunshine_duration&hourly=temperature_2m,relativehumidity_2m,apparent_temperature,windspeed_10m,winddirection_10m,windgusts_10m,precipitation,precipitation_probability,pressure_msl,cloudcover,weathercode&timezone={CONFIG.TIMEZONE}&forecast_days=7&past_days=7
```

### Ecowitt API v3 (Live-Daten)
- Doku: https://doc.ecowitt.net/web/#/apiv3
- Endpoint: `https://api.ecowitt.net/api/v3/device/real_time`
- Parameter: `application_key`, `api_key`, `mac` aus CONFIG
- Call-back Parameter: `outdoor`, `indoor`, `solar_and_uvi`, `rainfall`, `wind`, `pressure`
- CORS: die Ecowitt-API erlaubt Cross-Origin-Requests. Falls nicht, Fallback auf nur Open-Meteo.

## Design-Vorgaben

- **Dark Theme** (Hintergrund #1a1a2e oder ähnlich, Akzentfarbe Cyan/Teal #00d4aa)
- **Responsive**: Handy (360px) bis Desktop (1400px+)
- **Sprache**: nach CONFIG.LANGUAGE (Wochentage, Labels, Einheiten). Mindestens DE und EN.
- **Einheiten**: metric (°C, km/h, mm, hPa) oder imperial (°F, mph, in, inHg)
- **Wetter-Icons**: WMO-Weathercodes in SVG-Icons oder Emoji (Sonne, Wolken, Regen, Gewitter, Schnee, Nebel). Tag/Nacht-Variante basierend auf Sunrise/Sunset.
- Chart-Library: Chart.js via CDN (`https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`)

## Sektionen des Dashboards

### 1. Header
- Stationsname aus CONFIG.STATION_NAME
- Letzte Aktualisierung (Timestamp)
- Sonnenauf-/untergang (aus Open-Meteo)

### 2. Aktuell (Live-Daten von Ecowitt, Fallback auf Open-Meteo "current_weather")
- Außentemperatur + gefühlte Temperatur
- Luftfeuchtigkeit + Taupunkt
- Windgeschwindigkeit + Böen + Richtung (mit Kompass-Anzeige als SVG)
- Luftdruck (relativ)
- Solar-Strahlung + UV-Index
- Regenrate + Tagesregen

### 3. 7-Tage-Vorhersage (Open-Meteo)
- Kartenreihe: Tag, Icon, Min/Max-Temp, Niederschlagssumme, max. Wind
- Horizontal scrollbar auf Handy

### 4. Stündliche Vorhersage (nächste 24h)
- Kompakter Streifen: Stunde, Icon, Temp, Regenwahrscheinlichkeit, Wind

### 5. Diagramme (letzte 7 Tage + nächste 7 Tage)
- Temperatur (Min/Max als Bereichs-Chart)
- Wind (Durchschnitt + Böen)
- Niederschlag (Balken)
- Luftdruck (Linie)

### 6. Off-Grid-Sektion (nur wenn CONFIG.SHOW_OFFGRID === true)
- Sonnenstunden pro Tag (aus `sunshine_duration`)
- Stunden mit Wind > CONFIG.WIND_THRESHOLD_KMH pro Tag
- Kumulative Regen-Prognose (7 Tage, mm)
- Einfache farbliche Bewertung: grün = gut, gelb = mäßig, rot = wenig

## Technische Anforderungen

- Alle API-Calls client-seitig (fetch), kein Backend
- Config aus separater config.js (nicht inline)
- Graceful Degradation: Ecowitt nicht erreichbar → nur Open-Meteo. Config fehlt → Setup-Hinweis.
- Error-Handling: bei API-Fehlern Hinweis-Banner, kein Crash
- Auto-Refresh laut CONFIG.REFRESH_INTERVAL
- LocalStorage für letzte erfolgreiche Daten (Offline-Fallback)
- Sauberer, kommentierter Code (Englisch), bereit für Open-Source-Beiträge
- Keine externen Fonts (System-Font-Stack)
- Keine Tracking-Scripts, keine Analytics, keine Cookies

## Dateistruktur

```
index.html          ← Dashboard
config.js           ← Nutzerkonfiguration (nicht im Repo)
config.example.js   ← Vorlage
```

## Qualitätskriterien

- [ ] Dashboard lädt ohne config.js und zeigt Setup-Hinweis
- [ ] Dashboard lädt mit config.js und zeigt Daten
- [ ] Ecowitt-API-Fehler blendet Banner ein, Rest funktioniert
- [ ] Responsiv auf 360px und 1400px
- [ ] Off-Grid-Sektion erscheint nur wenn SHOW_OFFGRID true
- [ ] Alle Texte in CONFIG.LANGUAGE
- [ ] Charts rendern korrekt für 14-Tage-Zeitraum
- [ ] Auto-Refresh funktioniert ohne Memory-Leak
- [ ] Kein console.error im Normalbetrieb

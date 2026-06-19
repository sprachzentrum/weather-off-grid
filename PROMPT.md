# Prompt: Wettervorhersage-Dashboard für Ecowitt-Station "Wetter El Durazno"

## Aufgabe

Baue ein lokales Wetter-Dashboard (Single-Page HTML + JS, kein Framework), das:

1. **Live-Daten** von meiner Ecowitt-Wetterstation anzeigt (via Ecowitt API)
2. **7-Tage-Vorhersage** von Open-Meteo einbindet (kostenlos, kein API-Key nötig)
3. **Historische Daten** als Diagramme darstellt (Temperatur, Wind, Regen der letzten 7 Tage)

## Standort & Station

- Koordinaten: **32.1559° S, 64.7916° W** (Villa Yacanto, Calamuchita, Córdoba, Argentinien)
- Höhe: ca. **1.000 m ü. M.**
- Ecowitt-Station: MAC `C4:5B:BE:6E:46:15`, Name "Wetter El Durazno"
- Firmware: EasyWeatherV1.7.6

## APIs

### Open-Meteo (Vorhersage + Historie)
- Doku: https://open-meteo.com/en/docs
- Kein API-Key nötig
- Forecast-Endpoint: `https://api.open-meteo.com/v1/forecast`
- Beispiel-Call:
```
https://api.open-meteo.com/v1/forecast?latitude=-32.1559&longitude=-64.7916&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,windgusts_10m_max,weathercode&hourly=temperature_2m,relativehumidity_2m,windspeed_10m,winddirection_10m,precipitation,pressure_msl,cloudcover&timezone=America/Argentina/Cordoba&forecast_days=7&past_days=7
```
- Wichtige Felder: `weathercode` (WMO-Codes für Icons), `sunrise`, `sunset`

### Ecowitt API (Live-Daten)
- Doku: https://doc.ecowitt.net/web/#/apiv3
- Endpoint: `https://api.ecowitt.net/api/v3/device/real_time`
- Benötigt `application_key` und `api_key` (aus Ecowitt-Dashboard unter api.ecowitt.net)
- MAC-Adresse: `C4:5B:BE:6E:46:15`
- Falls die API-Keys noch nicht vorhanden sind: zeige mir eine Anleitung, wie ich sie im Ecowitt-Dashboard generiere, und verwende Platzhalter (`ECOWITT_APP_KEY`, `ECOWITT_API_KEY`) im Code

## Design-Vorgaben

- **Dark Theme** passend zur Ecowitt-App (Hintergrund #1a1a2e oder ähnlich dunkel, Akzentfarbe Cyan/Teal)
- **Responsive**: muss auf Handy (360px) und Desktop (1200px+) funktionieren
- **Sprache**: Deutsch (Wochentage, Beschriftungen, Einheiten)
- **Einheiten**: °C, km/h, mm, hPa
- **Wetter-Icons**: WMO-Weathercodes in passende Emoji oder SVG-Icons übersetzen (Sonne, Wolken, Regen, Gewitter, Schnee, Nebel)
- **Kein Build-System**: reines HTML + CSS + Vanilla JS in einer einzigen Datei
- Chart-Library: Chart.js via CDN (`https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`)

## Sektionen des Dashboards

### 1. Header
- Stationsname "Wetter El Durazno"
- Letzte Aktualisierung (Timestamp)
- Sonnenauf-/untergang (aus Open-Meteo)

### 2. Aktuell (Live-Daten von Ecowitt, Fallback auf Open-Meteo "current")
- Außentemperatur + gefühlte Temperatur
- Luftfeuchtigkeit + Taupunkt
- Windgeschwindigkeit + Böen + Richtung (mit Kompass-Anzeige)
- Luftdruck (relativ)
- Solar-Strahlung + UV-Index
- Regenrate + Tagesregen

### 3. 7-Tage-Vorhersage (Open-Meteo)
- Kartenreihe: Tag, Icon, Min/Max-Temp, Niederschlagssumme, Wind
- Horizontal scrollbar auf Handy

### 4. Stündliche Vorhersage (nächste 24h)
- Kompakter Streifen mit Stunde, Icon, Temp, Regenwahrscheinlichkeit

### 5. Diagramme (letzte 7 Tage + nächste 7 Tage)
- Temperatur (Min/Max als Bereichs-Chart)
- Wind (Durchschnitt + Böen)
- Niederschlag (Balken)
- Luftdruck (Linie)

### 6. Off-Grid-Relevanz (Bonus-Sektion)
- Solarertrag-Prognose: geschätzte Sonnenstunden pro Tag (aus `sunshine_duration` oder `cloudcover`)
- Wind-Potenzial: Stunden mit Wind > 10 km/h pro Tag (relevant für VAWT)
- Regen-Prognose: kumulative mm für Mikro-Hydro-Abschätzung

## Technische Anforderungen

- Alle API-Calls client-seitig (fetch), kein Backend nötig
- Ecowitt-API-Keys als Konstanten oben in der Datei (leicht austauschbar)
- Auto-Refresh alle 5 Minuten
- Graceful Degradation: wenn Ecowitt-API nicht erreichbar, nur Open-Meteo-Daten anzeigen
- Error-Handling: bei API-Fehlern Hinweis anzeigen, nicht crashen
- LocalStorage für letzte erfolgreiche Daten (Offline-Fallback)

## Dateistruktur

Eine einzige Datei: `wetter-el-durazno.html`

## Deployment

Die Datei soll statisch auf meinem Server gehostet werden können (einfaches Kopieren). Kein Node.js, kein Build-Step.

/**
 * Wetter Dashboard - Konfiguration
 *
 * Kopiere diese Datei nach config.js und passe die Werte an:
 *   cp config.example.js config.js
 *
 * WICHTIG: config.js niemals committen (steht in .gitignore),
 * da sie deine API-Keys enthält.
 */

const CONFIG = {

    // ── Station ──────────────────────────────────────────
    STATION_NAME: 'Meine Wetterstation',
    LATITUDE: -32.1559,       // negativ = Süd
    LONGITUDE: -64.7916,      // negativ = West
    ALTITUDE: 1000,           // Meter über Meeresspiegel
    TIMEZONE: 'America/Argentina/Cordoba',  // IANA Timezone

    // ── Ecowitt API ──────────────────────────────────────
    // Keys generieren unter https://api.ecowitt.net
    ECOWITT_APP_KEY: '',      // Application Key
    ECOWITT_API_KEY: '',      // API Key
    ECOWITT_MAC: '',          // MAC-Adresse der Station (z. B. 'AA:BB:CC:DD:EE:FF')

    // ── Anzeige ──────────────────────────────────────────
    UNITS: 'metric',          // 'metric' (°C, km/h, mm) oder 'imperial' (°F, mph, in)
    LANGUAGE: 'de',           // 'de', 'en', oder 'es'

    // ── Verhalten ────────────────────────────────────────
    REFRESH_INTERVAL: 300,    // Auto-Refresh in Sekunden (min. 60)

    // ── Off-Grid-Sektion ─────────────────────────────────
    SHOW_OFFGRID: true,       // Off-Grid-Kennzahlen anzeigen
    WIND_THRESHOLD_KMH: 10,  // Ab welcher Windgeschwindigkeit Wind "nutzbar" ist
};

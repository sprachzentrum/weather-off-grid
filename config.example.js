/**
 * Wetter Dashboard - Frontend-Konfiguration
 *
 * Kopiere diese Datei nach frontend/config.js:
 *   cp config.example.js frontend/config.js
 *
 * WICHTIG: frontend/config.js niemals committen,
 * da sie deine Backend-URL und Einstellungen enthaelt.
 */

const CONFIG = {

    // ── Station ──────────────────────────────────────
    STATION_NAME: 'Meine Wetterstation',

    // ── Backend ──────────────────────────────────────
    BACKEND_URL: 'http://localhost:8000/api',

    // ── Anzeige ──────────────────────────────────────
    UNITS: 'metric',          // 'metric' oder 'imperial'
    LANGUAGE: 'de',           // 'de', 'en', 'es'
    REFRESH_INTERVAL: 300,    // Auto-Refresh in Sekunden (min. 60)

    // ── Module ───────────────────────────────────────
    SHOW_BATTERY: true,       // Batterie-Widget (braucht Growatt im Backend)
    SHOW_OFFGRID: true,       // Off-Grid-Kennzahlen (Solar/Wind/Hydro)
    SHOW_MICROCLIMATE: true,  // Mikroklima-Korrekturen anzeigen

    // ── Energie ──────────────────────────────────────
    BATTERY_CAPACITY_KWH: 9.6, // Gesamtkapazitaet fuer SOC-Anzeige
    PV_KWP: 3.6,               // Installierte PV-Leistung in kWp
    PV_EFFICIENCY: 0.75,        // System-Wirkungsgrad (Kabel, MPPT, Temp, Verschmutzung; 0.70-0.85 typisch)

    // ── Off-Grid ─────────────────────────────────────
    WIND_THRESHOLD_KMH: 10,  // Ab welcher Windgeschwindigkeit "nutzbar"
};

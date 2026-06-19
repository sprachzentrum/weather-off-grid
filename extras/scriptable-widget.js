/**
 * Weather Off-Grid - iOS Scriptable Widget
 *
 * Zeigt aktuelle Wetterdaten und Batterie-Status auf dem iOS Homescreen.
 *
 * Setup:
 * 1. Scriptable App installieren (https://scriptable.app)
 * 2. Dieses Script in Scriptable einfuegen
 * 3. BACKEND_URL unten anpassen
 * 4. Widget auf Homescreen hinzufuegen (Scriptable Widget)
 *
 * Unterstuetzte Widget-Groessen: Small, Medium
 */

// ── Konfiguration ────────────────────────────────────
const BACKEND_URL = 'https://weather.example.com/api';
const STATION_NAME = 'Wetter';
// ─────────────────────────────────────────────────────

async function createWidget() {
    const widget = new ListWidget();
    widget.backgroundColor = new Color('#0f0f1a');

    try {
        const current = await fetchJSON(`${BACKEND_URL}/current`);
        const battery = await fetchJSON(`${BACKEND_URL}/battery`);

        // Header
        const title = widget.addText(STATION_NAME);
        title.font = Font.boldSystemFont(12);
        title.textColor = new Color('#00d4aa');

        widget.addSpacer(4);

        // Temperatur
        const temp = widget.addText(`${current.temperature_outdoor}°C`);
        temp.font = Font.boldSystemFont(28);
        temp.textColor = Color.white();

        // Wind + Regen
        const wind = widget.addText(`💨 ${current.wind_speed} km/h  🌧 ${current.rain_daily} mm`);
        wind.font = Font.systemFont(11);
        wind.textColor = new Color('#aaaaaa');

        widget.addSpacer(4);

        // Batterie
        if (battery && battery.soc !== undefined) {
            const socColor = battery.soc > 50 ? '#00d4aa' : battery.soc > 20 ? '#f4a261' : '#e63946';
            const bat = widget.addText(`🔋 ${battery.soc}%  ⚡ ${battery.pv_power}W`);
            bat.font = Font.systemFont(11);
            bat.textColor = new Color(socColor);
        }

    } catch (e) {
        const err = widget.addText('Keine Verbindung');
        err.font = Font.systemFont(12);
        err.textColor = new Color('#e63946');
    }

    return widget;
}

async function fetchJSON(url) {
    const req = new Request(url);
    req.timeoutInterval = 10;
    return await req.loadJSON();
}

const widget = await createWidget();
if (config.runsInWidget) {
    Script.setWidget(widget);
} else {
    widget.presentSmall();
}
Script.complete();

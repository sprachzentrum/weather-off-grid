/**
 * Weather Off-Grid - iOS Scriptable Widget
 *
 * Shows current weather + battery status on the iOS homescreen, matching the
 * backend's actual JSON shape:
 *   GET /current  -> { weather:{...}, battery:{...}, sun:{...}, weather_code, is_day }
 *   GET /forecast -> { days:[ {date, weathercode, temp_min, temp_max, corrected:{...}} ] }
 *
 * Setup:
 *   1. Install Scriptable (https://scriptable.app)
 *   2. Paste this script into a new Scriptable script
 *   3. Set BACKEND_URL below to your backend's /api base
 *   4. Add a Scriptable widget to the homescreen (Small or Medium)
 *
 * Small  widget: temperature, condition, wind/rain, battery.
 * Medium widget: adds the next forecast days.
 */

// ── Configuration ────────────────────────────────────────
const BACKEND_URL = 'https://weather.example.com/api';
const STATION_NAME = 'Wetter El Durazno';
// ─────────────────────────────────────────────────────────

const ACCENT = new Color('#00d4aa');
const BG = new Color('#0f0f1a');
const DIM = new Color('#9a9ac4');

// WMO weather code -> emoji (day/night aware).
function wmoIcon(code, isDay) {
  const map = {
    0: isDay ? '☀️' : '🌙', 1: isDay ? '🌤️' : '🌙', 2: '⛅', 3: '☁️',
    45: '🌫️', 48: '🌫️', 51: '🌦️', 53: '🌦️', 55: '🌧️',
    61: '🌦️', 63: '🌧️', 65: '🌧️', 71: '🌨️', 73: '🌨️', 75: '❄️',
    80: '🌦️', 81: '🌧️', 82: '⛈️', 95: '⛈️', 96: '⛈️', 99: '⛈️',
  };
  return map[code] || '❓';
}

function num(v, d = 0) {
  return (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
}

async function fetchJSON(path) {
  const req = new Request(BACKEND_URL + path);
  req.timeoutInterval = 12;
  return await req.loadJSON();
}

async function createWidget() {
  const widget = new ListWidget();
  widget.backgroundColor = BG;
  widget.setPadding(14, 14, 14, 14);
  const family = config.widgetFamily || 'small';

  let current;
  let forecast = null;
  try {
    current = await fetchJSON('/current');
    if (family === 'medium') {
      try { forecast = await fetchJSON('/forecast'); } catch (e) { /* optional */ }
    }
  } catch (e) {
    const err = widget.addText('⚠︎ Keine Verbindung');
    err.font = Font.systemFont(13);
    err.textColor = new Color('#e63946');
    return widget;
  }

  const w = current.weather || {};
  const bat = current.battery || null;
  const icon = wmoIcon(current.weather_code, current.is_day !== 0);

  if (family === 'medium') {
    return buildMedium(widget, w, bat, icon, forecast);
  }
  return buildSmall(widget, w, bat, icon);
}

function header(stack) {
  const title = stack.addText(STATION_NAME);
  title.font = Font.boldSystemFont(12);
  title.textColor = ACCENT;
  title.lineLimit = 1;
}

function batteryLine(stack, bat) {
  if (!bat || bat.battery_soc === undefined || bat.battery_soc === null) return;
  const soc = bat.battery_soc;
  const color = soc > 50 ? '#00d4aa' : soc > 20 ? '#f4a261' : '#e63946';
  const power = bat.battery_power || 0;
  const arrow = power > 10 ? '▲' : power < -10 ? '▼' : '·';
  const line = stack.addText(`🔋 ${num(soc)}% ${arrow}  ☀︎ ${num(bat.pv_power)}W`);
  line.font = Font.systemFont(11);
  line.textColor = new Color(color);
}

function buildSmall(widget, w, bat, icon) {
  header(widget);
  widget.addSpacer(6);

  const row = widget.addStack();
  const t = row.addText(`${num(w.temperature_outdoor, 1)}°`);
  t.font = Font.boldSystemFont(30);
  t.textColor = Color.white();
  row.addSpacer(4);
  const ic = row.addText(icon);
  ic.font = Font.systemFont(26);

  const wind = widget.addText(`💨 ${num(w.wind_speed)} km/h   🌧 ${num(w.rain_daily, 1)} mm`);
  wind.font = Font.systemFont(11);
  wind.textColor = DIM;

  widget.addSpacer(6);
  batteryLine(widget, bat);
  return widget;
}

function buildMedium(widget, w, bat, icon, forecast) {
  const top = widget.addStack();
  top.layoutHorizontally();

  // Left: current conditions
  const left = top.addStack();
  left.layoutVertically();
  header(left);
  left.addSpacer(4);
  const t = left.addText(`${num(w.temperature_outdoor, 1)}° ${icon}`);
  t.font = Font.boldSystemFont(26);
  t.textColor = Color.white();
  const feels = left.addText(`gefühlt ${num(w.temperature_feels_like, 1)}°`);
  feels.font = Font.systemFont(10);
  feels.textColor = DIM;
  const wind = left.addText(`💨 ${num(w.wind_speed)} km/h`);
  wind.font = Font.systemFont(11);
  wind.textColor = DIM;
  const rain = left.addText(`🌧 ${num(w.rain_daily, 1)} mm   📊 ${num(w.pressure_relative)} hPa`);
  rain.font = Font.systemFont(11);
  rain.textColor = DIM;
  left.addSpacer(4);
  batteryLine(left, bat);

  top.addSpacer();

  // Right: next 3 forecast days (microclimate-corrected when available)
  if (forecast && forecast.days && forecast.days.length) {
    const right = top.addStack();
    right.layoutVertically();
    const days = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
    forecast.days.slice(0, 3).forEach((d) => {
      const c = d.corrected || {};
      const tmax = c.temp_max != null ? c.temp_max : d.temp_max;
      const tmin = c.temp_min != null ? c.temp_min : d.temp_min;
      const dow = days[new Date(d.date + 'T12:00').getDay()];
      const line = right.addText(`${dow} ${wmoIcon(d.weathercode, true)} ${num(tmax)}°/${num(tmin)}°`);
      line.font = Font.systemFont(12);
      line.textColor = Color.white();
      right.addSpacer(3);
    });
  }
  return widget;
}

const widget = await createWidget();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();

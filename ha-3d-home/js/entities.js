// Darstellung von Home-Assistant-Entitäten: Icons, Zustände und Steuerelemente.

export const domainOf = (id) => id.split('.')[0];

// Domains, die automatisch über HA-Bereiche einem Raum zugeordnet werden.
export const AUTO_DOMAINS = new Set([
  'light', 'switch', 'sensor', 'binary_sensor', 'climate', 'cover', 'media_player', 'fan', 'lock',
  'camera', 'vacuum', 'scene', 'script', 'button', 'input_button', 'input_boolean', 'input_number',
  'input_select', 'number', 'select', 'humidifier', 'water_heater', 'siren', 'valve', 'alarm_control_panel',
]);

export const TOGGLE_DOMAINS = new Set(['light', 'switch', 'input_boolean', 'fan', 'humidifier', 'siren', 'automation']);
const ACTIVATE = { scene: ['scene', 'turn_on'], script: ['script', 'turn_on'], button: ['button', 'press'], input_button: ['input_button', 'press'] };

const GROUPS = [
  ['Licht', ['light']],
  ['Klima', ['climate', 'fan', 'humidifier', 'water_heater']],
  ['Rollläden & Schlösser', ['cover', 'lock', 'valve']],
  ['Schalter', ['switch', 'input_boolean', 'siren']],
  ['Medien', ['media_player', 'camera']],
  ['Szenen & Skripte', ['scene', 'script', 'button', 'input_button']],
  ['Einstellungen', ['input_number', 'number', 'input_select', 'select', 'vacuum', 'alarm_control_panel']],
  ['Sensoren', ['sensor', 'binary_sensor']],
];

export function groupOf(id) {
  const d = domainOf(id);
  return GROUPS.find(([, ds]) => ds.includes(d))?.[0] || 'Sonstiges';
}
export const GROUP_ORDER = [...GROUPS.map((g) => g[0]), 'Sonstiges'];

export const nameOf = (s) => s?.attributes?.friendly_name || s?.entity_id || '?';

export const isUnavailable = (s) => !s || s.state === 'unavailable' || s.state === 'unknown' && !['scene', 'script', 'button', 'input_button'].includes(domainOf(s.entity_id));

/** „Aktiv" = soll in der 3D-Ansicht auffallen. */
export function isActive(s) {
  if (!s) return false;
  const d = domainOf(s.entity_id);
  switch (d) {
    case 'light': case 'switch': case 'fan': case 'input_boolean': case 'humidifier': case 'siren':
      return s.state === 'on';
    case 'binary_sensor': return s.state === 'on';
    case 'cover': return s.state === 'open' || s.state === 'opening' || s.state === 'closing';
    case 'media_player': return s.state === 'playing';
    case 'lock': return s.state === 'unlocked' || s.state === 'open';
    case 'climate': return s.attributes.hvac_action === 'heating' || s.attributes.hvac_action === 'cooling';
    case 'vacuum': return s.state === 'cleaning' || s.state === 'returning';
    default: return false;
  }
}

/** Alarmzustand (rot pulsierend), z. B. Rauch, Wasser, offene Haustür. */
export function isAlert(s) {
  if (!s || domainOf(s.entity_id) !== 'binary_sensor' || s.state !== 'on') return false;
  return ['smoke', 'gas', 'carbon_monoxide', 'moisture', 'problem', 'safety', 'tamper'].includes(s.attributes.device_class);
}

export function iconOf(s) {
  const d = domainOf(s.entity_id);
  const a = s.attributes || {};
  const dc = a.device_class;
  const on = s.state === 'on';
  switch (d) {
    case 'light': return '💡';
    case 'switch': return dc === 'outlet' ? '🔌' : '🔌';
    case 'input_boolean': return '🔘';
    case 'fan': return '🌀';
    case 'climate': return '🔥';
    case 'water_heater': return '🚿';
    case 'humidifier': return '💨';
    case 'cover': return dc === 'garage' ? '🚗' : dc === 'door' || dc === 'gate' ? '🚪' : '🪟';
    case 'lock': return s.state === 'locked' ? '🔒' : '🔓';
    case 'valve': return '🚰';
    case 'media_player': return dc === 'tv' ? '📺' : dc === 'speaker' ? '🔊' : '🎵';
    case 'camera': return '📷';
    case 'vacuum': return '🤖';
    case 'scene': return '🎬';
    case 'script': return '📜';
    case 'automation': return '⚙️';
    case 'button': case 'input_button': return '⏺️';
    case 'input_number': case 'number': return '🎚️';
    case 'input_select': case 'select': return '📋';
    case 'alarm_control_panel': return '🛡️';
    case 'siren': return '🚨';
    case 'person': return '👤';
    case 'binary_sensor':
      switch (dc) {
        case 'door': case 'garage_door': case 'opening': return '🚪';
        case 'window': return '🪟';
        case 'motion': case 'occupancy': case 'presence': return on ? '🏃' : '🧍';
        case 'smoke': case 'gas': case 'carbon_monoxide': return '🔥';
        case 'moisture': return '💧';
        case 'battery': return '🪫';
        case 'light': return '☀️';
        case 'sound': return '🔉';
        case 'vibration': return '📳';
        default: return on ? '🟢' : '⚪';
      }
    case 'sensor':
      switch (dc) {
        case 'temperature': return '🌡️';
        case 'humidity': case 'moisture': return '💧';
        case 'power': case 'energy': case 'current': case 'voltage': return '⚡';
        case 'battery': return '🔋';
        case 'illuminance': return '☀️';
        case 'carbon_dioxide': case 'pm25': case 'pm10': case 'volatile_organic_compounds': return '🌫️';
        case 'pressure': return '🧭';
        default: return '📈';
      }
    default: return '⚙️';
  }
}

const STATE_DE = {
  on: 'An', off: 'Aus', unavailable: 'Nicht verfügbar', unknown: 'Unbekannt',
  open: 'Offen', closed: 'Geschlossen', opening: 'Öffnet …', closing: 'Schließt …',
  locked: 'Verriegelt', unlocked: 'Entriegelt', locking: 'Verriegelt …', unlocking: 'Entriegelt …', jammed: 'Blockiert',
  playing: 'Spielt', paused: 'Pausiert', idle: 'Bereit', standby: 'Standby', buffering: 'Lädt …',
  heat: 'Heizen', cool: 'Kühlen', auto: 'Automatik', heat_cool: 'Heizen/Kühlen', dry: 'Entfeuchten', fan_only: 'Lüften',
  heating: 'heizt', cooling: 'kühlt', drying: 'entfeuchtet', preheating: 'heizt vor',
  home: 'Zuhause', not_home: 'Abwesend',
  cleaning: 'Saugt', docked: 'In Station', returning: 'Fährt zurück', error: 'Fehler',
  armed_home: 'Scharf (Zuhause)', armed_away: 'Scharf (Abwesend)', armed_night: 'Scharf (Nacht)', disarmed: 'Unscharf', triggered: 'Ausgelöst',
};

const BINARY_DE = {
  door: ['Offen', 'Geschlossen'], garage_door: ['Offen', 'Geschlossen'], window: ['Offen', 'Geschlossen'], opening: ['Offen', 'Geschlossen'],
  motion: ['Bewegung', 'Keine Bewegung'], occupancy: ['Belegt', 'Frei'], presence: ['Anwesend', 'Abwesend'],
  moisture: ['Nass!', 'Trocken'], smoke: ['Rauch!', 'OK'], gas: ['Gas!', 'OK'], carbon_monoxide: ['CO!', 'OK'],
  battery: ['Niedrig', 'Normal'], problem: ['Problem', 'OK'], connectivity: ['Verbunden', 'Getrennt'], plug: ['Eingesteckt', 'Ausgesteckt'],
  lock: ['Entriegelt', 'Verriegelt'], light: ['Hell', 'Dunkel'], vibration: ['Vibration', 'Ruhig'], sound: ['Geräusch', 'Ruhig'],
};

export function stateText(s) {
  if (!s) return '–';
  const d = domainOf(s.entity_id);
  const a = s.attributes || {};
  if (s.state === 'unavailable') return STATE_DE.unavailable;
  if (d === 'sensor' || d === 'input_number' || d === 'number') {
    const n = parseFloat(s.state);
    const v = Number.isFinite(n) && /^-?[\d.]+$/.test(s.state) ? n.toLocaleString('de-DE', { maximumFractionDigits: 2 }) : s.state;
    return a.unit_of_measurement ? `${v} ${a.unit_of_measurement}` : v;
  }
  if (d === 'binary_sensor') {
    const pair = BINARY_DE[a.device_class];
    if (pair) return s.state === 'on' ? pair[0] : s.state === 'off' ? pair[1] : STATE_DE[s.state] || s.state;
  }
  if (d === 'light' && s.state === 'on' && a.brightness != null) return `An · ${Math.round((a.brightness / 255) * 100)} %`;
  if (d === 'cover' && a.current_position != null && (s.state === 'open' || s.state === 'closed')) {
    return a.current_position === 100 ? 'Offen' : a.current_position === 0 ? 'Geschlossen' : `${a.current_position} % offen`;
  }
  if (d === 'climate') {
    const cur = a.current_temperature != null ? `${fmtNum(a.current_temperature)} °C` : '';
    const act = a.hvac_action && STATE_DE[a.hvac_action] ? ` · ${STATE_DE[a.hvac_action]}` : '';
    return `${STATE_DE[s.state] || s.state}${cur ? ' · ' + cur : ''}${act}`;
  }
  if (d === 'fan' && s.state === 'on' && a.percentage != null) return `An · ${Math.round(a.percentage)} %`;
  if (d === 'media_player' && a.media_title && (s.state === 'playing' || s.state === 'paused')) {
    return `${STATE_DE[s.state]} · ${a.media_title}`;
  }
  if (['scene', 'script', 'button', 'input_button'].includes(d)) {
    if (d === 'script' && s.state === 'on') return 'Läuft …';
    return s.last_changed && s.state !== 'unknown' && d !== 'script' ? `Zuletzt: ${timeAgo(s.state)}` : '';
  }
  return STATE_DE[s.state] || s.state;
}

const fmtNum = (n) => Number(n).toLocaleString('de-DE', { maximumFractionDigits: 1 });

function timeAgo(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '–';
  const min = Math.round((Date.now() - t) / 60000);
  if (min < 1) return 'gerade eben';
  if (min < 60) return `vor ${min} Min.`;
  const h = Math.round(min / 60);
  if (h < 24) return `vor ${h} Std.`;
  return new Date(t).toLocaleDateString('de-DE');
}

/** Primäre Aktion beim Klick auf das Gerät in 3D (oder null). */
export function primaryAction(s) {
  if (!s || s.state === 'unavailable') return null;
  const d = domainOf(s.entity_id);
  if (TOGGLE_DOMAINS.has(d)) return [d, 'toggle', {}];
  if (ACTIVATE[d]) return [...ACTIVATE[d], {}];
  if (d === 'cover') {
    const closedish = s.state === 'closed' || s.state === 'closing';
    return ['cover', closedish ? 'open_cover' : 'close_cover', {}];
  }
  if (d === 'lock') return ['lock', s.state === 'locked' ? 'unlock' : 'lock', {}];
  if (d === 'media_player') return ['media_player', 'media_play_pause', {}];
  return null;
}

/** Farbe für Marker / Raum-Glühen bei Lichtern. */
export function lightColor(s) {
  const a = s?.attributes || {};
  if (Array.isArray(a.rgb_color)) return `rgb(${a.rgb_color.join(',')})`;
  return '#ffcf6e';
}

// ------------------------------------------------------------------
// Steuerelemente (DOM). `call(domain, service, data)` führt den Dienst aus.
// ------------------------------------------------------------------

const h = (tag, props = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
};
export { h };

function slider({ min, max, step = 1, value, label, fmt = (v) => v, onChange }) {
  const out = h('span', { class: 'slider-val', text: fmt(value) });
  const input = h('input', { type: 'range', min, max, step, value });
  input.addEventListener('input', () => { out.textContent = fmt(+input.value); });
  input.addEventListener('change', () => onChange(+input.value));
  return h('label', { class: 'ctl-row' }, h('span', { class: 'ctl-label', text: label }), input, out);
}

function btn(text, onClick, cls = '') {
  return h('button', { class: `btn ${cls}`, type: 'button', onclick: onClick, text });
}

function toggleSwitch(on, onClick, disabled) {
  return h('button', {
    class: `switch ${on ? 'on' : ''}`, type: 'button', role: 'switch', 'aria-checked': String(on), disabled, onclick: onClick, title: on ? 'Ausschalten' : 'Einschalten',
  }, h('span'));
}

export function hsToHex(hs) {
  // HA nutzt HSV mit voller Helligkeit (Helligkeit wird separat gesteuert).
  const [hh, ss] = hs; const s = ss / 100;
  const f = (n) => {
    const k = (n + hh / 60) % 6;
    return Math.round(255 * (1 - s * Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return '#' + [f(5), f(3), f(1)].map((x) => x.toString(16).padStart(2, '0')).join('');
}
export function hexToHs(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255, g = parseInt(hex.slice(3, 5), 16) / 255, b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), dlt = max - min;
  let hh = 0;
  if (dlt) {
    if (max === r) hh = ((g - b) / dlt) % 6; else if (max === g) hh = (b - r) / dlt + 2; else hh = (r - g) / dlt + 4;
  }
  hh = Math.round(hh * 60); if (hh < 0) hh += 360;
  const s = max === 0 ? 0 : dlt / max;
  return [hh, Math.round(s * 100)];
}

/**
 * Baut die Gerätekarte für das Seitenpanel.
 * @param s       state object
 * @param call    (domain, service, data) => Promise
 * @param ctx     { baseUrl }
 */
export function renderControls(s, call, ctx = {}) {
  const d = domainOf(s.entity_id);
  const a = s.attributes || {};
  const unavailable = s.state === 'unavailable';
  const body = h('div', { class: 'ctl' });
  const head = [];

  if (TOGGLE_DOMAINS.has(d)) head.push(toggleSwitch(s.state === 'on', () => call(d, 'toggle'), unavailable));

  if (unavailable) return { head, body };

  switch (d) {
    case 'light': {
      const modes = a.supported_color_modes || [];
      const dimmable = modes.some((m) => m !== 'onoff');
      if (dimmable) {
        const pct = a.brightness != null ? Math.round((a.brightness / 255) * 100) : 0;
        body.append(slider({ min: 1, max: 100, value: s.state === 'on' ? pct : 0, label: 'Helligkeit', fmt: (v) => `${v} %`, onChange: (v) => call('light', 'turn_on', { brightness_pct: v }) }));
      }
      if (modes.includes('color_temp') && a.min_color_temp_kelvin) {
        body.append(slider({
          min: a.min_color_temp_kelvin, max: a.max_color_temp_kelvin, step: 50, value: a.color_temp_kelvin || a.min_color_temp_kelvin,
          label: 'Farbtemp.', fmt: (v) => `${v} K`, onChange: (v) => call('light', 'turn_on', { color_temp_kelvin: v }),
        }));
      }
      if (modes.some((m) => ['hs', 'rgb', 'rgbw', 'rgbww', 'xy'].includes(m))) {
        const input = h('input', { type: 'color', value: a.hs_color ? hsToHex(a.hs_color) : '#ffcf6e' });
        input.addEventListener('change', () => call('light', 'turn_on', { hs_color: hexToHs(input.value) }));
        const presets = ['#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#00c7be', '#007aff', '#af52de', '#ff2d55'].map((c) =>
          h('button', { class: 'swatch', type: 'button', style: `background:${c}`, title: c, onclick: () => call('light', 'turn_on', { hs_color: hexToHs(c) }) }));
        body.append(h('div', { class: 'ctl-row' }, h('span', { class: 'ctl-label', text: 'Farbe' }), h('div', { class: 'swatches' }, ...presets, input)));
      }
      break;
    }
    case 'fan':
      if (a.percentage != null) {
        body.append(slider({ min: 0, max: 100, step: a.percentage_step || 1, value: a.percentage || 0, label: 'Stufe', fmt: (v) => `${Math.round(v)} %`, onChange: (v) => call('fan', 'set_percentage', { percentage: Math.round(v) }) }));
      }
      break;
    case 'cover': {
      const f = a.supported_features ?? 15;
      const row = h('div', { class: 'btn-row' });
      if (f & 1) row.append(btn('▲ Auf', () => call('cover', 'open_cover')));
      if (f & 8) row.append(btn('■ Stopp', () => call('cover', 'stop_cover')));
      if (f & 2) row.append(btn('▼ Zu', () => call('cover', 'close_cover')));
      body.append(row);
      if (f & 4 && a.current_position != null) {
        body.append(slider({ min: 0, max: 100, value: a.current_position, label: 'Position', fmt: (v) => `${v} %`, onChange: (v) => call('cover', 'set_cover_position', { position: v }) }));
      }
      break;
    }
    case 'climate': {
      if (a.temperature != null) {
        const step = a.target_temp_step || 0.5;
        let target = +a.temperature;
        let timer;
        const val = h('span', { class: 'big-num', text: `${fmtNum(target)} °C` });
        const change = (delta) => {
          target = Math.min(a.max_temp ?? 35, Math.max(a.min_temp ?? 5, Math.round((target + delta) / step) * step));
          val.textContent = `${fmtNum(target)} °C`;
          clearTimeout(timer);
          timer = setTimeout(() => call('climate', 'set_temperature', { temperature: target }), 700);
        };
        body.append(h('div', { class: 'thermo' }, btn('−', () => change(-step), 'round'), h('div', {}, h('small', { text: 'Soll' }), val), btn('+', () => change(step), 'round')));
      }
      if (Array.isArray(a.hvac_modes) && a.hvac_modes.length > 1) {
        const row = h('div', { class: 'btn-row' });
        for (const m of a.hvac_modes) {
          row.append(btn(STATE_DE[m] || m, () => call('climate', 'set_hvac_mode', { hvac_mode: m }), s.state === m ? 'active' : ''));
        }
        body.append(row);
      }
      break;
    }
    case 'media_player': {
      if (a.media_title) body.append(h('div', { class: 'media-info' }, h('strong', { text: a.media_title }), a.media_artist ? h('span', { text: a.media_artist }) : null));
      body.append(h('div', { class: 'btn-row' },
        btn('⏮', () => call('media_player', 'media_previous_track')),
        btn(s.state === 'playing' ? '⏸' : '▶', () => call('media_player', 'media_play_pause')),
        btn('⏭', () => call('media_player', 'media_next_track')),
        btn('⏻', () => call('media_player', 'toggle')),
      ));
      if (a.volume_level != null) {
        body.append(slider({ min: 0, max: 100, value: Math.round(a.volume_level * 100), label: 'Lautstärke', fmt: (v) => `${v} %`, onChange: (v) => call('media_player', 'volume_set', { volume_level: v / 100 }) }));
      }
      break;
    }
    case 'lock':
      body.append(h('div', { class: 'btn-row' },
        btn('🔒 Verriegeln', () => call('lock', 'lock'), s.state === 'locked' ? 'active' : ''),
        btn('🔓 Entriegeln', () => call('lock', 'unlock'), s.state === 'unlocked' ? 'active' : '')));
      break;
    case 'vacuum':
      body.append(h('div', { class: 'btn-row' }, btn('▶ Start', () => call('vacuum', 'start')), btn('⏸ Pause', () => call('vacuum', 'pause')), btn('🏠 Zur Station', () => call('vacuum', 'return_to_base'))));
      break;
    case 'scene': case 'script': case 'button': case 'input_button':
      head.push(btn(d === 'scene' ? 'Aktivieren' : d === 'script' ? 'Ausführen' : 'Drücken', () => call(...ACTIVATE[d]), 'primary small'));
      break;
    case 'input_number': case 'number':
      body.append(slider({
        min: a.min ?? 0, max: a.max ?? 100, step: a.step ?? 1, value: parseFloat(s.state) || 0, label: 'Wert',
        fmt: (v) => `${fmtNum(v)}${a.unit_of_measurement ? ' ' + a.unit_of_measurement : ''}`, onChange: (v) => call(d, 'set_value', { value: v }),
      }));
      break;
    case 'input_select': case 'select': {
      const sel = h('select', {}, ...(a.options || []).map((o) => h('option', { value: o, text: o, selected: o === s.state })));
      sel.addEventListener('change', () => call(d, 'select_option', { option: sel.value }));
      body.append(h('div', { class: 'ctl-row' }, h('span', { class: 'ctl-label', text: 'Auswahl' }), sel));
      break;
    }
    case 'camera':
      if (a.entity_picture && ctx.baseUrl != null) {
        const sep = a.entity_picture.includes('?') ? '&' : '?';
        body.append(h('img', { class: 'camera', alt: nameOf(s), src: `${ctx.baseUrl}${a.entity_picture}${sep}t=${Date.now()}` }));
      }
      break;
    default:
      break;
  }
  return { head, body };
}

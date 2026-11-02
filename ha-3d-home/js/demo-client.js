// Simuliertes Home Assistant – zum Ausprobieren ohne echte Installation.
import { Emitter } from './ha-client.js';

const AREAS = [
  ['wohnzimmer', 'Wohnzimmer', 'erdgeschoss'], ['kuche', 'Küche', 'erdgeschoss'], ['flur', 'Flur', 'erdgeschoss'],
  ['gaste_wc', 'Gäste-WC', 'erdgeschoss'], ['hauswirtschaft', 'Hauswirtschaft', 'erdgeschoss'], ['buro', 'Büro', 'erdgeschoss'],
  ['schlafzimmer', 'Schlafzimmer', 'obergeschoss'], ['kinderzimmer', 'Kinderzimmer', 'obergeschoss'], ['bad', 'Bad', 'obergeschoss'],
  ['flur_og', 'Flur OG', 'obergeschoss'], ['gastezimmer', 'Gästezimmer', 'obergeschoss'],
];

function light(id, name, area, opts = {}) {
  const modes = opts.color ? ['color_temp', 'hs'] : opts.dim === false ? ['onoff'] : ['brightness'];
  return [id, area, opts.on ? 'on' : 'off', {
    friendly_name: name,
    supported_color_modes: modes,
    brightness: opts.on ? 200 : null,
    color_mode: opts.on ? (opts.color ? 'hs' : modes[0]) : null,
    hs_color: opts.color ? [35, 60] : undefined,
    rgb_color: opts.color ? [255, 196, 102] : undefined,
    color_temp_kelvin: opts.color ? 3000 : undefined,
    min_color_temp_kelvin: opts.color ? 2200 : undefined,
    max_color_temp_kelvin: opts.color ? 6500 : undefined,
  }];
}
const sensor = (id, name, area, value, unit, device_class) =>
  [id, area, String(value), { friendly_name: name, unit_of_measurement: unit, device_class, state_class: 'measurement' }];
const binary = (id, name, area, on, device_class) => [id, area, on ? 'on' : 'off', { friendly_name: name, device_class }];
const cover = (id, name, area, pos) => [id, area, pos > 0 ? 'open' : 'closed', { friendly_name: name, current_position: pos, supported_features: 15, device_class: 'shutter' }];
const sw = (id, name, area, on) => [id, area, on ? 'on' : 'off', { friendly_name: name }];
const climate = (id, name, area, cur, target) => [id, area, 'heat', {
  friendly_name: name, current_temperature: cur, temperature: target, min_temp: 7, max_temp: 30,
  target_temp_step: 0.5, hvac_modes: ['off', 'heat', 'auto'], hvac_action: 'heating',
}];

const ENTITIES = [
  light('light.wohnzimmer_decke', 'Deckenlicht', 'wohnzimmer', { on: true, color: true }),
  light('light.wohnzimmer_stehlampe', 'Stehlampe', 'wohnzimmer'),
  sensor('sensor.wohnzimmer_temperatur', 'Temperatur', 'wohnzimmer', 21.4, '°C', 'temperature'),
  sensor('sensor.wohnzimmer_luftfeuchte', 'Luftfeuchtigkeit', 'wohnzimmer', 46, '%', 'humidity'),
  cover('cover.wohnzimmer_rollladen', 'Rollladen Terrasse', 'wohnzimmer', 100),
  climate('climate.wohnzimmer_heizung', 'Heizung', 'wohnzimmer', 21.4, 21.5),
  ['media_player.wohnzimmer_tv', 'wohnzimmer', 'playing', {
    friendly_name: 'Fernseher', device_class: 'tv', volume_level: 0.3, media_title: 'Tagesschau', media_artist: 'Das Erste', supported_features: 152463,
  }],
  binary('binary_sensor.terrassentuer', 'Terrassentür', 'wohnzimmer', false, 'door'),

  light('light.kueche', 'Küchenlicht', 'kuche', { on: true }),
  light('light.kueche_unterbau', 'Unterbauleuchte', 'kuche', { dim: false }),
  sw('switch.kaffeemaschine', 'Kaffeemaschine', 'kuche', false),
  sensor('sensor.kueche_temperatur', 'Temperatur', 'kuche', 22.1, '°C', 'temperature'),
  binary('binary_sensor.kueche_fenster', 'Fenster', 'kuche', true, 'window'),
  sensor('sensor.geschirrspueler_leistung', 'Geschirrspüler', 'kuche', 4.2, 'W', 'power'),

  light('light.flur', 'Flurlicht', 'flur', { dim: false }),
  binary('binary_sensor.flur_bewegung', 'Bewegungsmelder', 'flur', false, 'motion'),
  ['lock.haustuer', 'flur', 'locked', { friendly_name: 'Haustür' }],
  binary('binary_sensor.haustuer', 'Haustür Kontakt', 'flur', false, 'door'),

  light('light.gaeste_wc', 'Licht', 'gaste_wc', { dim: false }),
  ['fan.gaeste_wc_luefter', 'gaste_wc', 'off', { friendly_name: 'Lüfter', percentage: 0, percentage_step: 33.3 }],

  sw('switch.waschmaschine', 'Waschmaschine', 'hauswirtschaft', true),
  sensor('sensor.waschmaschine_leistung', 'Leistung Waschmaschine', 'hauswirtschaft', 1830, 'W', 'power'),
  binary('binary_sensor.wassermelder', 'Wassermelder', 'hauswirtschaft', false, 'moisture'),
  ['vacuum.saugroboter', 'hauswirtschaft', 'docked', { friendly_name: 'Saugroboter', battery_level: 100 }],

  light('light.buero', 'Schreibtischlampe', 'buro', { on: true }),
  sensor('sensor.buero_temperatur', 'Temperatur', 'buro', 22.8, '°C', 'temperature'),
  sensor('sensor.buero_co2', 'CO₂', 'buro', 840, 'ppm', 'carbon_dioxide'),
  sw('switch.buero_pc', 'PC Steckdose', 'buro', true),
  cover('cover.buero_rollladen', 'Rollladen', 'buro', 60),

  light('light.schlafzimmer', 'Deckenlicht', 'schlafzimmer', { color: true }),
  light('light.nachttisch', 'Nachttischlampe', 'schlafzimmer'),
  sensor('sensor.schlafzimmer_temperatur', 'Temperatur', 'schlafzimmer', 18.9, '°C', 'temperature'),
  climate('climate.schlafzimmer_heizung', 'Heizung', 'schlafzimmer', 18.9, 18),
  cover('cover.schlafzimmer_rollladen', 'Rollladen', 'schlafzimmer', 0),

  light('light.kinderzimmer', 'Deckenlicht', 'kinderzimmer', { color: true }),
  sensor('sensor.kinderzimmer_temperatur', 'Temperatur', 'kinderzimmer', 20.6, '°C', 'temperature'),
  ['media_player.kinderzimmer_lautsprecher', 'kinderzimmer', 'paused', {
    friendly_name: 'Lautsprecher', volume_level: 0.2, media_title: 'Gute-Nacht-Lieder', media_artist: 'Playlist', supported_features: 152463,
  }],
  ['input_select.kinderzimmer_modus', 'kinderzimmer', 'Spielen', { friendly_name: 'Raummodus', options: ['Spielen', 'Lernen', 'Schlafen'] }],

  light('light.bad', 'Spiegelschrank', 'bad'),
  sensor('sensor.bad_luftfeuchte', 'Luftfeuchtigkeit', 'bad', 63, '%', 'humidity'),
  sensor('sensor.bad_temperatur', 'Temperatur', 'bad', 23.2, '°C', 'temperature'),
  binary('binary_sensor.bad_fenster', 'Fenster', 'bad', false, 'window'),

  light('light.flur_og', 'Licht', 'flur_og', { dim: false }),
  binary('binary_sensor.flur_og_bewegung', 'Bewegungsmelder', 'flur_og', false, 'motion'),
  binary('binary_sensor.rauchmelder_og', 'Rauchmelder', 'flur_og', false, 'smoke'),

  light('light.gaestezimmer', 'Licht', 'gastezimmer'),
  ['input_number.gaestezimmer_heizprofil', 'gastezimmer', '19', { friendly_name: 'Zieltemperatur Profil', min: 15, max: 24, step: 0.5, unit_of_measurement: '°C' }],

  ['scene.filmabend', 'wohnzimmer', 'unknown', { friendly_name: 'Szene: Filmabend' }],
  ['scene.gute_nacht', null, 'unknown', { friendly_name: 'Szene: Gute Nacht' }],
  ['script.alles_aus', null, 'unknown', { friendly_name: 'Alles ausschalten' }],
];

export class DemoClient extends Emitter {
  constructor() {
    super();
    this.baseUrl = '';
    this.states = new Map();
    this.areas = AREAS.map(([area_id, name, floor_id]) => ({ area_id, name, floor_id }));
    this.floors = [
      { floor_id: 'erdgeschoss', name: 'Erdgeschoss', level: 0 },
      { floor_id: 'obergeschoss', name: 'Obergeschoss', level: 1 },
    ];
    this.entityArea = new Map();
    this.hiddenEntities = new Set();
    this._anims = new Map();
    const now = new Date().toISOString();
    for (const [id, area, state, attributes] of ENTITIES) {
      this.states.set(id, { entity_id: id, state, attributes, last_changed: now, last_updated: now });
      if (area) this.entityArea.set(id, area);
    }
  }

  get isDemo() { return true; }

  async connect() {
    this.emit('status', 'connected', 'Demo');
    this._timer = setInterval(() => this._simulate(), 4000);
  }

  disconnect() {
    clearInterval(this._timer);
    this.emit('status', 'disconnected');
  }

  _set(id, state, attrs = {}) {
    const old = this.states.get(id);
    if (!old) return;
    const now = new Date().toISOString();
    const ns = {
      ...old,
      state: state ?? old.state,
      attributes: { ...old.attributes, ...attrs },
      last_updated: now,
      last_changed: state != null && state !== old.state ? now : old.last_changed,
    };
    this.states.set(id, ns);
    this.emit('state', id, ns);
  }

  _simulate() {
    for (const [id, s] of this.states) {
      const dc = s.attributes.device_class;
      if (id.startsWith('sensor.') && (dc === 'temperature' || dc === 'humidity')) {
        const v = parseFloat(s.state) + (Math.random() - 0.5) * (dc === 'temperature' ? 0.2 : 1);
        this._set(id, (dc === 'temperature' ? v.toFixed(1) : Math.round(v).toString()));
      }
      if (id === 'sensor.waschmaschine_leistung') {
        const on = this.states.get('switch.waschmaschine')?.state === 'on';
        this._set(id, on ? String(Math.round(300 + Math.random() * 1800)) : '0');
      }
    }
    if (Math.random() < 0.35) {
      const id = Math.random() < 0.5 ? 'binary_sensor.flur_bewegung' : 'binary_sensor.flur_og_bewegung';
      this._set(id, this.states.get(id).state === 'on' ? 'off' : 'on');
    }
  }

  async callService(domain, service, data = {}, entityId) {
    const ids = [].concat(entityId || []);
    if (domain === 'script' || domain === 'scene') {
      const target = ids[0];
      if (target === 'script.alles_aus' || target === 'scene.gute_nacht') {
        for (const [id, s] of this.states) {
          if ((id.startsWith('light.') || id.startsWith('switch.')) && s.state === 'on') this._toggle(id, false);
        }
      }
      if (target === 'scene.filmabend') {
        this._toggle('light.wohnzimmer_decke', true, { brightness: 40 });
        this._toggle('light.wohnzimmer_stehlampe', false);
        this._coverTo('cover.wohnzimmer_rollladen', 0);
      }
      return;
    }
    for (const id of ids) this._apply(id, domain, service, data);
  }

  _toggle(id, on, attrs = {}) {
    const s = this.states.get(id);
    if (!s) return;
    const extra = {};
    if (id.startsWith('light.')) {
      extra.brightness = on ? (attrs.brightness ?? s.attributes.brightness ?? 200) : null;
      extra.color_mode = on ? (s.attributes.supported_color_modes.includes('hs') ? 'hs' : s.attributes.supported_color_modes[0]) : null;
    }
    if (id.startsWith('fan.')) extra.percentage = on ? (s.attributes.percentage || 33) : 0;
    this._set(id, on ? 'on' : 'off', { ...extra, ...attrs });
  }

  _coverTo(id, target) {
    const s = this.states.get(id);
    if (!s) return;
    clearInterval(this._anims.get(id));
    let pos = s.attributes.current_position ?? 0;
    const dir = Math.sign(target - pos);
    if (!dir) return;
    this._set(id, dir > 0 ? 'opening' : 'closing');
    const timer = setInterval(() => {
      pos = Math.max(0, Math.min(100, pos + dir * 10));
      const done = dir > 0 ? pos >= target : pos <= target;
      if (done) pos = target;
      this._set(id, done ? (pos > 0 ? 'open' : 'closed') : null, { current_position: pos });
      if (done) clearInterval(timer);
    }, 250);
    this._anims.set(id, timer);
  }

  _apply(id, domain, service, data) {
    const s = this.states.get(id);
    if (!s) return;
    const a = s.attributes;
    switch (`${domain}.${service}`) {
      case 'light.turn_on': {
        const attrs = {};
        if (data.brightness != null) attrs.brightness = data.brightness;
        if (data.hs_color) { attrs.hs_color = data.hs_color; attrs.color_mode = 'hs'; attrs.rgb_color = hsToRgb(...data.hs_color); }
        if (data.color_temp_kelvin) { attrs.color_temp_kelvin = data.color_temp_kelvin; attrs.color_mode = 'color_temp'; }
        this._toggle(id, true, attrs);
        break;
      }
      case 'fan.set_percentage':
        this._set(id, data.percentage > 0 ? 'on' : 'off', { percentage: data.percentage });
        break;
      case 'cover.open_cover': this._coverTo(id, 100); break;
      case 'cover.close_cover': this._coverTo(id, 0); break;
      case 'cover.set_cover_position': this._coverTo(id, data.position); break;
      case 'cover.stop_cover':
        clearInterval(this._anims.get(id));
        this._set(id, (a.current_position ?? 0) > 0 ? 'open' : 'closed');
        break;
      case 'climate.set_temperature':
        this._set(id, null, { temperature: data.temperature, hvac_action: data.temperature > a.current_temperature ? 'heating' : 'idle' });
        break;
      case 'climate.set_hvac_mode':
        this._set(id, data.hvac_mode, { hvac_action: data.hvac_mode === 'off' ? 'off' : 'heating' });
        break;
      case 'media_player.media_play_pause':
        this._set(id, s.state === 'playing' ? 'paused' : 'playing');
        break;
      case 'media_player.volume_set':
        this._set(id, null, { volume_level: data.volume_level });
        break;
      case 'media_player.toggle':
        this._set(id, s.state === 'off' ? 'playing' : 'off');
        break;
      case 'media_player.media_next_track':
      case 'media_player.media_previous_track':
        this._set(id, 'playing', { media_title: `Titel ${Math.floor(Math.random() * 20) + 1}` });
        break;
      case 'lock.lock': this._set(id, 'locked'); break;
      case 'lock.unlock': this._set(id, 'unlocked'); break;
      case 'vacuum.start': this._set(id, 'cleaning'); break;
      case 'vacuum.return_to_base': this._set(id, 'returning'); setTimeout(() => this._set(id, 'docked'), 3000); break;
      case 'input_number.set_value':
      case 'number.set_value':
        this._set(id, String(data.value));
        break;
      case 'input_select.select_option':
      case 'select.select_option':
        this._set(id, data.option);
        break;
      default:
        if (service === 'toggle') this._toggle(id, s.state !== 'on');
        else if (service === 'turn_on') this._toggle(id, true);
        else if (service === 'turn_off') this._toggle(id, false);
    }
  }
}

function hsToRgb(h, s) {
  s /= 100;
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return Math.round(255 * (1 - s * Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return [f(5), f(3), f(1)];
}

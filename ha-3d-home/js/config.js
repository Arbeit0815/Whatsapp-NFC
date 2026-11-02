// Konfiguration, Standard-Grundriss und Speicherung im Browser.

const STORAGE_KEY = 'ha3d.config.v1';

// Maße in Metern. x = links→rechts, z = vorne→hinten, floor = Etagen-ID.
export const DEFAULT_LAYOUT = {
  version: 1,
  floors: [
    { id: 0, name: 'Erdgeschoss' },
    { id: 1, name: 'Obergeschoss' },
  ],
  rooms: [
    { id: 'wohnzimmer', name: 'Wohnzimmer', floor: 0, x: 0, z: 0, w: 6, d: 5, color: '#7fa7d6', area: 'wohnzimmer' },
    { id: 'kueche', name: 'Küche', floor: 0, x: 6, z: 0, w: 4, d: 4, color: '#e6b86a', area: 'kuche' },
    { id: 'flur', name: 'Flur', floor: 0, x: 6, z: 4, w: 2, d: 4, color: '#b9b2a6', area: 'flur' },
    { id: 'gaeste_wc', name: 'Gäste-WC', floor: 0, x: 8, z: 4, w: 2, d: 2, color: '#7cc7c2', area: 'gaste_wc' },
    { id: 'hauswirtschaft', name: 'Hauswirtschaft', floor: 0, x: 8, z: 6, w: 2, d: 2, color: '#a3a8c9', area: 'hauswirtschaft' },
    { id: 'buero', name: 'Büro', floor: 0, x: 0, z: 5, w: 6, d: 3, color: '#93c08a', area: 'buro' },
    { id: 'schlafzimmer', name: 'Schlafzimmer', floor: 1, x: 0, z: 0, w: 5, d: 4, color: '#c79ad6', area: 'schlafzimmer' },
    { id: 'kinderzimmer', name: 'Kinderzimmer', floor: 1, x: 5, z: 0, w: 5, d: 4, color: '#f0a07a', area: 'kinderzimmer' },
    { id: 'bad', name: 'Bad', floor: 1, x: 0, z: 4, w: 3.5, d: 4, color: '#6fc3df', area: 'bad' },
    { id: 'flur_og', name: 'Flur OG', floor: 1, x: 3.5, z: 4, w: 2.5, d: 4, color: '#b9b2a6', area: 'flur_og' },
    { id: 'gaestezimmer', name: 'Gästezimmer', floor: 1, x: 6, z: 4, w: 4, d: 4, color: '#d6c27f', area: 'gastezimmer' },
  ],
};

export const DEFAULT_VIEW = {
  wallHeight: 2.5,
  wallOpacity: 0.35,
  floorHeight: 3.0,
  showAllMarkers: false,
  showLabels: true,
};

export const DEFAULT_CONNECTION = {
  mode: 'demo', // 'demo' | 'ha'
  url: '',
  token: '',
};

const clone = (o) => JSON.parse(JSON.stringify(o));

export function loadConfig() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
  } catch { /* ignorieren */ }
  return {
    connection: { ...DEFAULT_CONNECTION, ...(saved?.connection || {}) },
    view: { ...DEFAULT_VIEW, ...(saved?.view || {}) },
    layout: saved?.layout ? normalizeLayout(saved.layout) : null,
    isFirstRun: !saved,
  };
}

export function saveConfig(cfg) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      connection: cfg.connection,
      view: cfg.view,
      layout: cfg.layout,
    }));
  } catch (e) {
    console.warn('Konfiguration konnte nicht gespeichert werden', e);
  }
}

export function defaultLayout() {
  return normalizeLayout(clone(DEFAULT_LAYOUT));
}

/** Lädt optional eine layout.json neben der index.html (z. B. für mehrere Geräte). */
export async function fetchLayoutFile() {
  try {
    const res = await fetch('layout.json', { cache: 'no-store' });
    if (!res.ok) return null;
    return normalizeLayout(await res.json());
  } catch {
    return null;
  }
}

/** Stellt sicher, dass ein (importierter) Grundriss gültig ist. */
export function normalizeLayout(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('Ungültiger Grundriss');
  const floors = Array.isArray(raw.floors) && raw.floors.length
    ? raw.floors.map((f, i) => ({ id: Number.isFinite(+f.id) ? +f.id : i, name: String(f.name ?? `Etage ${i}`) }))
    : [{ id: 0, name: 'Erdgeschoss' }];
  const usedIds = new Set();
  const rooms = (Array.isArray(raw.rooms) ? raw.rooms : []).map((r, i) => {
    let id = slugify(r.id || r.name || `raum_${i}`) || `raum_${i}`;
    while (usedIds.has(id)) id += '_';
    usedIds.add(id);
    return {
      id,
      name: String(r.name ?? id),
      floor: Number.isFinite(+r.floor) ? +r.floor : floors[0].id,
      x: num(r.x, 0),
      z: num(r.z, 0),
      w: Math.max(0.5, num(r.w, 4)),
      d: Math.max(0.5, num(r.d, 4)),
      color: /^#[0-9a-f]{6}$/i.test(r.color) ? r.color : '#9aa5b1',
      area: r.area ? String(r.area) : '',
      entities: Array.isArray(r.entities) ? r.entities.map(String) : [],
      exclude: Array.isArray(r.exclude) ? r.exclude.map(String) : [],
      devices: r.devices && typeof r.devices === 'object' ? r.devices : {},
    };
  });
  return { version: 1, floors, rooms };
}

function num(v, fallback) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

/** Entspricht in etwa Home Assistants slugify (ä→a, ß→ss, Sonderzeichen→_). */
export function slugify(s) {
  return String(s ?? '')
    .toLowerCase()
    .replace(/ß/g, 'ss')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

const PALETTE = ['#7fa7d6', '#e6b86a', '#93c08a', '#c79ad6', '#f0a07a', '#6fc3df', '#d6c27f', '#a3a8c9', '#7cc7c2', '#e08f9c'];
export function paletteColor(i) {
  return PALETTE[i % PALETTE.length];
}

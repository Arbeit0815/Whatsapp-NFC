import { HouseScene } from './scene.js';
import { HAClient } from './ha-client.js';
import { DemoClient } from './demo-client.js';
import {
  loadConfig, saveConfig, defaultLayout, fetchLayoutFile, normalizeLayout, slugify, paletteColor,
} from './config.js';
import * as E from './entities.js';

const { h } = E;
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const cfg = loadConfig();
let client = null;
let roomEntities = new Map(); // roomId → [entity_id]
let entityRooms = new Map(); // entity_id → [roomId]
let selectedRoom = null;
let floorFilter = null;
let editMode = false;
let placing = null; // entity_id, dessen Position gerade gesetzt wird
const cards = new Map(); // entity_id → Karte im Panel

const scene = new HouseScene($('#viewport'), { onRoomClick, onDeviceClick, onPlace });

init();

// ====================================================================== Start

async function init() {
  if (!cfg.layout) cfg.layout = (await fetchLayoutFile()) || defaultLayout();
  // Wird die Seite von Home Assistant selbst ausgeliefert, direkt die HA-Sitzung nutzen.
  if (cfg.isFirstRun && localStorage.getItem('hassTokens')) cfg.connection.mode = 'ha';
  const params = new URLSearchParams(location.search);
  if (params.has('demo')) cfg.connection.mode = 'demo';
  saveConfig(cfg);

  bindUi();
  rebuildScene();
  const fromHash = new URLSearchParams(location.hash.slice(1)).get('room');
  if (fromHash && cfg.layout.rooms.some((r) => r.id === fromHash)) selectRoom(fromHash);
  else { renderPanel(); scene.focusHouse(); }

  await connect();
  if (cfg.isFirstRun && cfg.connection.mode === 'demo') {
    toast('Demo-Modus – unter ⚙ Einstellungen kannst du dich mit Home Assistant verbinden.', 7000);
  }
}

async function connect() {
  client?.disconnect();
  clearTimeout(connect.retry);
  const c = cfg.connection;
  const my = c.mode === 'ha' ? new HAClient({ url: c.url, token: c.token }) : new DemoClient();
  client = my;
  my.on('status', (st, detail) => { if (my === client) setStatus(st, detail); });
  my.on('state', (id, s) => { if (my === client) onState(id, s); });
  my.on('reload', () => { if (my === client) refreshAll(); });
  try {
    await my.connect();
    if (my !== client) return;
    refreshAll();
    $('#connInfo').textContent = my.isDemo
      ? 'Demo-Modus aktiv (simulierte Geräte).'
      : `Verbunden mit ${my.baseUrl} · ${my.states.size} Entitäten · ${my.areas.length} Bereiche`;
  } catch (e) {
    if (my !== client) return;
    setStatus('error', e.message);
    $('#connInfo').textContent = e.message;
    toast(e.message, 8000);
    refreshAll();
    if (!my._closedByUser) connect.retry = setTimeout(connect, 15000); // z. B. HA startet gerade neu
  }
}

// ====================================================================== Zuordnung

function areaIdFor(room) {
  if (!client?.areas.length) return null;
  for (const key of [room.area, room.name].filter(Boolean).map(slugify)) {
    const a = client.areas.find((x) => x.area_id === key || slugify(x.name) === key);
    if (a) return a.area_id;
  }
  return null;
}

function computeAssignments() {
  roomEntities = new Map();
  entityRooms = new Map();
  if (!client) return;
  const byArea = new Map();
  for (const [id, area] of client.entityArea) {
    if (client.hiddenEntities.has(id) || !E.AUTO_DOMAINS.has(E.domainOf(id)) || !client.states.has(id)) continue;
    (byArea.get(area) || byArea.set(area, []).get(area)).push(id);
  }
  for (const room of cfg.layout.rooms) {
    const ids = new Set(room.entities.filter((id) => client.states.has(id)));
    const area = areaIdFor(room);
    for (const id of byArea.get(area) || []) if (!room.exclude.includes(id)) ids.add(id);
    const list = [...ids].sort((a, b) => {
      const g = E.GROUP_ORDER.indexOf(E.groupOf(a)) - E.GROUP_ORDER.indexOf(E.groupOf(b));
      return g || E.nameOf(client.states.get(a)).localeCompare(E.nameOf(client.states.get(b)), 'de');
    });
    roomEntities.set(room.id, list);
    for (const id of list) (entityRooms.get(id) || entityRooms.set(id, []).get(id)).push(room.id);
  }
}

const MARKER_DOMAINS = new Set(['light', 'switch', 'cover', 'climate', 'media_player', 'fan', 'lock', 'binary_sensor', 'sensor', 'camera', 'vacuum', 'humidifier', 'valve', 'siren', 'water_heater']);

function markerFor(room, id) {
  const s = client.states.get(id);
  const d = E.domainOf(id);
  return {
    id,
    icon: E.iconOf(s),
    title: `${E.nameOf(s)}: ${E.stateText(s)}`,
    active: E.isActive(s),
    alert: E.isAlert(s),
    unavailable: s.state === 'unavailable',
    color: d === 'light' ? E.lightColor(s) : d === 'binary_sensor' ? '#ff9f43' : '#7fd3ff',
    pos: Array.isArray(room.devices?.[id]) ? room.devices[id] : null,
  };
}

function roomSummary(roomId) {
  const ids = roomEntities.get(roomId) || [];
  let temp = null, hum = null, lightsOn = 0, brightSum = 0, color = null, open = 0, motion = false, media = false, alert = false, heating = false;
  for (const id of ids) {
    const s = client.states.get(id);
    if (!s) continue;
    const d = E.domainOf(id);
    const dc = s.attributes.device_class;
    if (d === 'light' && s.state === 'on') {
      lightsOn++;
      brightSum += (s.attributes.brightness ?? 255) / 255;
      color ||= E.lightColor(s);
    } else if (d === 'sensor' && dc === 'temperature' && temp == null && Number.isFinite(parseFloat(s.state))) temp = parseFloat(s.state);
    else if (d === 'sensor' && dc === 'humidity' && hum == null && Number.isFinite(parseFloat(s.state))) hum = parseFloat(s.state);
    else if (d === 'climate') {
      if (temp == null && s.attributes.current_temperature != null) temp = +s.attributes.current_temperature;
      if (s.attributes.hvac_action === 'heating') heating = true;
    } else if (d === 'binary_sensor' && s.state === 'on') {
      if (['door', 'window', 'opening', 'garage_door'].includes(dc)) open++;
      if (['motion', 'occupancy', 'presence'].includes(dc)) motion = true;
    } else if (d === 'media_player' && s.state === 'playing') media = true;
    if (E.isAlert(s)) alert = true;
  }
  const chips = [];
  if (alert) chips.push({ text: '⚠️ Alarm', cls: 'danger' });
  if (temp != null) chips.push({ text: `🌡 ${temp.toLocaleString('de-DE', { maximumFractionDigits: 1 })}°` });
  if (hum != null) chips.push({ text: `💧 ${Math.round(hum)} %` });
  if (lightsOn) chips.push({ text: `💡 ${lightsOn}`, cls: 'warm' });
  if (heating) chips.push({ text: '🔥', cls: 'warm' });
  if (open) chips.push({ text: `🚪 ${open} offen`, cls: 'warn' });
  if (motion) chips.push({ text: '🏃', cls: 'info' });
  if (media) chips.push({ text: '🎵', cls: 'info' });
  return {
    chips, alert, lightsOn, count: ids.length,
    lightLevel: lightsOn ? Math.min(1, 0.35 + 0.65 * (brightSum / lightsOn)) : 0,
    lightColor: color,
  };
}

function refreshAll() {
  computeAssignments();
  for (const room of cfg.layout.rooms) {
    const ids = (roomEntities.get(room.id) || []).filter((id) => MARKER_DOMAINS.has(E.domainOf(id)) || room.devices?.[id]);
    scene.setDevices(room.id, client ? ids.map((id) => markerFor(room, id)) : []);
    scene.setRoomSummary(room.id, client ? roomSummary(room.id) : {});
  }
  renderPanel();
}

let refreshTimer;
function onState(id, s) {
  const rooms = entityRooms.get(id);
  if (!rooms) {
    // Neue Entität in einem zugeordneten Bereich? → komplett neu berechnen
    if (s && client.entityArea.has(id)) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshAll, 500); }
    if (!selectedRoom) updateOverviewGlobals();
    return;
  }
  if (!s) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshAll, 300); return; }
  for (const roomId of rooms) {
    const room = cfg.layout.rooms.find((r) => r.id === roomId);
    if (!room) continue;
    scene.updateDevice(roomId, markerFor(room, id));
    scene.setRoomSummary(roomId, roomSummary(roomId));
  }
  if (selectedRoom && rooms.includes(selectedRoom)) updateCard(id);
  else if (!selectedRoom) updateOverviewRows(rooms);
}

// ====================================================================== Interaktion 3D

function onRoomClick(id) {
  if (placing) return;
  if (id === selectedRoom) return;
  selectRoom(id);
}

function selectRoom(id) {
  selectedRoom = id;
  if (!id) editMode = false;
  placing = null;
  scene.startPlacing(null);
  document.body.classList.remove('placing');
  scene.selectRoom(id);
  scene.highlightDevice(null);
  history.replaceState(null, '', id ? `#room=${encodeURIComponent(id)}` : location.pathname + location.search);
  renderPanel();
}

function onDeviceClick(entityId, roomId) {
  const s = client?.states.get(entityId);
  if (!s) return;
  if (roomId !== selectedRoom) selectRoom(roomId);
  const d = E.domainOf(entityId);
  // Schlösser/Alarme nie mit einem einzelnen Klick in 3D schalten
  const action = d === 'lock' || editMode ? null : E.primaryAction(s);
  if (action) {
    callService(action[0], action[1], action[2], entityId);
  }
  focusCard(entityId);
}

function onPlace(roomId, pos) {
  const id = placing;
  placing = null;
  document.body.classList.remove('placing');
  if (!id) return;
  const room = cfg.layout.rooms.find((r) => r.id === roomId);
  if (!room) return;
  room.devices = { ...(room.devices || {}), [id]: pos.map((v) => Math.round(v * 100) / 100) };
  saveConfig(cfg);
  refreshAll();
  toast('Position gespeichert');
}

async function callService(domain, service, data = {}, entityId) {
  if (!client) return;
  try {
    await client.callService(domain, service, data, entityId);
  } catch (e) {
    toast(`Fehler: ${e.message}`, 5000);
  }
}

// ====================================================================== Panel

function renderPanel() {
  cards.clear();
  const body = $('#panelBody');
  body.replaceChildren();
  $('#btnBack').hidden = !selectedRoom;
  $('#btnEdit').hidden = !selectedRoom;
  $('#btnEdit').classList.toggle('active', editMode);
  $('#panel').classList.toggle('editing', editMode);
  renderFloorButtons();

  if (!selectedRoom) return renderOverview(body);

  const room = cfg.layout.rooms.find((r) => r.id === selectedRoom);
  if (!room) { selectedRoom = null; return renderOverview(body); }
  const floorName = cfg.layout.floors.find((f) => f.id === room.floor)?.name || '';
  const ids = roomEntities.get(room.id) || [];
  $('#panelTitle').textContent = room.name;
  $('#panelSub').textContent = `${floorName} · ${ids.length} ${ids.length === 1 ? 'Gerät' : 'Geräte'}`;

  if (!client) return;
  if (editMode) body.append(renderEditTools(room));

  if (!ids.length) {
    const area = areaIdFor(room);
    body.append(h('div', { class: 'empty' },
      h('p', { text: 'Diesem Raum sind noch keine Geräte zugeordnet.' }),
      h('p', { class: 'hint', text: area
        ? `Der HA-Bereich „${area}" enthält keine passenden Geräte.`
        : 'Tipp: Weise in den Einstellungen → Räume einen HA-Bereich zu, oder füge über ✎ einzelne Geräte hinzu.' }),
    ));
    return;
  }

  // Raum-Schnellaktionen
  const lights = ids.filter((id) => E.domainOf(id) === 'light');
  if (lights.length > 1) {
    const anyOn = lights.some((id) => client.states.get(id)?.state === 'on');
    body.append(h('div', { class: 'quick' },
      h('button', { class: 'btn', type: 'button', text: '💡 Alle an', onclick: () => callService('light', 'turn_on', {}, lights) }),
      h('button', { class: `btn ${anyOn ? '' : 'disabled'}`, type: 'button', text: '🌑 Alle aus', onclick: () => callService('light', 'turn_off', {}, lights) }),
    ));
  }

  let group = null;
  for (const id of ids) {
    const g = E.groupOf(id);
    if (g !== group) { group = g; body.append(h('h3', { class: 'group', text: g })); }
    const card = renderCard(id, room);
    cards.set(id, card);
    body.append(card);
  }
}

function renderCard(id, room) {
  const s = client.states.get(id);
  const call = (domain, service, data) => callService(domain, service, data, id);
  const { head, body } = E.renderControls(s, call, { baseUrl: client.baseUrl });
  const d = E.domainOf(id);
  const card = h('div', {
    class: `card ${E.isActive(s) ? 'active' : ''} ${E.isAlert(s) ? 'alert' : ''} ${s.state === 'unavailable' ? 'unavailable' : ''}`,
    'data-entity': id,
  });
  card.style.setProperty('--glow', d === 'light' ? E.lightColor(s) : '#4fc3ff');
  const iconBtn = h('button', {
    class: 'card-icon', type: 'button', text: E.iconOf(s), title: 'In 3D zeigen',
    onclick: () => scene.highlightDevice(id),
  });
  const title = h('div', { class: 'card-title' },
    h('div', { class: 'card-name', text: E.nameOf(s) }),
    h('div', { class: 'card-state', text: E.stateText(s) }));
  const tools = [];
  if (editMode) {
    tools.push(h('button', {
      class: 'icon-btn small', type: 'button', title: 'Position im Raum festlegen (danach in 3D auf den Boden klicken)', text: '📍',
      onclick: () => startPlacing(id, room),
    }));
    tools.push(h('button', { class: 'icon-btn small danger', type: 'button', title: 'Aus diesem Raum entfernen', text: '✕', onclick: () => removeFromRoom(room, id) }));
  }
  card.append(h('div', { class: 'card-head' }, iconBtn, title, ...(editMode ? tools : head)));
  if (!editMode && body.childNodes.length) card.append(body);

  // Während ein Schieberegler gezogen wird, keine Neuzeichnung
  card.addEventListener('pointerdown', (e) => {
    if (e.target.matches('input[type=range]')) {
      card.dataset.busy = '1';
      window.addEventListener('pointerup', () => {
        setTimeout(() => { delete card.dataset.busy; if (card.dataset.dirty) updateCard(id); }, 50);
      }, { once: true });
    }
  });
  return card;
}

function updateCard(id) {
  const old = cards.get(id);
  if (!old || !client.states.get(id)) return;
  if (old.dataset.busy) { old.dataset.dirty = '1'; return; }
  const room = cfg.layout.rooms.find((r) => r.id === selectedRoom);
  const fresh = renderCard(id, room);
  if (old.classList.contains('focus')) fresh.classList.add('focus');
  old.replaceWith(fresh);
  cards.set(id, fresh);
}

function focusCard(id) {
  scene.highlightDevice(id);
  const card = cards.get(id);
  if (!card) return;
  $$('.card.focus').forEach((c) => c.classList.remove('focus'));
  card.classList.add('focus');
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  setTimeout(() => card.classList.remove('focus'), 2500);
}

function renderOverview(body) {
  $('#panelTitle').textContent = 'Übersicht';
  const total = [...roomEntities.values()].reduce((n, l) => n + l.length, 0);
  $('#panelSub').textContent = client ? `${cfg.layout.rooms.length} Räume · ${total} Geräte` : '';

  const floors = cfg.layout.floors.filter((f) => floorFilter == null || f.id === floorFilter);
  for (const f of floors) {
    const rooms = cfg.layout.rooms.filter((r) => r.floor === f.id);
    if (!rooms.length) continue;
    body.append(h('h3', { class: 'group', text: f.name }));
    for (const room of rooms) body.append(overviewRow(room));
  }
  if (!cfg.layout.rooms.length) {
    body.append(h('div', { class: 'empty' }, h('p', { text: 'Noch keine Räume angelegt.' }),
      h('button', { class: 'btn primary', type: 'button', text: 'Räume einrichten', onclick: () => openSettings('rooms') })));
  }
  body.append(h('div', { id: 'globals' }));
  updateOverviewGlobals();
}

function overviewRow(room) {
  const sum = client ? roomSummary(room.id) : { chips: [], count: 0 };
  return h('button', { class: `room-row ${sum.alert ? 'alert' : ''}`, type: 'button', 'data-room': room.id, onclick: () => selectRoom(room.id) },
    h('span', { class: 'room-dot', style: `background:${room.color}` }),
    h('span', { class: 'room-row-name', text: room.name }),
    h('span', { class: 'room-row-chips' }, ...sum.chips.map((c) => h('span', { class: `chip ${c.cls || ''}`, text: c.text }))),
    h('span', { class: 'chev', text: '›' }));
}

function updateOverviewRows(roomIds) {
  for (const id of roomIds) {
    const old = document.querySelector(`.room-row[data-room="${CSS.escape(id)}"]`);
    const room = cfg.layout.rooms.find((r) => r.id === id);
    if (old && room) old.replaceWith(overviewRow(room));
  }
}

/** Szenen/Skripte ohne Raum als globale Aktionen. */
function updateOverviewGlobals() {
  const box = document.getElementById('globals');
  if (!box || !client) return;
  const ids = [...client.states.keys()].filter((id) => ['scene', 'script'].includes(E.domainOf(id)) && !entityRooms.has(id) && !client.hiddenEntities.has(id)).slice(0, 24);
  box.replaceChildren();
  if (!ids.length) return;
  box.append(h('h3', { class: 'group', text: 'Szenen & Skripte' }));
  const grid = h('div', { class: 'scene-grid' });
  for (const id of ids) {
    const s = client.states.get(id);
    const [d, sv] = E.primaryAction(s) || [];
    grid.append(h('button', {
      class: 'btn scene-btn', type: 'button', disabled: !d, title: id,
      onclick: () => { callService(d, sv, {}, id); toast(`${E.nameOf(s)} ausgeführt`); },
    }, h('span', { text: E.iconOf(s) }), h('span', { text: E.nameOf(s) })));
  }
  box.append(grid);
}

// ---------- Bearbeiten: Geräte zuordnen

function renderEditTools(room) {
  const list = h('datalist', { id: 'entityOptions' });
  const assigned = new Set(roomEntities.get(room.id) || []);
  const all = [...client.states.values()]
    .filter((s) => !assigned.has(s.entity_id))
    .sort((a, b) => E.nameOf(a).localeCompare(E.nameOf(b), 'de'));
  for (const s of all) list.append(h('option', { value: s.entity_id, text: E.nameOf(s) }));
  const input = h('input', { type: 'text', list: 'entityOptions', placeholder: 'Entität suchen, z. B. light.küche', spellcheck: 'false' });
  const add = () => {
    const v = input.value.trim();
    const id = client.states.has(v) ? v : all.find((s) => E.nameOf(s).toLowerCase() === v.toLowerCase())?.entity_id;
    if (!id) { toast('Entität nicht gefunden'); return; }
    if (!room.entities.includes(id)) room.entities.push(id);
    room.exclude = room.exclude.filter((x) => x !== id);
    saveConfig(cfg);
    refreshAll();
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
  const area = areaIdFor(room);
  return h('div', { class: 'edit-tools' },
    h('p', { class: 'hint', text: `Bearbeitungsmodus · HA-Bereich: ${area || '—'}. Geräte hinzufügen, entfernen (✕) oder mit 📍 im Raum platzieren.` }),
    h('div', { class: 'add-row' }, input, h('button', { class: 'btn primary', type: 'button', text: 'Hinzufügen', onclick: add })),
    list,
    Object.keys(room.devices || {}).length
      ? h('button', { class: 'btn small', type: 'button', text: 'Alle Positionen zurücksetzen', onclick: () => { room.devices = {}; saveConfig(cfg); refreshAll(); } })
      : null,
  );
}

function removeFromRoom(room, id) {
  if (room.entities.includes(id)) room.entities = room.entities.filter((x) => x !== id);
  else if (!room.exclude.includes(id)) room.exclude.push(id);
  if (room.devices?.[id]) delete room.devices[id];
  saveConfig(cfg);
  refreshAll();
}

function startPlacing(id, room) {
  placing = id;
  document.body.classList.add('placing');
  scene.startPlacing(room.id);
  toast('Jetzt in 3D auf die gewünschte Stelle im Raum klicken (Esc bricht ab)', 5000);
}

// ====================================================================== Etagen-Buttons, Status, Toast

function renderFloorButtons() {
  const nav = $('#floorButtons');
  const btns = [{ id: null, name: 'Alle' }, ...cfg.layout.floors];
  nav.replaceChildren(...btns.map((f) => h('button', {
    class: `floor-btn ${floorFilter === f.id ? 'active' : ''}`, type: 'button', text: f.name,
    onclick: () => setFloor(f.id),
  })));
}

function setFloor(id) {
  floorFilter = id;
  const room = cfg.layout.rooms.find((r) => r.id === selectedRoom);
  if (room && id != null && room.floor !== id) {
    selectedRoom = null;
    editMode = false;
    scene.selectRoom(null, false);
  }
  scene.setFloorFilter(id);
  renderPanel();
}

function setStatus(st, detail) {
  const el = $('#status');
  el.dataset.state = st;
  const text = {
    connecting: 'Verbinde …',
    connected: client?.isDemo ? 'Demo' : 'Verbunden',
    reconnecting: 'Neu verbinden …',
    disconnected: 'Getrennt',
    error: 'Fehler',
  }[st] || st;
  el.querySelector('span').textContent = text;
  el.title = st === 'connected' && detail && !client?.isDemo ? `Home Assistant ${detail}` : (typeof detail === 'string' ? detail : text);
}

let toastTimer;
function toast(msg, ms = 2500) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

// ====================================================================== Einstellungen

function rebuildScene() {
  scene.build(cfg.layout, cfg.view);
  if (floorFilter != null && !cfg.layout.floors.some((f) => f.id === floorFilter)) floorFilter = null;
  scene.floorFilter = floorFilter;
  if (selectedRoom && !cfg.layout.rooms.some((r) => r.id === selectedRoom)) selectedRoom = null;
  scene.selectRoom(selectedRoom, false);
  if (client) refreshAll(); else renderPanel();
}

let rebuildTimer;
function layoutChanged({ table = false } = {}) {
  saveConfig(cfg);
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuildScene, 150);
  if (table) renderRoomTable();
}

let lastLayoutMode = null;
function updateInsets() {
  const panel = $('#panel');
  const mobile = window.matchMedia('(max-width: 760px)').matches;
  scene.setInsets(mobile ? { bottom: panel.offsetHeight } : { right: panel.offsetWidth + 12 });
  // Beim ersten Mal bzw. Wechsel Handy ↔ Desktop den Bildausschnitt neu berechnen
  if (lastLayoutMode !== mobile) {
    lastLayoutMode = mobile;
    if (selectedRoom) scene.focusRoom(selectedRoom); else scene.focusHouse();
  }
}

function bindUi() {
  new ResizeObserver(updateInsets).observe($('#panel'));
  window.addEventListener('resize', updateInsets);
  $('#btnHome').addEventListener('click', () => { selectRoom(null); scene.focusHouse(); });
  $('#btnBack').addEventListener('click', () => selectRoom(null));
  $('#btnEdit').addEventListener('click', () => { editMode = !editMode; renderPanel(); });
  $('#btnSettings').addEventListener('click', () => openSettings());
  $('#panelGrip').addEventListener('click', () => $('#panel').classList.toggle('collapsed'));
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || $('#settings').open) return;
    if (placing) { placing = null; scene.startPlacing(null); document.body.classList.remove('placing'); toast('Abgebrochen'); }
    else if (selectedRoom) selectRoom(null);
  });

  // Tabs
  $$('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));

  // Verbindung
  $('#btnConnect').addEventListener('click', async () => {
    cfg.connection.mode = document.querySelector('input[name=mode]:checked')?.value || 'demo';
    cfg.connection.url = $('#cfgUrl').value.trim();
    cfg.connection.token = $('#cfgToken').value.trim();
    saveConfig(cfg);
    $('#connInfo').textContent = 'Verbinde …';
    await connect();
  });
  $$('input[name=mode]').forEach((r) => r.addEventListener('change', updateConnFields));

  // Darstellung
  const bindRange = (id, key, out, fmt) => {
    const el = $(id);
    el.addEventListener('input', () => {
      cfg.view[key] = +el.value;
      $(out).textContent = fmt(+el.value);
      layoutChanged();
    });
  };
  bindRange('#cfgWallHeight', 'wallHeight', '#outWallHeight', (v) => `${v.toFixed(1)} m`);
  bindRange('#cfgWallOpacity', 'wallOpacity', '#outWallOpacity', (v) => `${Math.round(v * 100)} %`);
  bindRange('#cfgFloorHeight', 'floorHeight', '#outFloorHeight', (v) => `${v.toFixed(1)} m`);
  $('#cfgShowAll').addEventListener('change', (e) => { cfg.view.showAllMarkers = e.target.checked; layoutChanged(); });
  $('#cfgLabels').addEventListener('change', (e) => { cfg.view.showLabels = e.target.checked; layoutChanged(); });

  // Räume
  $('#btnAddRoom').addEventListener('click', addRoom);
  $('#btnAddFloor').addEventListener('click', () => {
    const id = Math.max(-1, ...cfg.layout.floors.map((f) => f.id)) + 1;
    cfg.layout.floors.push({ id, name: `Etage ${id}` });
    layoutChanged({ table: true });
  });
  $('#btnFromAreas').addEventListener('click', roomsFromAreas);

  // Import/Export
  $('#btnApplyJson').addEventListener('click', () => {
    try {
      cfg.layout = normalizeLayout(JSON.parse($('#cfgJson').value));
      layoutChanged({ table: true });
      toast('Grundriss übernommen');
    } catch (e) { toast(`Ungültiges JSON: ${e.message}`, 5000); }
  });
  $('#btnDownload').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(cfg.layout, null, 2)], { type: 'application/json' });
    const a = h('a', { href: URL.createObjectURL(blob), download: 'layout.json' });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  $('#fileImport').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      cfg.layout = normalizeLayout(JSON.parse(await f.text()));
      $('#cfgJson').value = JSON.stringify(cfg.layout, null, 2);
      layoutChanged({ table: true });
      toast('Grundriss geladen');
    } catch (err) { toast(`Datei ungültig: ${err.message}`, 5000); }
    e.target.value = '';
  });
  $('#btnReset').addEventListener('click', () => {
    if (!confirm('Aktuellen Grundriss durch das Beispielhaus ersetzen?')) return;
    cfg.layout = defaultLayout();
    $('#cfgJson').value = JSON.stringify(cfg.layout, null, 2);
    layoutChanged({ table: true });
  });
}

function openSettings(tab = 'conn') {
  document.querySelector(`input[name=mode][value=${cfg.connection.mode}]`).checked = true;
  $('#cfgUrl').value = cfg.connection.url;
  $('#cfgToken').value = cfg.connection.token;
  updateConnFields();
  const v = cfg.view;
  $('#cfgWallHeight').value = v.wallHeight; $('#outWallHeight').textContent = `${(+v.wallHeight).toFixed(1)} m`;
  $('#cfgWallOpacity').value = v.wallOpacity; $('#outWallOpacity').textContent = `${Math.round(v.wallOpacity * 100)} %`;
  $('#cfgFloorHeight').value = v.floorHeight; $('#outFloorHeight').textContent = `${(+v.floorHeight).toFixed(1)} m`;
  $('#cfgShowAll').checked = v.showAllMarkers;
  $('#cfgLabels').checked = v.showLabels;
  renderRoomTable();
  showTab(tab);
  $('#settings').showModal();
}

function updateConnFields() {
  const ha = document.querySelector('input[name=mode]:checked')?.value === 'ha';
  $('#cfgUrl').disabled = !ha;
  $('#cfgToken').disabled = !ha;
  const tokenHint = ha && !$('#cfgToken').value && localStorage.getItem('hassTokens')
    ? 'Kein Token eingetragen – die bestehende Home-Assistant-Anmeldung dieses Browsers wird verwendet.' : '';
  $('#connInfo').textContent = tokenHint || $('#connInfo').textContent;
}

function showTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.tab-page').forEach((p) => p.classList.toggle('active', p.dataset.page === name));
  if (name === 'io') $('#cfgJson').value = JSON.stringify(cfg.layout, null, 2);
}

function renderRoomTable() {
  // Etagen
  $('#floorList').replaceChildren(...cfg.layout.floors.map((f) => {
    const name = h('input', { type: 'text', value: f.name });
    name.addEventListener('change', () => { f.name = name.value.trim() || f.name; layoutChanged(); });
    const del = h('button', {
      class: 'icon-btn small danger', type: 'button', title: 'Etage löschen', text: '✕', disabled: cfg.layout.floors.length <= 1,
      onclick: () => {
        const n = cfg.layout.rooms.filter((r) => r.floor === f.id).length;
        if (n && !confirm(`Etage „${f.name}" löschen? ${n} Räume werden ebenfalls gelöscht.`)) return;
        cfg.layout.floors = cfg.layout.floors.filter((x) => x !== f);
        cfg.layout.rooms = cfg.layout.rooms.filter((r) => r.floor !== f.id);
        layoutChanged({ table: true });
      },
    });
    return h('div', { class: 'floor-item' }, h('span', { class: 'floor-id', text: `#${f.id}` }), name, del);
  }));

  // Bereiche aus HA als Vorschläge
  $('#areaList').replaceChildren(...(client?.areas || []).map((a) => h('option', { value: a.area_id, text: a.name })));

  // Räume
  const rows = cfg.layout.rooms.map((room) => {
    const numInput = (key, step = 0.5) => {
      const el = h('input', { type: 'number', step, value: room[key], class: 'num' });
      el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        if (!Number.isFinite(v)) return;
        room[key] = key === 'w' || key === 'd' ? Math.max(0.5, v) : v;
        layoutChanged();
      });
      return h('td', {}, el);
    };
    const name = h('input', { type: 'text', value: room.name });
    name.addEventListener('input', () => { room.name = name.value; layoutChanged(); });
    const floor = h('select', {}, ...cfg.layout.floors.map((f) => h('option', { value: f.id, text: f.name, selected: f.id === room.floor })));
    floor.addEventListener('change', () => { room.floor = +floor.value; layoutChanged(); });
    const area = h('input', { type: 'text', value: room.area, list: 'areaList', placeholder: slugify(room.name), class: 'area' });
    const areaOk = () => area.classList.toggle('ok', !!client && !!areaIdFor(room));
    area.addEventListener('input', () => { room.area = area.value.trim(); areaOk(); layoutChanged(); });
    areaOk();
    const color = h('input', { type: 'color', value: room.color });
    color.addEventListener('input', () => { room.color = color.value; layoutChanged(); });
    const del = h('button', {
      class: 'icon-btn small danger', type: 'button', title: 'Raum löschen', text: '✕',
      onclick: () => {
        if (!confirm(`Raum „${room.name}" löschen?`)) return;
        cfg.layout.rooms = cfg.layout.rooms.filter((r) => r !== room);
        layoutChanged({ table: true });
      },
    });
    return h('tr', {}, h('td', {}, name), h('td', {}, floor), numInput('x'), numInput('z'), numInput('w'), numInput('d'), h('td', {}, area), h('td', {}, color), h('td', {}, del));
  });
  $('#roomRows').replaceChildren(...rows);
}

function addRoom() {
  const floor = floorFilter ?? cfg.layout.floors[0].id;
  const same = cfg.layout.rooms.filter((r) => r.floor === floor);
  const x = same.length ? Math.max(...same.map((r) => r.x + r.w)) : 0;
  const n = cfg.layout.rooms.length + 1;
  let id = `raum_${n}`;
  while (cfg.layout.rooms.some((r) => r.id === id)) id += '_';
  cfg.layout.rooms.push({ id, name: `Raum ${n}`, floor, x, z: 0, w: 4, d: 4, color: paletteColor(n), area: '', entities: [], exclude: [], devices: {} });
  layoutChanged({ table: true });
}

function roomsFromAreas() {
  if (!client?.areas.length) { toast('Keine Bereiche gefunden – bitte zuerst mit Home Assistant verbinden.', 5000); return; }
  if (cfg.layout.rooms.length && !confirm('Alle bestehenden Räume durch die Bereiche aus Home Assistant ersetzen?')) return;
  const haFloors = client.floors || [];
  const floors = haFloors.length
    ? haFloors.map((f, i) => ({ id: i, name: f.name, key: f.floor_id }))
    : [{ id: 0, name: 'Erdgeschoss', key: null }];
  const rooms = [];
  for (const f of floors) {
    const areas = client.areas.filter((a) => (haFloors.length ? (a.floor_id || floors[0].key) === f.key : true));
    const cols = Math.max(1, Math.ceil(Math.sqrt(areas.length)));
    areas.forEach((a, i) => {
      rooms.push({
        id: a.area_id, name: a.name, floor: f.id, x: (i % cols) * 4, z: Math.floor(i / cols) * 4, w: 4, d: 4,
        color: paletteColor(rooms.length), area: a.area_id, entities: [], exclude: [], devices: {},
      });
    });
  }
  cfg.layout = normalizeLayout({ floors: floors.map(({ id, name }) => ({ id, name })), rooms });
  layoutChanged({ table: true });
  toast(`${rooms.length} Räume erzeugt – Größen und Positionen jetzt in der Tabelle anpassen.`, 6000);
}

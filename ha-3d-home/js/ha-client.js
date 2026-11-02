// Minimaler Home-Assistant-WebSocket-Client
// Doku: https://developers.home-assistant.io/docs/api/websocket

export class Emitter {
  constructor() { this._handlers = {}; }
  on(evt, fn) { (this._handlers[evt] ||= []).push(fn); return () => this.off(evt, fn); }
  off(evt, fn) { this._handlers[evt] = (this._handlers[evt] || []).filter((h) => h !== fn); }
  emit(evt, ...args) { (this._handlers[evt] || []).forEach((h) => { try { h(...args); } catch (e) { console.error(e); } }); }
}

/**
 * Gemeinsame Schnittstelle (auch vom DemoClient implementiert):
 *  - states: Map<entity_id, stateObj>
 *  - areas: [{area_id, name, floor_id}]
 *  - floors: [{floor_id, name, level}]
 *  - entityArea: Map<entity_id, area_id>
 *  - hiddenEntities: Set<entity_id>  (Diagnose/Konfiguration/versteckt)
 *  - baseUrl: String (für Kamera-Bilder)
 *  - events: 'status' (status, detail), 'state' (entity_id, newState), 'reload'
 */
export class HAClient extends Emitter {
  constructor({ url, token }) {
    super();
    this.baseUrl = normalizeBaseUrl(url || window.location.origin);
    this.token = (token || '').trim();
    this.states = new Map();
    this.areas = [];
    this.floors = [];
    this.entityArea = new Map();
    this.hiddenEntities = new Set();
    this._id = 1;
    this._pending = new Map();
    this._ws = null;
    this._closedByUser = false;
    this._retry = 0;
    this._ping = null;
  }

  get isDemo() { return false; }

  async connect() {
    this._closedByUser = false;
    await this._open();
  }

  disconnect() {
    this._closedByUser = true;
    clearInterval(this._ping);
    if (this._ws) this._ws.close();
  }

  async callService(domain, service, data = {}, entityId) {
    const msg = { type: 'call_service', domain, service, service_data: data };
    if (entityId) msg.target = { entity_id: entityId };
    return this._send(msg);
  }

  // ---------- intern ----------

  async _accessToken() {
    if (this.token) return this.token;
    // Wird die App über Home Assistant selbst ausgeliefert (/local/...),
    // kann die bestehende Anmeldung des HA-Frontends genutzt werden.
    const raw = localStorage.getItem('hassTokens');
    if (!raw) throw new Error('Kein Access-Token angegeben (Einstellungen → Verbindung).');
    const t = JSON.parse(raw);
    if (t.access_token && t.expires && Date.now() < t.expires - 30_000) return t.access_token;
    if (!t.refresh_token) throw new Error('HA-Sitzung abgelaufen – bitte in Home Assistant neu anmelden.');
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: t.clientId || `${window.location.origin}/`,
      refresh_token: t.refresh_token,
    });
    const res = await fetch(`${t.hassUrl || this.baseUrl}/auth/token`, { method: 'POST', body });
    if (!res.ok) throw new Error('HA-Sitzung konnte nicht erneuert werden – bitte Token eintragen.');
    const data = await res.json();
    return data.access_token;
  }

  _open() {
    return new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => { if (!settled) { settled = true; reject(err); } };

      let wsUrl;
      try {
        wsUrl = this.baseUrl.replace(/^http/, 'ws') + '/api/websocket';
        if (window.location.protocol === 'https:' && wsUrl.startsWith('ws:')) {
          throw new Error('Diese Seite läuft über HTTPS, Home Assistant aber über HTTP – der Browser blockiert das (Mixed Content). Nutze eine HTTPS-Adresse oder hoste die App in Home Assistant (siehe README).');
        }
      } catch (e) { fail(e); return; }

      this.emit('status', 'connecting');
      const ws = new WebSocket(wsUrl);
      this._ws = ws;

      ws.onmessage = async (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const list = Array.isArray(msg) ? msg : [msg];
        for (const m of list) await this._handle(m, { resolve: (v) => { settled = true; resolve(v); }, fail });
      };
      ws.onerror = () => fail(new Error(`Keine Verbindung zu ${this.baseUrl} möglich.`));
      ws.onclose = () => {
        clearInterval(this._ping);
        for (const p of this._pending.values()) p.reject(new Error('Verbindung getrennt'));
        this._pending.clear();
        if (!settled) { fail(new Error(`Verbindung zu ${this.baseUrl} wurde geschlossen.`)); return; }
        if (this._closedByUser) { if (!this._authFailed) this.emit('status', 'disconnected'); return; }
        this._scheduleReconnect();
      };
    });
  }

  async _handle(m, { resolve, fail }) {
    switch (m.type) {
      case 'auth_required':
        try {
          this._ws.send(JSON.stringify({ type: 'auth', access_token: await this._accessToken() }));
        } catch (e) { fail(e); this._ws.close(); }
        break;
      case 'auth_invalid':
        this._closedByUser = true;
        this._authFailed = true;
        fail(new Error('Anmeldung fehlgeschlagen: Token ungültig.'));
        this._ws.close();
        break;
      case 'auth_ok':
        try {
          await this._loadAll();
          this._retry = 0;
          this._ping = setInterval(() => this._send({ type: 'ping' }).catch(() => {}), 30_000);
          this.emit('status', 'connected', m.ha_version);
          resolve();
        } catch (e) { fail(e); this._ws.close(); }
        break;
      case 'result':
      case 'pong': {
        const p = this._pending.get(m.id);
        if (!p) break;
        this._pending.delete(m.id);
        if (m.type === 'pong' || m.success) p.resolve(m.result);
        else p.reject(new Error(m.error?.message || 'Fehler'));
        break;
      }
      case 'event': {
        const e = m.event;
        if (e?.event_type === 'state_changed') {
          const { entity_id, new_state } = e.data;
          if (new_state) this.states.set(entity_id, new_state);
          else this.states.delete(entity_id);
          this.emit('state', entity_id, new_state);
        } else if (e?.event_type?.endsWith('_registry_updated')) {
          clearTimeout(this._regTimer);
          this._regTimer = setTimeout(() => this._loadRegistries().then(() => this.emit('reload')).catch(() => {}), 1000);
        }
        break;
      }
      default:
        break;
    }
  }

  _send(msg) {
    return new Promise((resolve, reject) => {
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
        reject(new Error('Nicht verbunden'));
        return;
      }
      const id = this._id++;
      this._pending.set(id, { resolve, reject });
      this._ws.send(JSON.stringify({ ...msg, id }));
    });
  }

  async _loadAll() {
    const states = await this._send({ type: 'get_states' });
    this.states = new Map(states.map((s) => [s.entity_id, s]));
    await this._loadRegistries();
    await this._send({ type: 'subscribe_events', event_type: 'state_changed' });
    for (const t of ['area_registry_updated', 'entity_registry_updated', 'device_registry_updated']) {
      this._send({ type: 'subscribe_events', event_type: t }).catch(() => {});
    }
  }

  async _loadRegistries() {
    const safe = (p) => p.catch(() => null);
    const [areas, entities, devices, floors] = await Promise.all([
      safe(this._send({ type: 'config/area_registry/list' })),
      safe(this._send({ type: 'config/entity_registry/list' })),
      safe(this._send({ type: 'config/device_registry/list' })),
      safe(this._send({ type: 'config/floor_registry/list' })), // ab HA 2024.4
    ]);
    this.areas = areas || [];
    this.floors = (floors || []).slice().sort((a, b) => (a.level ?? 0) - (b.level ?? 0));
    const deviceArea = new Map((devices || []).map((d) => [d.id, d.area_id]));
    this.entityArea = new Map();
    this.hiddenEntities = new Set();
    for (const e of entities || []) {
      const area = e.area_id || deviceArea.get(e.device_id);
      if (area) this.entityArea.set(e.entity_id, area);
      if (e.entity_category || e.hidden_by || e.disabled_by) this.hiddenEntities.add(e.entity_id);
    }
  }

  _scheduleReconnect() {
    const delay = Math.min(30_000, 1000 * 2 ** this._retry++);
    this.emit('status', 'reconnecting', delay);
    setTimeout(async () => {
      if (this._closedByUser) return;
      try {
        await this._open();
        this.emit('reload');
      } catch (e) {
        if (this._closedByUser) this.emit('status', 'error', e.message);
        else this._scheduleReconnect();
      }
    }, delay);
  }
}

export function normalizeBaseUrl(url) {
  let u = String(url).trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(u)) u = `http://${u}`;
  return u;
}

// 3D-Darstellung des Hauses mit three.js
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/CSS2DRenderer.js';

const WALL_T = 0.08;
const SLAB_T = 0.12;
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export class HouseScene {
  /**
   * @param {HTMLElement} container
   * @param {{onRoomClick:(id:string|null)=>void, onDeviceClick:(entityId:string, roomId:string)=>void}} cb
   */
  constructor(container, cb) {
    this.container = container;
    this.cb = cb;
    this.rooms = new Map(); // id → { room, group, floorMesh, walls[], label, markers: Map }
    this.floorFilter = null; // null = alle Etagen
    this.selected = null;
    this.hovered = null;
    this.view = {};

    const w = container.clientWidth || 1, hgt = container.clientHeight || 1;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, hgt);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.append(this.renderer.domElement);

    this.labels = new CSS2DRenderer();
    this.labels.setSize(w, hgt);
    this.labels.domElement.className = 'label-layer';
    container.append(this.labels.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#0f1724');
    this.scene.fog = new THREE.Fog('#0f1724', 40, 90);

    this.camera = new THREE.PerspectiveCamera(45, w / hgt, 0.1, 500);
    this.camera.position.set(14, 16, 20);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.47;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 70;
    this.controls.addEventListener('start', () => { this.anim = null; });

    // Licht
    this.scene.add(new THREE.HemisphereLight('#dfe9ff', '#2a2f3a', 1.1));
    const sun = new THREE.DirectionalLight('#ffffff', 1.6);
    sun.position.set(12, 22, 10);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -20; sun.shadow.camera.right = 20;
    sun.shadow.camera.top = 20; sun.shadow.camera.bottom = -20;
    sun.shadow.bias = -0.0005;
    this.sun = sun;
    this.scene.add(sun, sun.target);

    // Boden
    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(60, 64),
      new THREE.MeshStandardMaterial({ color: '#1b2636', roughness: 1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.01;
    ground.receiveShadow = true;
    this.scene.add(ground);
    this.grid = new THREE.GridHelper(80, 80, '#2c3a50', '#223045');
    this.grid.position.y = 0.001;
    this.scene.add(this.grid);

    this.house = new THREE.Group();
    this.scene.add(this.house);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._bindPointer();

    new ResizeObserver(() => this._resize()).observe(container);
    this.renderer.setAnimationLoop(() => this._tick());
  }

  // ------------------------------------------------------------------ Aufbau

  build(layout, view) {
    this.view = view;
    this.layout = layout;
    for (const r of this.rooms.values()) {
      r.label.element.remove();
      for (const m of r.markers.values()) m.obj.element.remove();
    }
    this.house.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
    this.house.clear();
    this.rooms.clear();
    this.house.position.set(0, 0, 0);
    this.house.updateMatrixWorld(true);
    this.explode = this._explodeTarget();

    const box = new THREE.Box3();
    for (const room of layout.rooms) {
      const entry = this._buildRoom(room, view);
      this.rooms.set(room.id, entry);
      box.expandByObject(entry.group);
    }
    if (box.isEmpty()) box.set(new THREE.Vector3(-5, 0, -5), new THREE.Vector3(5, 3, 5));
    // Haus um den Ursprung zentrieren
    const c = box.getCenter(new THREE.Vector3());
    this.house.position.set(-c.x, 0, -c.z);
    this.houseSize = box.getSize(new THREE.Vector3());
    this.sun.target.position.set(0, 0, 0);
    if (this.selected && !this.rooms.has(this.selected)) this.selected = null;
    this._applyVisibility();
  }

  /** Höhe einer Etage. `current` = aktuell animierter Wert, sonst Zielwert. */
  _floorBase(floor, current = false) {
    const gap = current ? this.explode : this._explodeTarget();
    return floor * ((this.view.floorHeight || 3) + gap);
  }

  /** In der „Alle"-Ansicht werden die Etagen auseinandergezogen. */
  _explodeTarget() {
    const floors = new Set((this.layout?.rooms || []).map((r) => r.floor));
    return this.floorFilter == null && floors.size > 1 ? 2.2 : 0;
  }

  /** Abstand, aus dem ein Bereich der Größe `size` vollständig ins Bild passt. */
  _fitDistance(size) {
    // Durch setViewOffset deckt das Sichtfeld die virtuelle Fläche (H + bottom) ab;
    // sichtbar frei ist nur (W - right) × (H - bottom).
    const W = this.container.clientWidth || 1, H = this.container.clientHeight || 1;
    const { right = 0, bottom = 0 } = this.insets || {};
    const t = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const tv = (t * Math.max(1, H - bottom)) / (H + bottom);
    const th = (t * Math.max(1, W - right)) / (H + bottom);
    return (size / 2) / Math.max(0.02, Math.min(tv, th));
  }

  _buildRoom(room, view) {
    const group = new THREE.Group();
    const base = this._floorBase(room.floor, true);
    group.position.set(room.x, base, room.z);
    group.userData.roomId = room.id;
    this.house.add(group);

    const color = new THREE.Color(room.color);
    const floorMat = new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.0, emissive: new THREE.Color('#000000') });
    const floorMesh = new THREE.Mesh(new THREE.BoxGeometry(room.w, SLAB_T, room.d), floorMat);
    floorMesh.position.set(room.w / 2, SLAB_T / 2, room.d / 2);
    floorMesh.receiveShadow = true;
    floorMesh.castShadow = true;
    floorMesh.userData.roomId = room.id;
    group.add(floorMesh);

    const H = view.wallHeight;
    const wallColor = new THREE.Color('#e9eef5').lerp(color, 0.25);
    const wallMat = new THREE.MeshStandardMaterial({
      color: wallColor, roughness: 0.9, transparent: true, opacity: view.wallOpacity, depthWrite: view.wallOpacity > 0.9,
      side: THREE.DoubleSide,
    });
    const edgeMat = new THREE.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.55 });
    const walls = [];
    const specs = [
      [room.w, room.w / 2, WALL_T / 2], // vorne
      [room.w, room.w / 2, room.d - WALL_T / 2], // hinten
    ];
    const specsZ = [
      [room.d - 2 * WALL_T, WALL_T / 2, room.d / 2], // links
      [room.d - 2 * WALL_T, room.w - WALL_T / 2, room.d / 2], // rechts
    ];
    const addWall = (geo, x, z) => {
      const m = new THREE.Mesh(geo, wallMat);
      m.position.set(x, SLAB_T + H / 2, z);
      m.castShadow = view.wallOpacity > 0.5;
      m.receiveShadow = true;
      m.userData.roomId = room.id;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), edgeMat);
      m.add(edges);
      group.add(m);
      walls.push(m);
    };
    for (const [len, x, z] of specs) addWall(new THREE.BoxGeometry(len, H, WALL_T), x, z);
    for (const [len, x, z] of specsZ) addWall(new THREE.BoxGeometry(WALL_T, H, len), x, z);

    // Auswahl-Rahmen (Bodenumriss)
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(room.w + 0.06, SLAB_T + 0.02, room.d + 0.06)),
      new THREE.LineBasicMaterial({ color: '#4fc3ff', transparent: true, opacity: 0 }),
    );
    outline.position.copy(floorMesh.position);
    group.add(outline);

    // Beschriftung
    const el = document.createElement('div');
    el.className = 'room-label';
    el.innerHTML = '<div class="room-name"></div><div class="room-chips"></div>';
    el.querySelector('.room-name').textContent = room.name;
    el.addEventListener('click', (e) => { e.stopPropagation(); this.cb.onRoomClick(room.id); });
    const label = new CSS2DObject(el);
    label.position.set(room.w / 2, SLAB_T + H + 0.35, room.d / 2);
    group.add(label);

    return { room, group, floorMesh, floorMat, walls, wallMat, edgeMat, outline, label, markers: new Map(), glow: 0, glowTarget: 0, glowColor: new THREE.Color('#ffb24d') };
  }

  // ------------------------------------------------------------------ Zustände

  /** Raum-Zusammenfassung aktualisieren (Chips im Label + Licht-Glühen). */
  setRoomSummary(roomId, { chips = [], lightLevel = 0, lightColor = null, alert = false } = {}) {
    const r = this.rooms.get(roomId);
    if (!r) return;
    const box = r.label.element.querySelector('.room-chips');
    box.replaceChildren(...chips.map((c) => {
      const s = document.createElement('span');
      s.className = 'chip' + (c.cls ? ' ' + c.cls : '');
      s.textContent = c.text;
      return s;
    }));
    r.label.element.classList.toggle('alert', alert);
    r.glowTarget = lightLevel;
    if (lightColor) r.glowColor.set(lightColor);
  }

  /** Geräte-Marker eines Raums setzen: [{id, icon, active, alert, title, pos:[fx,fz]?}] */
  setDevices(roomId, devices) {
    const r = this.rooms.get(roomId);
    if (!r) return;
    const keep = new Set(devices.map((d) => d.id));
    for (const [id, m] of r.markers) {
      if (!keep.has(id)) { m.obj.element.remove(); r.group.remove(m.obj); r.markers.delete(id); }
    }
    const { w, d } = r.room;
    const auto = devices.filter((dv) => !dv.pos);
    const cols = Math.max(1, Math.ceil(Math.sqrt(auto.length * (w / d))));
    const rows = Math.max(1, Math.ceil(auto.length / cols));
    let i = 0;
    for (const dev of devices) {
      let m = r.markers.get(dev.id);
      if (!m) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'marker';
        el.addEventListener('click', (e) => { e.stopPropagation(); this.cb.onDeviceClick(dev.id, roomId); });
        el.addEventListener('pointerdown', (e) => e.stopPropagation());
        const obj = new CSS2DObject(el);
        r.group.add(obj);
        m = { obj };
        r.markers.set(dev.id, m);
      }
      let fx, fz;
      if (dev.pos) [fx, fz] = dev.pos;
      else {
        const c = i % cols, rr = Math.floor(i / cols);
        fx = (c + 0.5) / cols; fz = (rr + 0.5) / rows;
        i++;
      }
      const margin = 0.35;
      m.obj.position.set(margin + fx * (w - 2 * margin), SLAB_T + Math.min(1.1, this.view.wallHeight * 0.45), margin + fz * (d - 2 * margin));
      this.updateDevice(roomId, dev);
    }
    this._applyVisibility();
  }

  updateDevice(roomId, dev) {
    const m = this.rooms.get(roomId)?.markers.get(dev.id);
    if (!m) return;
    const el = m.obj.element;
    el.textContent = dev.icon;
    el.title = dev.title;
    el.classList.toggle('active', !!dev.active);
    el.classList.toggle('alert', !!dev.alert);
    el.classList.toggle('off', !!dev.unavailable);
    el.style.setProperty('--glow', dev.color || '#ffcf6e');
    m.active = !!dev.active || !!dev.alert;
    this._applyVisibility();
  }

  highlightDevice(entityId) {
    for (const r of this.rooms.values()) {
      for (const [id, m] of r.markers) m.obj.element.classList.toggle('focus', id === entityId);
    }
  }

  // ------------------------------------------------------------------ Ansicht

  setFloorFilter(floor) {
    this.floorFilter = floor;
    this._applyVisibility();
    if (this.selected) {
      const r = this.rooms.get(this.selected);
      if (r && floor != null && r.room.floor !== floor) this.selected = null;
    }
    if (!this.selected) this.focusHouse();
  }

  selectRoom(id, fly = true) {
    this.selected = id && this.rooms.has(id) ? id : null;
    this._applyVisibility();
    if (!fly) return;
    if (this.selected) this.focusRoom(this.selected);
    else this.focusHouse();
  }

  focusRoom(id) {
    const r = this.rooms.get(id);
    if (!r) return;
    const { room } = r;
    const target = new THREE.Vector3(room.x + room.w / 2, this._floorBase(room.floor) + 0.6, room.z + room.d / 2).add(this.house.position);
    const dist = Math.max(4.5, this._fitDistance(Math.hypot(room.w, room.d)) * 1.3);
    this._flyTo(target, dist, 0.95);
  }

  focusHouse() {
    const s = this.houseSize || new THREE.Vector3(10, 3, 10);
    const floors = (this.layout?.rooms || []).map((r) => r.floor);
    const top = floors.length ? Math.max(...floors) : 0;
    let y = (this._floorBase(top) + (this.view.wallHeight || 2.5)) / 2;
    if (this.floorFilter != null) y = this._floorBase(this.floorFilter);
    const dist = Math.max(8, this._fitDistance(Math.hypot(s.x, s.z)) * 0.95 + y * 0.6);
    this._flyTo(new THREE.Vector3(0, y, 0), dist, 0.8);
  }

  _flyTo(target, dist, elevation) {
    const cur = this.camera.position.clone().sub(this.controls.target);
    const az = Math.atan2(cur.x, cur.z);
    const pos = new THREE.Vector3(
      Math.sin(az) * Math.cos(elevation) * dist,
      Math.sin(elevation) * dist,
      Math.cos(az) * Math.cos(elevation) * dist,
    ).add(target);
    this.anim = {
      start: performance.now(), dur: 750,
      fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone(),
      toPos: pos, toTarget: target,
    };
  }

  _floorVisible(floor) {
    if (this.floorFilter != null) return floor === this.floorFilter;
    // Raum gewählt: darüberliegende Etagen ausblenden, damit man hineinsieht
    const sel = this.selected && this.rooms.get(this.selected);
    if (sel && floor > sel.room.floor) return false;
    return true;
  }

  _applyVisibility() {
    const showLabels = this.view.showLabels !== false;
    for (const [id, r] of this.rooms) {
      const vis = this._floorVisible(r.room.floor);
      const isSel = id === this.selected;
      const dim = this.selected && !isSel;
      r.group.visible = vis;
      r.label.visible = vis && showLabels;
      r.label.element.classList.toggle('selected', isSel);
      r.label.element.classList.toggle('dim', !!dim);
      r.outline.material.opacity = isSel ? 1 : 0;
      r.wallMat.opacity = isSel ? Math.min(1, this.view.wallOpacity + 0.1) : dim ? this.view.wallOpacity * 0.5 : this.view.wallOpacity;
      r.edgeMat.opacity = isSel ? 0.9 : dim ? 0.25 : 0.55;
      r.edgeMat.color.set(isSel ? '#7fd3ff' : '#ffffff');
      for (const m of r.markers.values()) {
        m.obj.visible = vis && (isSel || this.view.showAllMarkers || (!!m.active && !dim));
      }
    }
  }

  // ------------------------------------------------------------------ Interaktion

  _bindPointer() {
    const el = this.renderer.domElement;
    let down = null;
    el.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    el.addEventListener('pointerup', (e) => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      down = null;
      if (moved > 6) return;
      if (this.placingRoom) { this._place(e); return; }
      const id = this._pick(e);
      this.cb.onRoomClick(id);
    });
    el.addEventListener('pointermove', (e) => {
      if (e.pointerType !== 'mouse' || e.buttons || this.placingRoom) return;
      const id = this._pick(e);
      if (id !== this.hovered) {
        this.hovered = id;
        el.style.cursor = id ? 'pointer' : '';
        for (const [rid, r] of this.rooms) r.label.element.classList.toggle('hover', rid === id);
      }
    });
  }

  /** Platzierungsmodus: nächster Klick auf den Boden des Raums setzt eine Geräteposition. */
  startPlacing(roomId) {
    this.placingRoom = roomId || null;
    this.renderer.domElement.style.cursor = roomId ? 'crosshair' : '';
  }

  _place(e) {
    const r = this.rooms.get(this.placingRoom);
    if (!r) { this.startPlacing(null); return; }
    this._setPointer(e);
    const hit = this.raycaster.intersectObject(r.floorMesh, false)[0];
    if (!hit) return; // daneben geklickt → weiter warten
    const local = r.group.worldToLocal(hit.point.clone());
    const margin = 0.35;
    const clamp = (v) => Math.min(1, Math.max(0, v));
    const pos = [clamp((local.x - margin) / (r.room.w - 2 * margin)), clamp((local.z - margin) / (r.room.d - 2 * margin))];
    const roomId = this.placingRoom;
    this.startPlacing(null);
    this.cb.onPlace?.(roomId, pos);
  }

  _setPointer(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
  }

  _pick(e) {
    this._setPointer(e);
    const visible = [...this.rooms.values()].filter((r) => r.group.visible);
    // Böden zuerst: durch transparente Wände hindurch soll der Raum dahinter wählbar sein
    const hit = this.raycaster.intersectObjects(visible.map((r) => r.floorMesh), false)[0]
      || this.raycaster.intersectObjects(visible.flatMap((r) => r.walls), false)[0];
    return hit?.object.userData.roomId ?? null;
  }

  /** Bereiche, die vom Panel verdeckt sind – der Bildmittelpunkt wird in den freien Bereich verschoben. */
  setInsets({ right = 0, bottom = 0 } = {}) {
    this.insets = { right, bottom };
    this._resize();
  }

  _resize() {
    const w = this.container.clientWidth, hgt = this.container.clientHeight;
    if (!w || !hgt) return;
    const { right = 0, bottom = 0 } = this.insets || {};
    this.camera.aspect = (w + right) / (hgt + bottom);
    if (right || bottom) this.camera.setViewOffset(w + right, hgt + bottom, right, bottom, w, hgt);
    else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, hgt);
    this.labels.setSize(w, hgt);
  }

  _tick() {
    const now = performance.now();
    if (this.anim) {
      const t = Math.min(1, (now - this.anim.start) / this.anim.dur);
      const k = ease(t);
      this.camera.position.lerpVectors(this.anim.fromPos, this.anim.toPos, k);
      this.controls.target.lerpVectors(this.anim.fromTarget, this.anim.toTarget, k);
      if (t >= 1) this.anim = null;
    }
    // Explosionsansicht der Etagen animieren
    const ex = this._explodeTarget();
    if (Math.abs(this.explode - ex) > 0.001) {
      this.explode += (ex - this.explode) * 0.15;
      if (Math.abs(this.explode - ex) < 0.01) this.explode = ex;
      for (const r of this.rooms.values()) r.group.position.y = this._floorBase(r.room.floor, true);
    }
    // Licht-Glühen weich überblenden
    for (const r of this.rooms.values()) {
      if (Math.abs(r.glow - r.glowTarget) > 0.001) {
        r.glow += (r.glowTarget - r.glow) * 0.12;
        r.floorMat.emissive.copy(r.glowColor).multiplyScalar(r.glow * 0.55);
      }
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }
}

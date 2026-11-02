# Smart Home 3D – Home Assistant als 3D-Haus

Eine Web-App, die dein Zuhause als **3D-Modell** zeigt. Du klickst einen Raum an,
die Kamera fliegt hinein, und rechts erscheinen alle Geräte dieses Raums aus
**Home Assistant** – live und steuerbar.

## Funktionen

- **3D-Haus** mit mehreren Etagen (drehen, zoomen, verschieben mit Maus oder Touch)
- **Räume anklicken** → Kamera fliegt hinein, darüberliegende Etagen werden ausgeblendet
- **Live-Status direkt im 3D-Modell**
  - Räume leuchten in der Lichtfarbe, wenn Lampen an sind
  - Raumschilder zeigen Temperatur, Luftfeuchte, Anzahl Lichter, offene Fenster/Türen, Bewegung, laufende Medien
  - Alarme (Rauch, Wasser, Gas …) blinken rot
- **Geräte-Symbole im Raum** – ein Klick schaltet Licht/Steckdose/Rollladen/Szene direkt
  (Schlösser bewusst nur über das Panel)
- **Steuerung im Panel** je nach Gerätetyp:
  Licht (an/aus, Helligkeit, Farbtemperatur, Farbe), Schalter, Heizung/Klima (Solltemperatur, Modus),
  Rollläden (auf/stopp/zu, Position), Medienplayer (Play/Pause, Titel, Lautstärke), Lüfter, Schlösser,
  Saugroboter, Szenen, Skripte, Buttons, `input_number`/`number`, `input_select`/`select`, Kamerabild, Sensoren
- **Automatische Zuordnung** der Geräte über die **Bereiche (Areas)** aus Home Assistant
- **Räume automatisch aus HA-Bereichen erzeugen** (inkl. Etagen ab HA 2024.4)
- **Grundriss-Editor** (Etagen, Räume, Größe, Position, Farbe) und Import/Export als JSON
- **Bearbeitungsmodus (✎)**: Geräte einem Raum hinzufügen/entfernen und per 📍 frei im Raum platzieren
- **Demo-Modus** mit simuliertem Haus – zum Ausprobieren ganz ohne Home Assistant
- Läuft am PC, Tablet und Handy; kein Build, keine Installation, kein Internet nötig
  (three.js liegt im Ordner `vendor/`)
- Direktlink zu einem Raum: `index.html#room=wohnzimmer`

## Schnellstart (Demo)

Die Seite muss über einen Webserver geöffnet werden (nicht per Doppelklick als `file://`), z. B.:

```bash
cd ha-3d-home
python3 -m http.server 8080
```

Dann <http://localhost:8080> öffnen. Ohne Verbindung startet automatisch der **Demo-Modus**.

## Mit Home Assistant verbinden

### Variante A (empfohlen): App direkt in Home Assistant hosten

So gibt es keine Probleme mit HTTPS/HTTP, und die Anmeldung von Home Assistant wird automatisch mitbenutzt.

1. Den kompletten Ordner `ha-3d-home` in das HA-Konfigurationsverzeichnis nach
   `config/www/ha-3d-home/` kopieren (z. B. mit dem Add-on „File editor", „Samba share" oder „Studio Code Server").
   Existiert der Ordner `www` noch nicht, anlegen und Home Assistant einmal neu starten.
2. Aufrufen: `http://<deine-ha-adresse>:8123/local/ha-3d-home/index.html`
3. Wenn du im selben Browser in Home Assistant mit „Angemeldet bleiben" eingeloggt bist, verbindet sich die App
   automatisch. Andernfalls unter ⚙ → *Verbindung* ein Token eintragen (siehe unten), Adresse leer lassen.

**Als Seitenleisten-Eintrag in Home Assistant:**
*Einstellungen → Dashboards → Dashboard hinzufügen → „Webseite"* → URL `/local/ha-3d-home/index.html`.
Alternativ in einem Dashboard eine **Webseiten-Karte** (`type: iframe`) mit dieser URL anlegen –
mit `#room=kueche` am Ende zeigt die Karte direkt einen bestimmten Raum.

> Hinweis: Dateien unter `/local/` sind ohne Anmeldung abrufbar. Das ist unkritisch, weil die App selbst
> keine Zugangsdaten enthält – das Token wird nur im Browser gespeichert.

### Variante B: App woanders hosten

1. Seite öffnen (lokaler Webserver, NAS, GitHub Pages …).
2. ⚙ → *Verbindung* → Modus **Home Assistant**.
3. **Adresse** eintragen, z. B. `http://homeassistant.local:8123` oder deine Nabu-Casa-/HTTPS-Adresse.
4. **Langlebiges Zugriffstoken** erstellen: in Home Assistant unten links auf dein **Profil** →
   Reiter **Sicherheit** → *Langlebige Zugriffstoken* → **Token erstellen**. Token kopieren und einfügen.
5. **Verbinden** klicken.

> ⚠️ Wird die App über **HTTPS** geöffnet (z. B. GitHub Pages), muss auch Home Assistant über **HTTPS**
> erreichbar sein (z. B. Nabu Casa). Browser blockieren Verbindungen von HTTPS-Seiten zu `http://`-Adressen.

## Grundriss einrichten

Unter ⚙ → **Räume**:

- **„Aus HA-Bereichen erzeugen"** legt für jeden Bereich in Home Assistant einen Raum an
  (bei eingerichteten HA-Etagen auch gleich die Etagen). Danach nur noch Größe und Position anpassen.
- Oder Räume manuell anlegen: **X/Z** ist die Position der linken vorderen Ecke in Metern,
  **Breite/Tiefe** die Größe. Räume liegen bündig nebeneinander, wenn z. B. Raum 1 bei X=0 mit Breite 5
  und Raum 2 bei X=5 beginnt.
- **HA-Bereich**: ID oder Name des Bereichs in Home Assistant (z. B. `kuche` für „Küche").
  Bleibt das Feld leer, wird ein Bereich mit gleichem Namen wie der Raum gesucht.
  Ein grüner Rahmen zeigt, dass der Bereich gefunden wurde.

Im Raum über **✎** (oben rechts im Panel) kannst du:

- weitere Entitäten hinzufügen (auch ohne Bereich in HA),
- automatisch zugeordnete Geräte ausblenden (✕),
- mit **📍** ein Gerät anklicken und dann im 3D-Raum auf die Stelle klicken, an der es stehen soll.

Unter ⚙ → **Import/Export** lässt sich der Grundriss als `layout.json` speichern. Legst du diese Datei neben die
`index.html`, wird sie auf neuen Geräten automatisch geladen – praktisch für Wandtablets.

### Aufbau der `layout.json`

```json
{
  "floors": [{ "id": 0, "name": "Erdgeschoss" }, { "id": 1, "name": "Obergeschoss" }],
  "rooms": [
    {
      "id": "wohnzimmer", "name": "Wohnzimmer", "floor": 0,
      "x": 0, "z": 0, "w": 6, "d": 5, "color": "#7fa7d6",
      "area": "wohnzimmer",
      "entities": ["light.stehlampe_extra"],
      "exclude": ["sensor.irgendwas_unwichtiges"],
      "devices": { "light.wohnzimmer_decke": [0.5, 0.5] }
    }
  ]
}
```

`devices` enthält die Positionen der Symbole im Raum (0 – 1, links→rechts / vorne→hinten).

## Bedienung

| Aktion | Maus | Touch |
|---|---|---|
| Drehen | Linke Maustaste ziehen | Ein Finger ziehen |
| Verschieben | Rechte Maustaste ziehen | Zwei Finger ziehen |
| Zoomen | Mausrad | Zwei Finger spreizen |
| Raum öffnen | Raum anklicken | Raum antippen |
| Zurück zur Übersicht | ⌂, ‹ oder Esc | ⌂ oder ‹ |

## Technik

- Reines HTML/CSS/JavaScript (ES-Module), kein Build-Schritt
- [three.js](https://threejs.org) r160 (MIT-Lizenz, in `vendor/three/`)
- Kommunikation über die [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket)
  (`get_states`, `subscribe_events`, `call_service`, Bereichs-/Geräte-/Entitäts-Registry)
- Automatische Wiederverbindung, z. B. nach einem HA-Neustart

| Datei | Inhalt |
|---|---|
| `index.html` | Seitenaufbau, Einstellungsdialog |
| `js/main.js` | Ablauf, Zuordnung Geräte ↔ Räume, Panel, Einstellungen |
| `js/scene.js` | 3D-Haus, Kamera, Klicks, Symbole |
| `js/ha-client.js` | WebSocket-Verbindung zu Home Assistant |
| `js/demo-client.js` | Simuliertes Haus für den Demo-Modus |
| `js/entities.js` | Symbole, deutsche Zustandstexte, Steuerelemente je Gerätetyp |
| `js/config.js` | Beispielhaus, Speichern/Laden der Einstellungen |

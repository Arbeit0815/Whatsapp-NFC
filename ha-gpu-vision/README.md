# HA GPU Vision – Ereignis-Analyse mit Kennzeichenerkennung für Home Assistant

Ein Dienst, der Fotos und Videos von Kamera-Ereignissen mit einer **NVIDIA-GPU**
analysiert und das Ergebnis **als Text** an Home Assistant liefert – inklusive
**Kennzeichenerkennung (ALPR)** wie bei einem Automated License Plate Reader.

Beispiel-Ausgabe:

> „2 Personen und 1 Auto erkannt. Kennzeichen: BAB1234."

## Was es kann

- **Objekterkennung** (YOLOv8 auf der GPU): Personen, Autos, LKWs, Busse,
  Motorräder, Fahrräder, Hunde, Katzen
- **Kennzeichenerkennung** ([fast-alpr](https://github.com/ankandrew/fast-alpr)):
  eigener Kennzeichen-Detektor + OCR, läuft ebenfalls auf der GPU
- **Videos und Fotos**: Videos werden Frame-weise abgetastet, Ergebnisse aggregiert
- **Drei Eingangswege**:
  1. **Ordnerüberwachung** – neue Dateien in `./watch` werden automatisch analysiert
     (ideal für Frigate-Clips oder Kamera-FTP-Uploads)
  2. **HTTP-API** – Snapshot-URL der Kamera oder Datei-Upload analysieren
  3. **Dateipfad** – eine gemountete Datei (z. B. Frigate-Clip) direkt analysieren
- **Home-Assistant-Integration per MQTT-Discovery** – diese Sensoren erscheinen
  automatisch, ohne YAML:
  - `sensor.gpu_vision_analyzer_letztes_ereignis` (Textbeschreibung, alle Details als Attribute)
  - `sensor.gpu_vision_analyzer_letztes_kennzeichen`
  - `sensor.gpu_vision_analyzer_personen`
  - `sensor.gpu_vision_analyzer_fahrzeuge`

## Warum kein Home-Assistant-Add-on?

Home Assistant OS kann NVIDIA-GPUs nicht an Add-ons durchreichen (keine
NVIDIA-Treiber im HAOS-Kernel). Der bewährte Weg ist daher ein **eigenständiger
Docker-Container auf dem Rechner mit der GPU** (das kann derselbe Rechner sein,
wenn du Home Assistant Container/Supervised nutzt, oder ein zweiter Rechner im
Netz). Die Anbindung an Home Assistant läuft vollautomatisch über MQTT.

## Voraussetzungen

- NVIDIA-GPU mit installiertem Treiber (auf dem Docker-Host)
- Docker + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  (Test: `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi`)
- Home Assistant mit eingerichteter **MQTT-Integration** (z. B. Mosquitto-Add-on)

## Installation

```bash
cd ha-gpu-vision
# docker-compose.yml anpassen: MQTT_HOST, MQTT_USER, MQTT_PASSWORD
docker compose up -d --build
```

Beim ersten Start werden die Modelle (YOLO + ALPR, insgesamt ca. 100 MB)
automatisch heruntergeladen und im Volume `./models` gecacht.

Status prüfen:

```bash
curl http://localhost:8099/health
docker logs -f ha-gpu-vision
```

Wenn im Log `CUDA verfügbar: ...` steht, läuft die Analyse auf der GPU.

## Benutzung

### 1. Ordnerüberwachung (einfachster Weg)

Jede Bild- oder Videodatei, die im Ordner `./watch` landet, wird automatisch
analysiert und das Ergebnis per MQTT publiziert:

```bash
cp einfahrt_clip.mp4 watch/
```

Viele Kameras können bei Bewegung per FTP hochladen – FTP-Zielordner einfach
auf `./watch` zeigen lassen. Frigate-Nutzer können stattdessen den
Frigate-Media-Ordner mounten und `analyze_path` nutzen (siehe unten).

### 2. Kamera-Snapshot per Automation analysieren

`homeassistant/configuration_beispiel.yaml` in deine `configuration.yaml`
übernehmen (IP anpassen), dann in einer Automation:

```yaml
action:
  - service: rest_command.gpu_vision_analyse_snapshot
    data:
      snapshot_url: "http://192.168.1.30/snapshot.jpg"
      username: "kamera_user"
      password: "kamera_passwort"
      source: "einfahrt"
```

Fertige Beispiel-Automationen (Bewegungsmelder → Analyse → Benachrichtigung,
Tor öffnen bei bekanntem Kennzeichen) findest du in
`homeassistant/automationen_beispiel.yaml`.

### 3. HTTP-API direkt

```bash
# Foto hochladen
curl -F "file=@foto.jpg" http://localhost:8099/analyze

# Video hochladen
curl -F "file=@clip.mp4" http://localhost:8099/analyze

# Kamera-Snapshot von URL
curl -X POST http://localhost:8099/analyze_url \
  -H "Content-Type: application/json" \
  -d '{"url": "http://192.168.1.30/snapshot.jpg", "username": "u", "password": "p"}'

# Gemountete Datei (z. B. Frigate-Clip)
curl -X POST http://localhost:8099/analyze_path \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/frigate/clips/einfahrt-123.mp4"}'
```

Antwort (und identisch der MQTT-Payload auf `gpu_vision/result` und `gpu_vision/event`):

```json
{
  "source": "einfahrt",
  "kind": "image",
  "description": "1 Auto erkannt. Kennzeichen: BAB1234.",
  "counts": {"Auto": 1},
  "persons": 0,
  "vehicles": 1,
  "plates": [{"text": "BAB1234", "confidence": 0.94, "box": [412, 388, 561, 431]}],
  "detections": [{"label": "Auto", "confidence": 0.91, "box": [201, 220, 780, 610]}],
  "frames_analyzed": 1
}
```

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|---|---|---|
| `MQTT_HOST` / `MQTT_PORT` | `homeassistant.local` / `1883` | MQTT-Broker |
| `MQTT_USER` / `MQTT_PASSWORD` | leer | MQTT-Zugangsdaten |
| `MQTT_BASE_TOPIC` | `gpu_vision` | Basis-Topic für Ergebnisse |
| `YOLO_MODEL` | `yolov8m.pt` | `yolov8n.pt` = schnell, `yolov8l/x.pt` = genauer |
| `DETECTION_CONFIDENCE` | `0.40` | Mindest-Konfidenz Objekte |
| `PLATE_CONFIDENCE` | `0.35` | Mindest-Konfidenz Kennzeichen-OCR |
| `DEVICE` | `auto` | `auto`, `cuda:0` oder `cpu` |
| `ALPR_OCR_MODEL` | `global-plates-mobile-vit-v2-model` | für Europa gut geeignet; siehe fast-alpr-Doku für Alternativen |
| `WATCH_ENABLED` / `WATCH_DIR` | `true` / `/watch` | Ordnerüberwachung |
| `PROCESSED_DIR` | leer | analysierte Dateien hierhin verschieben |
| `VIDEO_FRAME_STRIDE` | `10` | jedes n-te Video-Frame analysieren |
| `VIDEO_MAX_FRAMES` | `60` | max. analysierte Frames pro Video |
| `HTTP_PORT` | `8099` | API-Port |

## Tipps für gute Kennzeichenerkennung

- Kamera so ausrichten, dass Kennzeichen **möglichst frontal** und mit
  mindestens ~80 Pixeln Breite im Bild sind
- Bei Videos erhöht ein kleinerer `VIDEO_FRAME_STRIDE` (z. B. `5`) die
  Trefferquote, kostet aber GPU-Zeit
- Nachts hilft eine Kamera mit gutem IR – Kennzeichen reflektieren stark
- Die OCR gibt Kennzeichen **ohne Leerzeichen/Bindestriche** aus
  (`BAB1234` statt `B-AB 1234`) – in Automationen entsprechend vergleichen

## Fehlersuche

- **`Keine GPU gefunden` im Log**: NVIDIA Container Toolkit installiert?
  `docker compose` mit dem `deploy.resources`-Block gestartet? Test mit `nvidia-smi` im Container.
- **Sensoren erscheinen nicht in HA**: MQTT-Integration in HA aktiv? Log auf
  `Mit MQTT-Broker ... verbunden` prüfen; Zugangsdaten kontrollieren.
- **Erster Start dauert lange**: Modelle werden heruntergeladen – dank
  `./models`-Volume nur einmal.

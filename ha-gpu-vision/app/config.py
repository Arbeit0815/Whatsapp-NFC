"""Zentrale Konfiguration über Umgebungsvariablen."""
import os


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- HTTP-Server ---
HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = _int("HTTP_PORT", 8099)

# --- MQTT / Home Assistant ---
MQTT_ENABLED = _bool("MQTT_ENABLED", True)
MQTT_HOST = os.getenv("MQTT_HOST", "homeassistant.local")
MQTT_PORT = _int("MQTT_PORT", 1883)
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "gpu_vision")
HA_DISCOVERY_PREFIX = os.getenv("HA_DISCOVERY_PREFIX", "homeassistant")
DEVICE_NAME = os.getenv("DEVICE_NAME", "GPU Vision Analyzer")
DEVICE_ID = os.getenv("DEVICE_ID", "gpu_vision_analyzer")

# --- Erkennung ---
# z. B. yolov8n.pt (schnell) ... yolov8x.pt (genau). Wird beim ersten Start geladen.
YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8m.pt")
DETECTION_CONFIDENCE = _float("DETECTION_CONFIDENCE", 0.40)
PLATE_CONFIDENCE = _float("PLATE_CONFIDENCE", 0.35)
# "auto" wählt CUDA, wenn verfügbar, sonst CPU. Alternativ "cuda:0" oder "cpu".
DEVICE = os.getenv("DEVICE", "auto")

# fast-alpr Modelle (werden beim ersten Start automatisch heruntergeladen)
ALPR_DETECTOR_MODEL = os.getenv(
    "ALPR_DETECTOR_MODEL", "yolo-v9-t-384-license-plate-end2end"
)
ALPR_OCR_MODEL = os.getenv("ALPR_OCR_MODEL", "global-plates-mobile-vit-v2-model")

# --- Video-Analyse ---
# Nur jedes n-te Frame analysieren (Videos haben meist 15-30 fps)
VIDEO_FRAME_STRIDE = _int("VIDEO_FRAME_STRIDE", 10)
VIDEO_MAX_FRAMES = _int("VIDEO_MAX_FRAMES", 60)

# --- Ordnerüberwachung (z. B. Frigate-Clips oder Kamera-FTP-Upload) ---
WATCH_ENABLED = _bool("WATCH_ENABLED", True)
WATCH_DIR = os.getenv("WATCH_DIR", "/watch")
# analysierte Dateien hierhin verschieben ("" = Datei liegen lassen)
PROCESSED_DIR = os.getenv("PROCESSED_DIR", "")
WATCH_POLL_SECONDS = _float("WATCH_POLL_SECONDS", 2.0)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".ts", ".m4v"}

# --- Snapshot-Abruf (analyze_url) ---
SNAPSHOT_TIMEOUT = _float("SNAPSHOT_TIMEOUT", 15.0)

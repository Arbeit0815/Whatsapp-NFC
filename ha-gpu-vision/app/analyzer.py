"""GPU-Bild-/Videoanalyse: Objekterkennung (YOLO) + Kennzeichenerkennung (fast-alpr)."""
import logging
import threading
from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

import config

log = logging.getLogger("analyzer")

# COCO-Klassen, die als "Ereignis" gemeldet werden (Klassen-ID -> deutscher Name)
INTERESTING_CLASSES = {
    0: "Person",
    1: "Fahrrad",
    2: "Auto",
    3: "Motorrad",
    5: "Bus",
    7: "LKW",
    15: "Katze",
    16: "Hund",
}

PLURALS = {
    "Person": "Personen",
    "Fahrrad": "Fahrräder",
    "Auto": "Autos",
    "Motorrad": "Motorräder",
    "Bus": "Busse",
    "LKW": "LKWs",
    "Katze": "Katzen",
    "Hund": "Hunde",
}

VEHICLE_LABELS = {"Auto", "Motorrad", "Bus", "LKW"}


@dataclass
class Detection:
    label: str
    confidence: float
    box: list  # [x1, y1, x2, y2]


@dataclass
class PlateResult:
    text: str
    confidence: float
    box: list


@dataclass
class AnalysisResult:
    source: str
    kind: str  # "image" | "video"
    detections: list = field(default_factory=list)
    plates: list = field(default_factory=list)
    frames_analyzed: int = 1
    description: str = ""

    @property
    def counts(self) -> dict:
        return dict(Counter(d.label for d in self.detections))

    @property
    def person_count(self) -> int:
        return self.counts.get("Person", 0)

    @property
    def vehicle_count(self) -> int:
        return sum(n for lbl, n in self.counts.items() if lbl in VEHICLE_LABELS)

    @property
    def plate_texts(self) -> list:
        return [p.text for p in self.plates]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "description": self.description,
            "counts": self.counts,
            "persons": self.person_count,
            "vehicles": self.vehicle_count,
            "plates": [
                {"text": p.text, "confidence": round(p.confidence, 3), "box": p.box}
                for p in self.plates
            ],
            "detections": [
                {"label": d.label, "confidence": round(d.confidence, 3), "box": d.box}
                for d in self.detections
            ],
            "frames_analyzed": self.frames_analyzed,
        }


def build_description(result: AnalysisResult) -> str:
    """Erzeugt eine deutsche Textbeschreibung des Ereignisses."""
    parts = []
    for label, count in sorted(result.counts.items(), key=lambda x: -x[1]):
        name = label if count == 1 else PLURALS.get(label, label)
        parts.append(f"{count} {name}")

    if not parts:
        text = "Keine relevanten Objekte erkannt."
    elif len(parts) == 1:
        text = f"{parts[0]} erkannt."
    else:
        text = f"{', '.join(parts[:-1])} und {parts[-1]} erkannt."

    if result.plate_texts:
        if len(result.plate_texts) == 1:
            text += f" Kennzeichen: {result.plate_texts[0]}."
        else:
            text += f" Kennzeichen: {', '.join(result.plate_texts)}."
    return text


class VisionAnalyzer:
    """Lädt die Modelle einmalig und analysiert Bilder/Videos auf der GPU."""

    def __init__(self):
        self._lock = threading.Lock()  # GPU-Inferenz seriell halten
        self.device = self._resolve_device()
        log.info("Lade YOLO-Modell '%s' auf Gerät '%s' ...", config.YOLO_MODEL, self.device)

        from ultralytics import YOLO

        self.yolo = YOLO(config.YOLO_MODEL)

        log.info(
            "Lade ALPR-Modelle (Detektor: %s, OCR: %s) ...",
            config.ALPR_DETECTOR_MODEL,
            config.ALPR_OCR_MODEL,
        )
        from fast_alpr import ALPR

        self.alpr = ALPR(
            detector_model=config.ALPR_DETECTOR_MODEL,
            ocr_model=config.ALPR_OCR_MODEL,
        )
        log.info("Modelle geladen. Analyzer bereit.")

    @staticmethod
    def _resolve_device() -> str:
        if config.DEVICE != "auto":
            return config.DEVICE
        try:
            import torch

            if torch.cuda.is_available():
                log.info("CUDA verfügbar: %s", torch.cuda.get_device_name(0))
                return "cuda:0"
        except Exception as exc:  # noqa: BLE001
            log.warning("CUDA-Prüfung fehlgeschlagen: %s", exc)
        log.warning("Keine GPU gefunden – nutze CPU (deutlich langsamer).")
        return "cpu"

    # ------------------------------------------------------------------ Frames

    def _detect_objects(self, frame: np.ndarray) -> list:
        results = self.yolo.predict(
            frame,
            device=self.device,
            conf=config.DETECTION_CONFIDENCE,
            classes=list(INTERESTING_CLASSES.keys()),
            verbose=False,
        )
        detections = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                label = INTERESTING_CLASSES.get(cls_id)
                if label is None:
                    continue
                detections.append(
                    Detection(
                        label=label,
                        confidence=float(box.conf[0]),
                        box=[round(float(v)) for v in box.xyxy[0].tolist()],
                    )
                )
        return detections

    def _detect_plates(self, frame: np.ndarray) -> list:
        plates = []
        for res in self.alpr.predict(frame):
            if res.ocr is None or not res.ocr.text:
                continue
            conf = float(res.ocr.confidence)
            if conf < config.PLATE_CONFIDENCE:
                continue
            bb = res.detection.bounding_box
            plates.append(
                PlateResult(
                    text=res.ocr.text.strip().upper(),
                    confidence=conf,
                    box=[int(bb.x1), int(bb.y1), int(bb.x2), int(bb.y2)],
                )
            )
        return plates

    def _analyze_frame(self, frame: np.ndarray):
        detections = self._detect_objects(frame)
        # ALPR nur ausführen, wenn ein Fahrzeug im Bild ist (spart GPU-Zeit),
        # bei leerer Objektliste trotzdem prüfen (Fahrzeug evtl. nur angeschnitten).
        has_vehicle = any(d.label in VEHICLE_LABELS for d in detections)
        plates = self._detect_plates(frame) if (has_vehicle or not detections) else []
        return detections, plates

    # ------------------------------------------------------------------ Public

    def analyze_image(self, frame: np.ndarray, source: str = "upload") -> AnalysisResult:
        with self._lock:
            detections, plates = self._analyze_frame(frame)
        result = AnalysisResult(
            source=source, kind="image", detections=detections, plates=plates
        )
        result.description = build_description(result)
        return result

    def analyze_image_file(self, path: str) -> AnalysisResult:
        frame = cv2.imread(path)
        if frame is None:
            raise ValueError(f"Bild konnte nicht gelesen werden: {path}")
        return self.analyze_image(frame, source=path)

    def analyze_image_bytes(self, data: bytes, source: str = "upload") -> AnalysisResult:
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Bilddaten konnten nicht dekodiert werden.")
        return self.analyze_image(frame, source=source)

    def analyze_video_file(self, path: str) -> AnalysisResult:
        """Analysiert ein Video: jedes n-te Frame, Ergebnisse werden aggregiert.

        Objektzahlen: Maximum gleichzeitig sichtbarer Objekte pro Klasse.
        Kennzeichen: pro Text die Erkennung mit der höchsten Konfidenz.
        """
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise ValueError(f"Video konnte nicht geöffnet werden: {path}")

        max_counts: Counter = Counter()
        best_frame_detections: dict = {}
        best_plates: dict = {}
        frames_analyzed = 0
        frame_idx = 0

        try:
            with self._lock:
                while frames_analyzed < config.VIDEO_MAX_FRAMES:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if frame_idx % config.VIDEO_FRAME_STRIDE != 0:
                        frame_idx += 1
                        continue
                    frame_idx += 1
                    frames_analyzed += 1

                    detections, plates = self._analyze_frame(frame)

                    counts = Counter(d.label for d in detections)
                    for label, n in counts.items():
                        if n > max_counts[label]:
                            max_counts[label] = n
                            best_frame_detections[label] = [
                                d for d in detections if d.label == label
                            ]
                    for plate in plates:
                        prev = best_plates.get(plate.text)
                        if prev is None or plate.confidence > prev.confidence:
                            best_plates[plate.text] = plate
        finally:
            cap.release()

        all_detections = [d for dets in best_frame_detections.values() for d in dets]
        result = AnalysisResult(
            source=path,
            kind="video",
            detections=all_detections,
            plates=list(best_plates.values()),
            frames_analyzed=frames_analyzed,
        )
        result.description = build_description(result)
        return result

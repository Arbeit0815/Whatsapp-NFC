"""Überwacht einen Ordner auf neue Bilder/Videos (z. B. Frigate-Clips, Kamera-FTP)."""
import logging
import os
import shutil
import threading
import time

import config

log = logging.getLogger("watcher")


class FolderWatcher(threading.Thread):
    """Pollt WATCH_DIR und analysiert neue Dateien, sobald sie fertig geschrieben sind."""

    def __init__(self, analyzer, on_result):
        super().__init__(daemon=True, name="folder-watcher")
        self.analyzer = analyzer
        self.on_result = on_result
        self._seen: dict = {}  # Pfad -> (Größe, mtime) beim letzten Poll
        self._processed: set = set()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        os.makedirs(config.WATCH_DIR, exist_ok=True)
        if config.PROCESSED_DIR:
            os.makedirs(config.PROCESSED_DIR, exist_ok=True)
        # Beim Start vorhandene Dateien ignorieren, nur neue verarbeiten
        for name in os.listdir(config.WATCH_DIR):
            self._processed.add(os.path.join(config.WATCH_DIR, name))
        log.info("Überwache Ordner: %s", config.WATCH_DIR)

        while not self._stop.wait(config.WATCH_POLL_SECONDS):
            try:
                self._poll()
            except Exception:  # noqa: BLE001
                log.exception("Fehler bei der Ordnerüberwachung")

    def _poll(self):
        for name in sorted(os.listdir(config.WATCH_DIR)):
            path = os.path.join(config.WATCH_DIR, name)
            if path in self._processed or not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS | config.VIDEO_EXTENSIONS:
                continue

            stat = os.stat(path)
            signature = (stat.st_size, stat.st_mtime)
            if self._seen.get(path) != signature:
                # Datei wird evtl. noch geschrieben -> nächsten Poll abwarten
                self._seen[path] = signature
                continue

            self._processed.add(path)
            self._seen.pop(path, None)
            self._handle(path, ext)

    def _handle(self, path: str, ext: str):
        log.info("Neue Datei erkannt: %s", path)
        try:
            if ext in config.VIDEO_EXTENSIONS:
                result = self.analyzer.analyze_video_file(path)
            else:
                result = self.analyzer.analyze_image_file(path)
            self.on_result(result)
        except Exception:  # noqa: BLE001
            log.exception("Analyse fehlgeschlagen für %s", path)
            return

        if config.PROCESSED_DIR:
            try:
                shutil.move(
                    path, os.path.join(config.PROCESSED_DIR, os.path.basename(path))
                )
                self._processed.discard(path)
            except OSError:
                log.exception("Konnte Datei nicht verschieben: %s", path)

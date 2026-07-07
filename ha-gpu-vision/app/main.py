"""HA GPU Vision – Bild-/Videoanalyse mit GPU für Home Assistant.

Startet einen HTTP-Server (FastAPI) und optional MQTT + Ordnerüberwachung.

Endpunkte:
  GET  /health              – Status & Geräteinfo
  POST /analyze             – Bild/Video als Datei-Upload analysieren
  POST /analyze_url         – Bild von einer URL (z. B. Kamera-Snapshot) analysieren
  POST /analyze_path        – Datei analysieren, die im Container gemountet ist
"""
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Optional

import anyio
import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

import config
from analyzer import VisionAnalyzer
from mqtt_client import HomeAssistantMqtt
from watcher import FolderWatcher

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
log = logging.getLogger("main")

analyzer: Optional[VisionAnalyzer] = None
mqtt: Optional[HomeAssistantMqtt] = None
watcher: Optional[FolderWatcher] = None


def publish(result) -> dict:
    data = result.to_dict()
    if mqtt is not None:
        mqtt.publish_result(data)
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    global analyzer, mqtt, watcher
    analyzer = VisionAnalyzer()
    if config.MQTT_ENABLED:
        mqtt = HomeAssistantMqtt()
        mqtt.start()
    if config.WATCH_ENABLED:
        watcher = FolderWatcher(analyzer, publish)
        watcher.start()
    yield
    if watcher is not None:
        watcher.stop()
    if mqtt is not None:
        mqtt.stop()


app = FastAPI(title="HA GPU Vision", lifespan=lifespan)


class UrlRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    source: Optional[str] = None


class PathRequest(BaseModel):
    path: str


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": analyzer.device if analyzer else "wird geladen",
        "yolo_model": config.YOLO_MODEL,
        "mqtt": mqtt.connected if mqtt else False,
        "watch_dir": config.WATCH_DIR if config.WATCH_ENABLED else None,
    }


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """Bild oder Video hochladen und analysieren."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    data = await file.read()

    if ext in config.VIDEO_EXTENSIONS:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = await anyio.to_thread.run_sync(
                analyzer.analyze_video_file, tmp_path
            )
        finally:
            os.unlink(tmp_path)
    else:
        try:
            result = await anyio.to_thread.run_sync(
                lambda: analyzer.analyze_image_bytes(data, source=file.filename or "upload")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return publish(result)


@app.post("/analyze_url")
async def analyze_url(req: UrlRequest):
    """Bild von einer URL abrufen (z. B. Kamera-Snapshot) und analysieren."""

    def fetch_and_analyze():
        auth = (req.username, req.password) if req.username else None
        resp = requests.get(req.url, auth=auth, timeout=config.SNAPSHOT_TIMEOUT)
        resp.raise_for_status()
        return analyzer.analyze_image_bytes(resp.content, source=req.source or req.url)

    try:
        result = await anyio.to_thread.run_sync(fetch_and_analyze)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Abruf fehlgeschlagen: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return publish(result)


@app.post("/analyze_path")
async def analyze_path(req: PathRequest):
    """Eine im Container erreichbare Datei (Bild oder Video) analysieren."""
    if not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {req.path}")
    ext = os.path.splitext(req.path)[1].lower()
    try:
        if ext in config.VIDEO_EXTENSIONS:
            result = await anyio.to_thread.run_sync(
                analyzer.analyze_video_file, req.path
            )
        else:
            result = await anyio.to_thread.run_sync(
                analyzer.analyze_image_file, req.path
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return publish(result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HTTP_HOST, port=config.HTTP_PORT)

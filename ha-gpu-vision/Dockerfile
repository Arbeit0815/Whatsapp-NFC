# HA GPU Vision – Bild-/Videoanalyse mit NVIDIA-GPU für Home Assistant
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    # Modell-Downloads (YOLO & ALPR) hier ablegen -> per Volume persistierbar
    YOLO_CONFIG_DIR=/models/ultralytics \
    XDG_CACHE_HOME=/models/cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# PyTorch mit CUDA 12.1
RUN pip3 install --no-cache-dir torch torchvision \
        --index-url https://download.pytorch.org/whl/cu121

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/

WORKDIR /app
EXPOSE 8099

CMD ["python3", "main.py"]

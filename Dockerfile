FROM python:3.12-slim

WORKDIR /app

# OS deps for compiled wheels (birdnetlib, librosa, psycopg2-binary)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# FIX: Force-install a compatible NumPy version before the requirements file
RUN pip install --no-cache-dir "numpy<2.0"

# Install the rest of the requirements
RUN pip install --no-cache-dir -r requirements.txt

# Apply tflite_runtime shim for Python 3.12 (ai-edge-litert compatibility)
COPY setup_birdnet.py .
RUN python setup_birdnet.py

COPY . .

# Directories created here are overridden by volume mounts in prod
RUN mkdir -p instance static/uploads

EXPOSE 80

# 1 worker — keeps the in-memory news cache coherent (no fragmentation across processes)
# 120s timeout — accommodates BirdNET model init on first upload (~90s cold start)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "1", "--timeout", "120"]

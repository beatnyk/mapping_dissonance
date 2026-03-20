FROM python:3.12-slim

WORKDIR /app

# Install OS deps (needed for some compiled wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directories that need to persist (overridden by volume mounts)
RUN mkdir -p instance static/uploads

EXPOSE 80

# Use gunicorn on port 80 so Dokploy's Traefik integration works out of the box
# (same "Port 80 Fix" principle as sftp-manager)
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "2", "--timeout", "120"]

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies: fonts for Pillow text rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-dejavu-extra \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command: jalankan FastAPI server
# Untuk Cloud Run Jobs, ganti command/entrypoint saat execute atau di gcloud run jobs create
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
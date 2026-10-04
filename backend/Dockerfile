# Backend image shared by the api and worker services.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System packages: OCR engine (English) for scanned PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned dependencies first to maximise layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Non-root runtime user; log/error/storage dirs are created here so named volumes inherit ownership.
RUN useradd --create-home --uid 1000 hc \
    && mkdir -p /app/logs /app/errors /app/storage/exports /app/storage/quarantine \
    && chown -R hc:hc /app

COPY --chown=hc:hc app ./app

USER hc
EXPOSE 8000

# Container-level liveness probe against /health.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
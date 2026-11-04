# CivicPulse AI — analytics API image.
# Build from the repo root:  docker build -f services/api/Dockerfile -t civicpulse-api .
# STATUS: written but NOT yet built/verified (Docker daemon unavailable during Milestone 1).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services/api ./services/api
COPY configs ./configs
COPY scripts ./scripts
COPY data/samples ./data/samples
RUN pip install . \
 && python scripts/build_sample_warehouse.py --data-dir /app/sample-data \
 && useradd --system --uid 10001 app \
 && chown -R app /app

USER app
# Default to the bundled real-data sample; mount a full warehouse at /app/data and set
# CIVICPULSE_DATA_DIR=/app/data to serve the complete snapshot.
ENV CIVICPULSE_DATA_DIR=/app/sample-data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready').status==200 else 1)"
CMD ["uvicorn", "civicpulse_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

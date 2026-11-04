# CivicPulse AI — analytics + predictions API image.
# Build from the repo root:  docker build -f services/api/Dockerfile -t civicpulse-api .
#
# Stage 1 builds the warehouse from the committed real-data sample and trains smoke-scale models
# (needs the `ml` extra). Stage 2 ships only the API runtime: no LightGBM/scikit-learn, because the
# API serves stored model outputs and never trains or executes models.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CIVICPULSE_REPO_ROOT=/app
# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services/api ./services/api
COPY configs ./configs
COPY scripts ./scripts
COPY data/samples ./data/samples
RUN pip install ".[ml]" \
 && python scripts/build_sample_warehouse.py --data-dir /app/sample-data --with-models

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CIVICPULSE_REPO_ROOT=/app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services/api ./services/api
COPY configs ./configs
RUN pip install . \
 && useradd --system --uid 10001 app
COPY --from=builder --chown=app /app/sample-data /app/sample-data

USER app
# Default to the bundled real-data sample; mount a full warehouse at /app/data and set
# CIVICPULSE_DATA_DIR=/app/data to serve the complete snapshot (and its trained models).
ENV CIVICPULSE_DATA_DIR=/app/sample-data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready').status==200 else 1)"
CMD ["uvicorn", "civicpulse_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

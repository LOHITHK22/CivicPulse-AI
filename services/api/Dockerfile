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
COPY services/mcp ./services/mcp
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
COPY services/mcp ./services/mcp
COPY configs ./configs
RUN pip install . \
 && useradd --system --uid 10001 app \
 && mkdir -p /app/artifacts && chown app /app/artifacts
COPY --from=builder --chown=app /app/sample-data /app/sample-data

USER app
# Data source, in order:
#  * CIVICPULSE_ARTIFACT_URL + CIVICPULSE_ARTIFACT_SHA256: the pinned full snapshot is downloaded, verified and
#    installed under /app/artifacts at start (reused if already present), then served (deployments);
#  * otherwise the bundled real-data sample (or a warehouse mounted at /app/data with CIVICPULSE_DATA_DIR).
# CIVICPULSE_REQUIRE_ARTIFACT=true refuses to start on the sample. See docs/deployment/ARTIFACT_MANIFEST.md.
ENV CIVICPULSE_DATA_DIR=/app/sample-data PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health/ready' % os.environ.get('PORT','8000')).status==200 else 1)"
# start.py restores artifacts, then execs uvicorn (one worker, bounded concurrency, graceful shutdown on SIGTERM).
CMD ["python", "/app/services/api/start.py"]

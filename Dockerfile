# AI Food Analyzer — two-stage build: dependencies are compiled to wheels in a
# builder stage so the runtime image ships only the installed packages and the
# application code. Runs either the HTTP API (default CMD) or the CLI.

# ---------- stage 1: build wheels ----------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- stage 2: runtime ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

# Application code. `scripts/` and `demo_ai.py` are included so the graded
# demo (`python demo_ai.py --offline`) and the sequential-vs-concurrent
# benchmark are reproducible inside the container, not just on a dev machine.
COPY ai/ ./ai/
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY tests/ ./tests/
COPY demo_ai.py foodanalyzer.py pytest.ini requirements-ai.txt ./

# Run as an unprivileged user; data/uploads is created up front because
# src/api.py writes uploaded photos there (see Settings.image_upload_dir)
# and a mounted volume must already be owned by the runtime user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data/uploads \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]

# AI Food Analyzer

Analyzes a photo of a meal, identifies ingredients via a VLM, looks up nutrition
facts (USDA), and returns/stores kcal, protein, carbs and fat totals. Available
as both a CLI and an HTTP API.

## Setup

### 1. Install dependencies

    python -m venv .venv
    source .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install -r requirements.txt

### 2. Configure environment

Copy `.env.example` to `.env` and fill in the values:

    cp .env.example .env

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | yes | — | Gemini API key used by the VLM provider |
| `USDA_API_KEY` | yes | — | USDA FoodData Central API key for nutrition lookups |
| `DATABASE_URL` | yes | — | Postgres connection string, e.g. `postgresql://user:pass@localhost:5432/foodanalyzer` |
| `LLM_PROVIDER` | no | `gemini` | VLM provider identifier |
| `LLM_MODEL` | no | `gemini-3.6-flash` | Model name passed to the VLM provider |
| `NUTRITION_PROVIDER` | no | `usda` | Nutrition provider identifier |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `NUTRITION_CACHE_TTL_SECONDS` | no | `86400` | How long a nutrition lookup is cached before re-querying USDA |
| `MAX_IMAGE_SIZE_MB` | no | `5` | Max accepted upload/input image size |
| `HTTP_PORT` | no | `8000` | Port the API listens on |
| `IMAGE_UPLOAD_DIR` | no | `data/uploads` | Where uploaded photos are persisted (API only) |

### 3. Database

The app expects a reachable Postgres instance at `DATABASE_URL`. Schema is created automatically on first connection.

## Running

### CLI

    python -m foodanalyzer analyze data/rice_chicken.png
    python -m foodanalyzer analyze data/rice_chicken.png --no-save
    python -m foodanalyzer history --limit 5

### HTTP API

    uvicorn src.api:app --host 0.0.0.0 --port 8000

Health check:

    curl http://localhost:8000/health
    # {"status": "ok"}

Analyze a photo:

    curl -X POST http://localhost:8000/analyze -F "file=@data/rice_chicken.png"

On Windows PowerShell use `curl.exe`, not `curl` -- the latter is an alias for
`Invoke-WebRequest`, which does not understand `-F`.

Example response:

    {
      "meal_recognized": true,
      "ingredients": [
        {"name": "egg", "weight_g": 50.0, "confidence": 0.9,
         "kcal": 78.0, "protein": 6.5, "carbs": 0.5, "fat": 5.5}
      ],
      "total_weight_g": 50.0,
      "totals": {"kcal": 78.0, "protein": 6.5, "carbs": 0.5, "fat": 5.5}
    }

### Docker

The application needs PostgreSQL, so the supported path is Compose, which
brings up both services and waits for the database to pass its healthcheck
before starting the API:

    docker compose up --build

The API is then on `http://localhost:8000`. `.env` is optional for Compose
itself, but the API container needs `GOOGLE_API_KEY` and `USDA_API_KEY` to do
any real work; `DATABASE_URL` is supplied by Compose and overrides whatever is
in `.env`.

The image carries the CLI and the offline demo as well as the server:

    docker compose run --rm api python -m foodanalyzer analyze data/rice_chicken.png
    docker compose run --rm api python demo_ai.py --offline

Building the image on its own works too, but a container started with
`docker run` has no database to talk to and will exit at startup unless you
point `DATABASE_URL` at a reachable PostgreSQL instance yourself.

## Testing

    pytest --cov=src --cov=ai

102 tests. Coverage is 93% over `src/` and 64% including the provided
`ai/` package. Our own modules: `api.py` 90%, `cli.py` 84%,
`ai_service.py` 97%, and `nutrition.py`, `pipeline.py`, `config.py`, `models.py`
at 100%. The remaining uncovered lines are in the provided `ai/providers/`
adapters, which we neither modify nor exercise offline.

Tests use fakes (`FakeVLM`, `FakeNutrition` in `tests/conftest.py`) — no real API keys or network access needed to run the suite.

The provided smoke tests are untouched and pass:

    pytest tests/test_ai_smoke.py

The AI pipeline can also be exercised without any API keys at all:

    python demo_ai.py --offline

## Performance

Parallelizing nutrition lookups (see `src/pipeline.py`) instead of looking them up one at a time:

| | Time |
|---|---|
| Sequential | 15.69s |
| Parallel | 3.16s |
| Speedup | 4.96× |

Workload: 10 distinct ingredients, a fresh `NutritionService` per run so neither
side gets a cache hit. Parallelism is bounded by `MAX_PARALLEL_LOOKUPS = 10` in
`src/pipeline.py` so a burst cannot trip USDA's rate limit. The bottleneck after
parallelising is the slowest single USDA round-trip, which is why the speedup is
~5x rather than 10x.

Reproduce with a real `USDA_API_KEY`:

    python scripts/benchmark_concurrency.py



## Architecture

- `src/config.py` — typed settings loaded from `.env`
- `src/ai_service.py` — wraps the provided `ai` package with retries, timeouts, and nutrition caching
- `src/nutrition.py` — audits USDA energy values against the macronutrients and corrects kilojoules reported as kilocalories
- `src/pipeline.py` — runs nutrition lookups for all ingredients in parallel
- `src/cli.py` / `src/api.py` — two front doors onto the same pipeline
- `src/storage/repository.py` — Postgres persistence

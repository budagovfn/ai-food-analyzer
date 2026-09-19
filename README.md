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

[FILL IN: cross-check this list against report §7.2 — add anything it documents that isn't listed here]

### 3. Database

The app expects a reachable Postgres instance at `DATABASE_URL`. Schema is created automatically on first connection.

## Running

### CLI

    python -m foodanalyzer analyze data/sample_meal.jpg
    python -m foodanalyzer analyze data/sample_meal.jpg --no-save
    python -m foodanalyzer history --limit 5

### HTTP API

    uvicorn src.api:app --host 0.0.0.0 --port 8000

Health check:

    curl http://localhost:8000/health
    # {"status": "ok"}

Analyze a photo:

    curl -X POST http://localhost:8000/analyze \
      -F "file=@data/sample_meal.jpg"

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

    docker build -t foodanalyzer .
    docker run --env-file .env -p 8000:8000 foodanalyzer

## Testing

    pytest --cov=src

Tests use fakes (`FakeVLM`, `FakeNutrition` in `tests/conftest.py`) — no real API keys or network access needed to run the suite.

## Performance

Parallelizing nutrition lookups (see `src/pipeline.py`) instead of looking them up one at a time:

| | Time |
|---|---|
| Sequential | 15.69s |
| Parallel | 3.16s |
| Speedup | 4.96× |



## Architecture

- `src/config.py` — typed settings loaded from `.env`
- `src/ai_service.py` — wraps the provided `ai` package with retries, timeouts, and nutrition caching
- `src/pipeline.py` — runs nutrition lookups for all ingredients in parallel
- `src/cli.py` / `src/api.py` — two front doors onto the same pipeline
- `src/storage/repository.py` — Postgres persistence

# Architecture

## Overview

The provided `ai/` package is treated as a clean, external boundary — an
SDK we call but never modify (per the brief's §4.8: "Do not modify the AI
module's public interface"). Everything under `src/` is our own
software-engineering layer built around it.

```
                ┌─────────────────────────────┐
                │   ai/ package (provided)     │
                │   identify_ingredients()     │
                │   USDAProvider.lookup()      │
                └───────────────┬─────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
┌───────▼────────┐      ┌────────▼─────────┐      ┌───────▼────────┐
│ Config & storage│      │ Concurrency &     │      │ API, CLI &      │
│                 │      │ AI calls          │      │ tests           │
│ config.py       │      │ ai_service.py     │      │ cli.py          │
│ repository.py   │      │ pipeline.py       │      │ api.py (WIP)    │
│ (Postgres+blobs)│      │ (parallel calls)  │      │ tests + Docker  │
└─────────────────┘      └───────────────────┘      └─────────────────┘
```

Shared responsibility across all three areas: the smoke tests shipped in
`tests/test_ai_smoke.py` must keep passing — nobody's changes are allowed
to break the contract with the provided `ai/` package.

## Module ownership

| Area | Files | Owner | Status |
|---|---|---|---|
| Config & storage | `src/config.py`, `src/storage/repository.py` | _(fill in name)_ | ✅ Done |
| Concurrency & AI calls | `src/ai_service.py`, `src/pipeline.py` | _(fill in name)_ | 🟡 Retries/timeout/cache done; parallel `asyncio.gather` batch not yet done |
| API, CLI & tests | `src/cli.py`, `src/api.py`, `tests/`, `Dockerfile` | _(fill in name)_ | 🟡 CLI done; HTTP API, Docker, and full coverage not yet done |

## Design decisions

### Config (`src/config.py`)
Typed settings via `pydantic-settings`, reading `.env`. Two subtleties
worth documenting in the report:
- The `.env` file must resolve to an **absolute path** independent of the
  process's working directory — PyCharm sometimes launches scripts with
  `src/` as the working directory, which broke a relative `"./.env"` path.
- `pydantic-settings` only populates our own `Settings` object; it does
  **not** export values into `os.environ`. The provided `ai/` package
  reads `LLM_PROVIDER`/`GOOGLE_API_KEY`/etc. directly via `os.getenv(...)`,
  so `config.py` also calls `load_dotenv()` to push `.env` values into the
  real process environment for `ai/` to see.

### Storage (`src/storage/repository.py`)
`asyncpg` directly (no ORM) — the brief allows "PostgreSQL or AsyncPG
minimum," and a raw driver keeps the dependency surface small. One
`meal_analyses` table (`id`, `created_at`, `image_path`, `ingredients`
JSONB, `totals` JSONB). All I/O errors are wrapped into `StorageError` so
callers never touch asyncpg-specific exceptions directly.

### AI service wrapper (`src/ai_service.py`)
Wraps every `ai.*` call with:
- `tenacity`-based retry, exponential backoff (3 attempts)
- A per-call timeout enforced via a shared `ThreadPoolExecutor` (the
  provided `ai/` SDK is synchronous, so a thread pool is the simplest way
  to bound an otherwise-uninterruptible call)
- Structured logging (timings, cache hits/misses) via the `logging` module
- A local TTL cache for nutrition lookups (`NUTRITION_CACHE_TTL_SECONDS`)
- One exception type (`AIServiceError`) for callers, mirroring
  `StorageError`'s pattern

### CLI (`src/cli.py`)
`python -m foodanalyzer analyze <path>` ties config → ai_service → storage
into one runnable command (the brief's minimum runnable demo, §5.2.2).
Input validation happens before any AI call: file exists, extension is
`.png/.jpg/.jpeg`, size ≤ `MAX_IMAGE_SIZE_MB`. Graceful degradation: if one
ingredient's nutrition lookup fails, the analysis still completes with the
remaining ingredients rather than aborting entirely.

## Known limitations (candidates for the report's failure-mode analysis)

1. **USDA search picks the first result without a relevance check.**
   Observed: "steamed broccoli" resolved to ~1620 kcal/100g instead of the
   real ~35 kcal/100g — likely matched an unrelated USDA entry with a
   similar name. `USDAProvider.lookup()` (in `ai/nutrition.py`, which we
   cannot modify) always takes `foods[0]` from the search response.
2. **Nutrition lookups are sequential**, not yet parallelized — next
   milestone per the brief's Day 6-8 Concurrency phase.
3. **No rate-limiting semaphore yet** — retries are bounded per call, but
   there's no bound on how many `ai.*` calls can be in flight at once.

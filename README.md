# AI Food Analyzer

Final project for AI-ENG-110 (AI Academy, National AI Center, Spring 2026) — Topic 2:
AI Food Analyzer. Identifies ingredients in a meal photo via a VLM (Gemini),
looks up nutrition facts per ingredient via USDA FoodData Central, and
reports total calories + macronutrient breakdown.

The `ai/` package (VLM calls, USDA lookup, pure calculator) is provided by
the course and must not be modified. Everything in `src/` is our own
software-engineering layer wrapping it with config, retries, caching,
storage, a CLI, and (soon) an HTTP API.

## Project status

Currently at the end of the **Skeleton** milestone: config → AI service
wrapper → storage → CLI works end-to-end. Concurrency, full robustness
(rate limiting, structured logging everywhere), the HTTP API, Docker, and
the ≥60% test coverage target are still in progress — see `docs/architecture.md`
for who owns what.

## Setup

### 1. Clone and create a virtual environment

```powershell
git clone https://github.com/budagovfn/ai-food-analyzer.git
cd ai-food-analyzer
py -m venv venv
.\venv\Scripts\Activate.ps1
```

(macOS/Linux: `python3 -m venv venv && source venv/bin/activate`)

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in your own keys — **each team member uses their own
keys**, never share or commit them:

| Variable | Where to get it |
|---|---|
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey (free tier) |
| `USDA_API_KEY` | https://fdc.nal.usda.gov/api-key-signup (free) |

We use `LLM_PROVIDER=gemini` with `LLM_MODEL=gemini-3.6-flash` (the model
originally referenced in the brief, `gemini-2.5-flash`, has since been
retired by Google for new users).

### 4. Database

Requires a local PostgreSQL instance (version 16+) with a `foodanalyzer`
database. Two options:

**Docker** (recommended, matches the eventual Dockerfile setup):
```powershell
docker run -d --name pg -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
docker exec -it pg psql -U postgres -c "CREATE DATABASE foodanalyzer;"
```

**Native install**: install PostgreSQL from postgresql.org, then:
```powershell
psql -U postgres -c "CREATE DATABASE foodanalyzer;"
```

Either way, make sure `DATABASE_URL` in `.env` matches the password you set:
```
DATABASE_URL=postgresql+asyncpg://postgres:<your-password>@localhost:5432/foodanalyzer
```

## Running

### Verify the provided AI module works

```powershell
python demo_ai.py --offline   # no network, no API keys needed
python demo_ai.py             # real Gemini + USDA calls
```

### Run the CLI

```powershell
python -m foodanalyzer analyze data\broccoli_egg.png
python -m foodanalyzer analyze data\broccoli_egg.png --no-save
python -m foodanalyzer history --limit 10
```

Sample output:
```
ingredient        g   kcal  protein  carbs  fat
-----------------------------------------------
steamed broccoli  35  567   3.4      26.3   1.8
hard-boiled egg   50  78    6.3      0.6    5.3
-----------------------------------------------
TOTAL             85  644   9.7      26.9   7.1
Saved as analysis #1
```

## Testing

```powershell
pytest tests/test_ai_smoke.py    # provided smoke tests — must never break
pytest                            # full suite
pytest --cov=src --cov=ai         # with coverage report
```

## Known issues / failure modes

- **USDA lookup can return an implausible match.** `USDAProvider.lookup()`
  takes the first search result (`foods[0]`) without checking relevance.
  Observed: "steamed broccoli" resolved to ~1620 kcal/100g (real broccoli
  is ~35 kcal/100g), likely a mismatched product in USDA's database. This
  is a documented failure mode for the report's incident analysis — not
  yet fixed in code.
- Nutrition lookups currently run **sequentially** per ingredient; this is
  the next milestone (parallelize via `asyncio.gather`, per brief §5.2.2).

## Project layout

```
ai/                  # provided — do not modify
src/
  config.py          # typed settings, reads .env
  ai_service.py       # retries, timeouts, logging, nutrition cache
  storage/
    repository.py     # PostgreSQL persistence (asyncpg)
  cli.py               # `analyze` and `history` commands
  models.py            # API response schemas (HTTP API not yet built)
foodanalyzer.py        # entry point shim for `python -m foodanalyzer`
tests/
docs/
data/                  # sample meal photos (provided)
```

## Team

See `docs/architecture.md` for the module ownership split.

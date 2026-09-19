# Run artefacts

Output of real runs against the code at this commit, captured so a reader can
see what the system produces without setting up keys or a database.

| File | Command |
|---|---|
| `demo-offline.txt` | `python demo_ai.py --offline` — the full analysis pipeline on a sample photo, no API keys required |
| `pytest-coverage.txt` | `pytest -q --cov=src --cov=ai --cov-report=term` — 102 tests, 93% over `src/`, 64% including the provided `ai/` |
| `mypy.txt` | `mypy src/` with the repository's `mypy.ini` |
| `cli-help.txt` | `python -m foodanalyzer --help` and `analyze --help` |
| `analyze-http.json` | `POST /analyze` against the running container with live Gemini and USDA keys |

`analyze-http.json` has to be captured on a machine holding valid API keys:

    docker compose up --build
    curl.exe -F "file=@data/rice_chicken.png" http://localhost:8000/analyze > artefacts/analyze-http.json

Capture it *after* the kilojoule fix in `src/nutrition.py` is in place; a
capture from before it reports energy roughly 4.2x too high.

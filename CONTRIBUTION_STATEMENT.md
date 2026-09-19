# Contribution Statement

**Team:** AI Food Analyzer Team
**Topic:** Topic 2 — AI Food Analyzer
**Repository:** [https://github.com/budagovfn/ai-food-analyzer](https://github.com/budagovfn/ai-food-analyzer)
**Final tag:** `v1.0-final`
**Submission date:** _[YYYY-MM-DD]_

---

## Note on the commit counts

All three of us have committed under more than one git identity, and merges made through the GitHub web interface carry a `users.noreply.github.com` address rather than the author's own. A raw `git shortlog -sne origin/main` therefore lists six authors for a team of three and makes every share look smaller than it is.

The repository contains a `.mailmap` that collapses these identities. All percentages below come from `git shortlog -sn origin/main` **with** that file applied, and reproduce exactly:

| Member | Commits | Share |
|---|---|---|
| Farhad Budagov | 19 / 31 | ~61% |
| Mikayil Hasanov | 8 / 31 | ~26% |
| Nihad Gojayev | 4 / 31 | ~13% |

Every member is above the 10% floor. The distribution is uneven and we state that plainly rather than round it away: Nihad joined the codebase later than the other two, and his contribution is concentrated in the test suite for the two entry points, which was the largest single gap in the project at the time he picked it up.

---

## Member A — Farhad Budagov (`@budagovfn`)

**Owned (sole author of these files / PRs):**
- `src/config.py` (typed settings, `.env` loading via `pydantic-settings` + `python-dotenv`)
- `src/storage/repository.py`, `src/storage/__init__.py` (PostgreSQL history log, `AnalysisRepository`)
- `src/nutrition.py` (`UnitCheckedNutritionProvider` — the Atwater cross-check that corrects USDA energy values reported in kilojoules) and `tests/test_nutrition_units.py`
- `tests/test_config.py`, `tests/test_storage.py`
- `src/api.py` bug fixes: Windows temp-file `PermissionError`, dangling upload path, per-request DB pool, missing `python-multipart` dependency
- Hardening of the container setup on top of PR #8: two-stage build, unprivileged runtime user, named volume for uploads, optional `.env` in compose (PR #9)
- `.mailmap`
- PRs: #1 (config + storage), #6 (review and fix commit), #9 (Docker hardening), #10 (test-suite integration and fixes)

**Co-owned (paired or substantially edited):**
- `src/api.py` (original endpoint by Mikayil Hasanov, PR #6; Windows and DB-pool fixes by Farhad in the same PR)
- `Dockerfile`, `docker-compose.yml`, `.dockerignore` (first version by Mikayil Hasanov, PR #8; hardening by Farhad, PR #9)
- Git/GitHub workflow for the whole team (branch-based development, merge-commit-not-squash policy to preserve per-author history, PR review text)
- `report/report.tex`, `slides/slides.tex`, this document

**Reviewed (PRs reviewed and merged):**
- PRs #1–#8

**Share of commits:** ~61% (19 of 31)

---

## Member B — Mikayil Hasanov (`@mickael044`)

**Owned:**
- `src/ai_service.py` (retry/timeout/logging wrapper around `ai.*`, `NutritionService` with TTL cache)
- `src/pipeline.py` (`asyncio.gather` + `Semaphore(10)` concurrent nutrition lookups)
- `src/api.py` (original `POST /analyze` and `GET /health` endpoints, PR #6, before Farhad's Windows and DB-pool fixes)
- `tests/test_ai_service.py`, `tests/test_pipeline.py` (PR #7)
- `src/models.py` (initial version)
- `Dockerfile`, `docker-compose.yml`, `.dockerignore` (first version, PR #8)

**Co-owned:**
- `src/api.py` (with Farhad Budagov — see above)
- Container setup (with Farhad Budagov — first version PR #8, hardening PR #9)

**Reviewed:**
- PR #3 (`mickael044-patch-1`, `models.py`)

**Share of commits:** ~26% (8 of 31)

---

## Member C — Nihad Gojayev (`@justpr09rammer`)

**Owned:**
- `tests/test_api.py` — the `TestClient`-driven HTTP test suite for `POST /analyze` and `GET /health`: happy path, unrecognised meal, partially failed nutrition lookup, and the 400/502 error branches, with the storage layer stubbed so the suite stays offline
- `tests/test_cli.py` — the CLI test suite for `cmd_analyze` and `cmd_history`, covering input validation, exit codes, and the graceful-degradation paths
- `README.md`

Together these lifted `src/api.py` from 0% to 90% coverage and `src/cli.py` from 0% to 84%, and took the project over the 60% coverage threshold in the rubric (40% → 64%).

**Co-owned:**
- Test-suite maintenance: two failures in the above suites (an upload directory removed by a fixture teardown, and an assertion that did not match the CLI's actual output) were fixed in PR #10 before merge.

**Reviewed (PRs reviewed and merged):**
- PR #9 (Docker hardening), PR #10 (test-suite integration)

**Share of commits:** ~13% (4 of 31)

---

## AI tool disclosure (also in §9 of the report)

We used AI coding assistants as follows. Each row lists the module, the assistant, and what the team did with the output. Model names are the ones configured for the session in which that work was done.

| Module / file | Assistant | What we did with it |
|---|---|---|
| `src/api.py` | Claude (Sonnet) | Reviewed PR #6, identified and fixed the Windows `NamedTemporaryFile` `PermissionError`, the dangling `image_path` on a deleted temp file, and the per-request DB connection pool. Team ran the fixed endpoint end-to-end against real Gemini/USDA keys before merging. |
| `tests/test_ai_service.py`, `tests/test_pipeline.py` | Claude (Sonnet) | Reviewed PR #7; identified that `test_lookup_all_runs_concurrently_not_sequentially` does not actually prove concurrency, because an autouse fixture disables `time.sleep` file-wide. Confirmed experimentally; left as a documented, unfixed issue rather than patched under time pressure. |
| Git/GitHub workflow | Claude (Sonnet) | Explained merge-vs-squash trade-offs (we used "Create a merge commit" throughout, to preserve per-author history for the commit-balance rubric item) and drafted PR review text; approve and merge actions performed by team members. |
| `report/report.tex`, `slides/slides.tex` | Claude (Sonnet, then Opus) | Drafted from our actual commit history, source code, and real incidents; later revised to correct statements that our own subsequent work had made false (coverage figures, the "no Dockerfile yet" items, the response-validation claim). Reviewed by the team before submission. |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | Claude (Opus) | Reviewed PR #8 and wrote the hardening in PR #9: two-stage build, unprivileged user, volume for uploads, optional `.env`. Verified by a team member with `docker compose up --build` and live `curl` calls against the running container. |
| `tests/test_api.py`, `tests/test_cli.py` | Claude (Opus) | Diagnosed and fixed two failures in Nihad's suites during the PR #10 merge (a fixture teardown removing a directory `src/api.py` only creates at import time, and an assertion string that did not match the CLI output). The tests themselves were written by Nihad Gojayev. |
| `src/nutrition.py`, `tests/test_nutrition_units.py` | Claude (Opus) | Found the USDA kilojoule/kilocalorie defect by cross-checking a live `POST /analyze` response against the Atwater factors, traced the root cause to unit-blind nutrient extraction in the provided `ai/nutrition.py`, and wrote the compensating provider and its regression tests. Reviewed and reproduced by the team. |
| `.mailmap` | Claude (Opus) | Identified that six git identities were masking a three-person team and produced the mapping. Verified against `git shortlog -sn`. |

The defect in the provided `ai/nutrition.py` is to be reported to the instructor as an issue, per Appendix C of the brief; we did not edit the `ai/` package.

We affirm that we **can defend every line of code** in this repository during the oral defense. "The AI wrote it" is not an answer we will use.

---

## Signatures

By signing below, we affirm that:
- The contributions described above are accurate.
- The commit percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team member.
- AI assistant usage has been disclosed as described above.

| Member | Signature | Date |
|---|---|---|
| Farhad Budagov | __________________________ | __________ |
| Mikayil Hasanov | __________________________ | __________ |
| Nihad Gojayev | __________________________ | __________ |

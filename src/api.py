"""HTTP API for the Food Analyzer (Topic 2 - Food Analyzer).

Exposes POST /analyze, mirroring cli.py's `analyze` command as an HTTP
endpoint instead of a terminal command: same validation, same AI
pipeline, same storage layer - just a different front door
(TOPIC.md's "HTTP API" requirement).

Run with:
    uvicorn src.api:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, HTTPException, Request, UploadFile

import ai

from src.ai_service import AIServiceError, identify_ingredients
from src.config import settings
from src.models import AnalysisResponse, IngredientResult
from src.pipeline import lookup_all
from src.storage.repository import AnalysisRepository, StorageError

logger = logging.getLogger("foodanalyzer")

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Uploaded photos are written here instead of an auto-deleted temp file.
# Two reasons, both found in review:
#  1. ai/providers/*.py opens the image again by path (Path(image_path)
#     .read_bytes()). tempfile.NamedTemporaryFile(delete=True) keeps its
#     own handle open, and Windows refuses to open a file a second time
#     while another handle already has it open -> PermissionError
#     (WinError 32) on every single request on our (Windows) dev machines.
#  2. A temp file is deleted right after the request returns, so the
#     image_path saved by save_analysis() below would immediately dangle -
#     any later `history`/`get_by_id` would point at a file that no
#     longer exists. cli.py doesn't have this problem because it's handed
#     a real, permanent, user-supplied path.
UPLOAD_DIR = Path(settings.image_upload_dir)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one shared DB connection pool for the app's lifetime.

    Opening a fresh AnalysisRepository (and therefore a fresh asyncpg
    pool + schema-creation query) on every request is wasteful and, under
    concurrent load, risks exhausting Postgres's connection limit. One
    pool, created at startup and closed at shutdown, is reused by every
    request instead.
    """
    repo = AnalysisRepository(settings.database_url)
    await repo.connect()
    app.state.repo = repo
    try:
        yield
    finally:
        await repo.close()


app = FastAPI(title="Food Analyzer API", lifespan=lifespan)


def _validate_upload(filename: str, size_bytes: int) -> str:
    """Same checks as cli.py's _validate_image_path, adapted for an upload."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix!r}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    size_mb = size_bytes / (1024 * 1024)
    if size_mb > settings.max_image_size_mb:
        raise HTTPException(
            status_code=400,
            detail=f"Image is {size_mb:.1f} MB, exceeds the {settings.max_image_size_mb} MB limit",
        )

    return suffix


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: Request, file: UploadFile = File(...)) -> AnalysisResponse:
    # Reject an obviously oversized upload before reading it into memory,
    # when the client sends Content-Length. This isn't a hard guarantee
    # (chunked requests may omit it), so the size check in _validate_upload
    # below still runs after the read as a backstop.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_mb = int(content_length) / (1024 * 1024)
        except ValueError:
            declared_mb = None
        if declared_mb is not None and declared_mb > settings.max_image_size_mb:
            raise HTTPException(
                status_code=400,
                detail=f"Image is {declared_mb:.1f} MB, exceeds the {settings.max_image_size_mb} MB limit",
            )

    contents = await file.read()
    suffix = _validate_upload(file.filename or "", len(contents))

    image_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    image_path.write_bytes(contents)

    try:
        try:
            # identify_ingredients is a blocking call - run it off the
            # event loop so one slow request doesn't stall the server.
            ingredients = await asyncio.to_thread(identify_ingredients, str(image_path))
        except AIServiceError as e:
            raise HTTPException(status_code=502, detail=f"AI analysis failed: {e}") from e

        if not ingredients:
            return AnalysisResponse(meal_recognized=False, message="Meal not recognized in image.")

        # Nutrition lookups run in parallel - see src/pipeline.py.
        facts_by_name, failures = await lookup_all(ingredients)
        for ing, error in failures:
            logger.warning("skipping %s: %s", ing.name, error)

        if not facts_by_name:
            return AnalysisResponse(
                meal_recognized=True,
                message="Ingredients detected, but nutrition lookup failed for all of them.",
            )

        totals = ai.compute_totals(ingredients, facts_by_name)

        results: list[IngredientResult] = []
        for ing in ingredients:
            facts = facts_by_name.get(ing.name)
            if facts is None:
                continue
            portion = facts.for_grams(ing.estimated_grams)
            results.append(
                IngredientResult(
                    name=ing.name,
                    weight_g=ing.estimated_grams,
                    confidence=ing.confidence,
                    kcal=portion.kcal,
                    protein=portion.protein_g,
                    carbs=portion.carbs_g,
                    fat=portion.fat_g,
                )
            )

        repo: AnalysisRepository = request.app.state.repo
        try:
            await repo.save_analysis(str(image_path), ingredients, totals)
        except StorageError as e:
            # A DB outage shouldn't hide the analysis result itself - log
            # it and still return what we found (same policy as cli.py).
            logger.warning("could not save analysis: %s", e)

        return AnalysisResponse(
            meal_recognized=True,
            ingredients=results,
            total_weight_g=sum(r.weight_g for r in results),
            totals={
                "kcal": totals.kcal,
                "protein": totals.protein_g,
                "carbs": totals.carbs_g,
                "fat": totals.fat_g,
            },
        )
    except HTTPException:
        image_path.unlink(missing_ok=True)
        raise
    except Exception:
        # Anything unexpected (e.g. a provider raising FileNotFoundError)
        # shouldn't leak a raw stack trace to the client (brief §4.5:
        # "clear error message, not a stack trace"), and shouldn't leave
        # an orphaned upload behind either.
        image_path.unlink(missing_ok=True)
        logger.exception("unexpected error analyzing %s", image_path)
        raise HTTPException(status_code=500, detail="Internal error while analyzing the image")

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
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

import ai

from src.ai_service import AIServiceError, identify_ingredients
from src.config import settings
from src.models import AnalysisResponse, IngredientResult
from src.pipeline import lookup_all
from src.storage.repository import AnalysisRepository, StorageError

logger = logging.getLogger("foodanalyzer")

app = FastAPI(title="Food Analyzer API")

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _validate_upload(filename: str, size_bytes: int) -> str:
    """Same checks as cli.py's _validate_image_path, adapted for an upload."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix!r}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

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
async def analyze(file: UploadFile = File(...)) -> AnalysisResponse:
    contents = await file.read()
    suffix = _validate_upload(file.filename or "", len(contents))

    # ai.identify_ingredients needs a real file path, so the upload is
    # written to a temp file for the duration of the request.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(contents)
        tmp.flush()

        try:
            # identify_ingredients is a blocking call - run it off the
            # event loop so one slow request doesn't stall the server.
            ingredients = await asyncio.to_thread(identify_ingredients, tmp.name)
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

        results = [
            IngredientResult(
                name=ing.name,
                weight_g=ing.estimated_grams,
                confidence=ing.confidence,
                kcal=facts_by_name[ing.name].for_grams(ing.estimated_grams).kcal,
                protein=facts_by_name[ing.name].for_grams(ing.estimated_grams).protein_g,
                carbs=facts_by_name[ing.name].for_grams(ing.estimated_grams).carbs_g,
                fat=facts_by_name[ing.name].for_grams(ing.estimated_grams).fat_g,
            )
            for ing in ingredients
            if ing.name in facts_by_name
        ]

        try:
            async with AnalysisRepository(settings.database_url) as repo:
                await repo.save_analysis(tmp.name, ingredients, totals)
        except StorageError as e:
            # A DB outage shouldn't hide the analysis result itself -
            # log it and still return what we found (same policy as cli.py).
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
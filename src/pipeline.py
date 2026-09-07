"""
Runs nutrition lookups for all ingredients in one photo in parallel.

Rule: still no direct calls to USDA or any provider here — everything
goes through src.ai_service.NutritionService (which itself only calls
ai.*, per the project contract).
"""

from __future__ import annotations

import asyncio
import logging

from ai.schemas import Ingredient, NutritionFacts

from src.ai_service import AIServiceError, NutritionService

logger = logging.getLogger("foodanalyzer")

# USDA's free tier has limits — a burst of many simultaneous requests can
# still trigger throttling. Bound how many lookups run at once.
MAX_PARALLEL_LOOKUPS = 10


async def lookup_all(
    ingredients: list[Ingredient],
    nutrition_service: NutritionService | None = None,
) -> tuple[dict[str, NutritionFacts], list[tuple[Ingredient, str]]]:
    """Look up nutrition facts for every ingredient in parallel.

    Returns (facts_by_name, failures):
    - facts_by_name: ready to pass straight into ai.compute_totals
    - failures: list of (ingredient, error_message) for lookups that
      failed after retries — one bad ingredient never aborts the batch.
    """
    service = nutrition_service or NutritionService()
    semaphore = asyncio.Semaphore(MAX_PARALLEL_LOOKUPS)

    async def _lookup_one(ing: Ingredient):
        async with semaphore:
            try:
                # NutritionService.lookup is a synchronous (blocking) call,
                # so we run it in a worker thread — while one lookup is
                # waiting on I/O, others can proceed.
                facts = await asyncio.to_thread(service.lookup, ing.name)
                return ing, facts, None
            except AIServiceError as e:
                logger.warning("lookup failed: %s -> %s", ing.name, e)
                return ing, None, str(e)

    results = await asyncio.gather(*(_lookup_one(ing) for ing in ingredients))

    facts_by_name: dict[str, NutritionFacts] = {}
    failures: list[tuple[Ingredient, str]] = []
    for ing, facts, error in results:
        if facts is not None:
            facts_by_name[ing.name] = facts
        else:
            failures.append((ing, error or "unknown error"))

    return facts_by_name, failures
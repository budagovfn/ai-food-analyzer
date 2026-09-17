"""Adds retry, timeout, logging, and caching on top of ai.identify_ingredients
and ai.NutritionProvider.

Rule: no direct calls to the VLM or USDA here — everything goes through
ai.* (§4.3 of the brief: wrap the provided AI module, don't bypass it).

Design notes
------------
- Every call into ai.* is bounded by AI_CALL_TIMEOUT_SECONDS via a shared
  ThreadPoolExecutor (§4.3: "Add a timeout to every call"). ai.* is a
  synchronous SDK, so a thread pool is the simplest way to enforce a
  timeout on a call we can't otherwise interrupt.
- Retries cover both transient provider errors and timeouts — a slow
  response is as likely to be transient as a 5xx/429 would be.
- All failures surface as AIServiceError, mirroring StorageError in
  src/storage/repository.py — callers (CLI, HTTP API) only need to catch
  one exception type from this layer, never ai.providers internals.
- NutritionService caches lookups by ingredient name for
  settings.nutrition_cache_ttl_seconds (§5.2.2: "nutrition lookups for the
  same ingredient string within a configurable TTL (default 24h) must hit
  a local cache").
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Lock
from typing import Callable, TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import ai
from ai.providers.base import ProviderError
from ai.schemas import Ingredient, NutritionFacts
from src.config import settings

logger = logging.getLogger("foodanalyzer")
logger.setLevel(settings.log_level)

T = TypeVar("T")

# Every call into ai.* is bounded by this timeout (seconds). Not yet exposed
# via .env — worth promoting to a Settings field if it ever needs tuning
# per environment (e.g. a slower VLM provider in CI than in prod).
AI_CALL_TIMEOUT_SECONDS = 30

# Shared executor: bounds how many ai.* calls are in flight at once and
# gives us a place to enforce AI_CALL_TIMEOUT_SECONDS on an otherwise
# blocking, synchronous SDK call.
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-call")


class AIServiceError(Exception):
    """Raised when a call into the ai module fails or times out.

    Wraps ai.providers.base.ProviderError and timeout errors so callers
    only need to catch one exception type from this layer.
    """


# Same retry policy for both identify_ingredients and nutrition lookups:
# 3 attempts, exponential backoff (1s, 2s, 4s...). Retries on transient
# provider errors AND on our own enforced timeout.
RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ProviderError, FutureTimeoutError)),
    reraise=True,
)


def _call_with_timeout(func: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run func in the shared executor, bounded by AI_CALL_TIMEOUT_SECONDS."""
    future = _executor.submit(func, *args, **kwargs)
    return future.result(timeout=AI_CALL_TIMEOUT_SECONDS)


@RETRY
def _identify_ingredients_with_retry(image_path: str) -> list[Ingredient]:
    return _call_with_timeout(ai.identify_ingredients, image_path)


def identify_ingredients(image_path: str) -> list[Ingredient]:
    """Finds ingredients in the photo via the AI module.

    Retries transient provider errors and timeouts (3 attempts, exponential
    backoff). Returns [] if the meal isn't recognized — that's a valid AI
    response, not a failure. Raises AIServiceError if every retry is
    exhausted.
    """
    start = time.perf_counter()
    try:
        ingredients = _identify_ingredients_with_retry(image_path)
    except FutureTimeoutError as e:
        raise AIServiceError(
            f"identify_ingredients timed out after {AI_CALL_TIMEOUT_SECONDS}s "
            f"for {image_path!r}"
        ) from e
    except ProviderError as e:
        raise AIServiceError(f"identify_ingredients failed for {image_path!r}: {e}") from e

    elapsed = time.perf_counter() - start
    logger.info(
        "image=%s -> %d ingredient(s) found in %.2fs", image_path, len(ingredients), elapsed
    )
    return ingredients


class NutritionService:
    """Looks up nutrition facts for one ingredient (via USDA), with a local
    TTL cache keyed by ingredient name (case-insensitive, trimmed).
    """

    def __init__(self, provider: "ai.NutritionProvider | None" = None) -> None:
        self._provider = provider or ai.get_nutrition_provider()
        self._cache: dict[str, tuple[float, NutritionFacts]] = {}
        self._cache_lock = Lock()
        self._ttl_seconds = settings.nutrition_cache_ttl_seconds

    @RETRY
    def _lookup_with_retry(self, ingredient_name: str) -> NutritionFacts:
        return _call_with_timeout(self._provider.lookup, ingredient_name)

    def lookup(self, ingredient_name: str) -> NutritionFacts:
        """Return nutrition facts for one ingredient, using the cache when possible."""
        key = ingredient_name.strip().lower()

        cached = self._get_cached(key)
        if cached is not None:
            logger.info("%s -> cache hit", ingredient_name)
            return cached

        start = time.perf_counter()
        try:
            facts = self._lookup_with_retry(ingredient_name)
        except FutureTimeoutError as e:
            raise AIServiceError(
                f"nutrition lookup timed out after {AI_CALL_TIMEOUT_SECONDS}s "
                f"for {ingredient_name!r}"
            ) from e
        except ProviderError as e:
            raise AIServiceError(
                f"nutrition lookup failed for {ingredient_name!r}: {e}"
            ) from e

        elapsed = time.perf_counter() - start
        logger.info(
            "%s -> %.0f kcal/100g in %.2fs (cache miss)",
            ingredient_name,
            facts.kcal_per_100g,
            elapsed,
        )
        self._set_cached(key, facts)
        return facts

    def _get_cached(self, key: str) -> NutritionFacts | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, facts = entry
            if time.monotonic() >= expires_at:
                del self._cache[key]
                return None
            return facts

    def _set_cached(self, key: str, facts: NutritionFacts) -> None:
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + self._ttl_seconds, facts)
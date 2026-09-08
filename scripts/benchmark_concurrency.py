"""Sequential vs. concurrent nutrition-lookup benchmark.

The brief requires a wall-clock comparison (N requests sequential vs.
concurrent) for at least one realistic workload, on the same machine,
with caches cleared (§4.4). This script provides that comparison for the
nutrition-lookup stage of the pipeline (src/pipeline.lookup_all).

Usage
-----
    python scripts/benchmark_concurrency.py

Requires a real USDA_API_KEY in .env (this makes real network calls —
that's the point, we're timing real I/O-bound work).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.schemas import Ingredient
from src.ai_service import AIServiceError, NutritionService
from src.pipeline import lookup_all

# A realistic workload: distinct ingredient names from the sample data set
# (data/*.png filenames), so a fresh NutritionService has no cache hits to
# skew the comparison.
SAMPLE_INGREDIENTS = [
    Ingredient(name=n, estimated_grams=100.0, confidence=0.9)
    for n in [
        "white rice, cooked",
        "grilled chicken breast",
        "broccoli, raw",
        "salmon, baked",
        "baked potato",
        "hard-boiled egg",
        "mixed green salad",
        "pasta, cooked",
        "tomato, raw",
        "cheddar cheese",
    ]
]


def run_sequential(ingredients: list[Ingredient]) -> float:
    """Time N nutrition lookups run one at a time, no concurrency."""
    service = NutritionService()  # fresh instance -> empty cache
    start = time.perf_counter()
    for ing in ingredients:
        try:
            service.lookup(ing.name)
        except AIServiceError:
            pass  # a lookup failure doesn't invalidate the timing comparison
    return time.perf_counter() - start


def run_concurrent(ingredients: list[Ingredient]) -> float:
    """Time the same N lookups run through pipeline.lookup_all."""
    service = NutritionService()  # fresh instance -> empty cache
    start = time.perf_counter()
    asyncio.run(lookup_all(ingredients, nutrition_service=service))
    return time.perf_counter() - start


def main() -> None:
    n = len(SAMPLE_INGREDIENTS)
    print(f"Benchmarking {n} nutrition lookups (fresh cache each run)...\n")

    seq_time = run_sequential(SAMPLE_INGREDIENTS)
    print(f"Sequential : {seq_time:.2f}s  ({seq_time / n:.2f}s per lookup)")

    con_time = run_concurrent(SAMPLE_INGREDIENTS)
    print(f"Concurrent : {con_time:.2f}s  ({con_time / n:.2f}s per lookup)")

    speedup = seq_time / con_time if con_time > 0 else float("inf")
    print(f"\nSpeedup: {speedup:.2f}x")


if __name__ == "__main__":
    main()

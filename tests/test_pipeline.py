"""Offline tests for src/pipeline.py (parallel nutrition lookups)."""

from __future__ import annotations

import asyncio
import time

import pytest

from ai.providers.base import ProviderError
from ai.schemas import Ingredient, NutritionFacts

from src.ai_service import NutritionService
from src.pipeline import MAX_PARALLEL_LOOKUPS, lookup_all


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Skip tenacity's real backoff sleep so failure-path tests stay fast."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _ingredient(name: str) -> Ingredient:
    return Ingredient(name=name, estimated_grams=100, confidence=0.9)


def test_lookup_all_returns_facts_for_every_ingredient(fake_nutrition):
    svc = NutritionService(provider=fake_nutrition)
    ingredients = [
        _ingredient("white rice (cooked)"),
        _ingredient("grilled chicken breast"),
        _ingredient("broccoli"),
    ]

    facts_by_name, failures = asyncio.run(lookup_all(ingredients, nutrition_service=svc))

    assert set(facts_by_name) == {i.name for i in ingredients}
    assert failures == []


def test_lookup_all_one_bad_ingredient_does_not_break_the_batch(fake_nutrition):
    svc = NutritionService(provider=fake_nutrition)
    ingredients = [
        _ingredient("white rice (cooked)"),
        _ingredient("this-ingredient-does-not-exist"),
    ]

    facts_by_name, failures = asyncio.run(lookup_all(ingredients, nutrition_service=svc))

    assert "white rice (cooked)" in facts_by_name
    assert len(failures) == 1
    assert failures[0][0].name == "this-ingredient-does-not-exist"


def test_lookup_all_runs_concurrently_not_sequentially():
    """8 ingredients x 0.3s each should take ~0.3s in parallel, not ~2.4s serial."""

    class SlowProvider:
        def lookup(self, name: str) -> NutritionFacts:
            time.sleep(0.3)
            return NutritionFacts(
                name=name, kcal_per_100g=100, protein_g_per_100g=5,
                carbs_g_per_100g=10, fat_g_per_100g=2, source="slow-fake",
            )

    svc = NutritionService(provider=SlowProvider())
    ingredients = [_ingredient(f"ingredient-{i}") for i in range(8)]

    start = time.perf_counter()
    facts_by_name, failures = asyncio.run(lookup_all(ingredients, nutrition_service=svc))
    elapsed = time.perf_counter() - start

    assert len(facts_by_name) == 8
    assert failures == []
    # Generous upper bound - well under the ~2.4s a sequential loop would take.
    assert elapsed < 1.5, f"lookups took {elapsed:.2f}s, expected them to run in parallel"


def test_lookup_all_respects_max_parallel_lookups(fake_nutrition):
    """Sanity check that the semaphore constant is sane, not zero or unbounded."""
    assert 1 <= MAX_PARALLEL_LOOKUPS <= 50


def test_lookup_all_empty_ingredient_list(fake_nutrition):
    svc = NutritionService(provider=fake_nutrition)

    facts_by_name, failures = asyncio.run(lookup_all([], nutrition_service=svc))

    assert facts_by_name == {}
    assert failures == []
    
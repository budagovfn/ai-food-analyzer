"""Offline tests for src/ai_service.py (retry, timeout, caching, error wrapping).

Everything here uses the Fake providers from tests/conftest.py - no network,
no real API keys needed.
"""

from __future__ import annotations

import time

import pytest

from ai.providers.base import ProviderError
from ai.schemas import Ingredient, NutritionFacts

from src.ai_service import AIServiceError, NutritionService, identify_ingredients


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Retries use real exponential backoff (1s, 2s, 4s...) - skip the
    actual waiting so this test file runs fast."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


# --- identify_ingredients ---------------------------------------------------

def test_identify_ingredients_returns_ai_result(monkeypatch):
    expected = [Ingredient(name="white rice (cooked)", estimated_grams=200, confidence=0.9)]
    monkeypatch.setattr("ai.identify_ingredients", lambda path: expected)

    result = identify_ingredients("data/rice_chicken.png")

    assert result == expected


def test_identify_ingredients_empty_list_is_not_an_error(monkeypatch):
    """An empty list means 'no meal recognized' - normal control flow, not AIServiceError."""
    monkeypatch.setattr("ai.identify_ingredients", lambda path: [])

    result = identify_ingredients("data/no_meal_blue.png")

    assert result == []


def test_identify_ingredients_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("temporary VLM hiccup")
        return [Ingredient(name="broccoli", estimated_grams=80, confidence=0.8)]

    monkeypatch.setattr("ai.identify_ingredients", flaky)

    result = identify_ingredients("data/rice_chicken.png")

    assert calls["n"] == 3
    assert result[0].name == "broccoli"


def test_identify_ingredients_raises_aiservice_error_after_all_retries_fail(monkeypatch):
    monkeypatch.setattr(
        "ai.identify_ingredients",
        lambda path: (_ for _ in ()).throw(ProviderError("VLM is down")),
    )

    with pytest.raises(AIServiceError):
        identify_ingredients("data/rice_chicken.png")


# --- NutritionService --------------------------------------------------------

def test_nutrition_lookup_success(fake_nutrition):
    svc = NutritionService(provider=fake_nutrition)

    facts = svc.lookup("white rice (cooked)")

    assert isinstance(facts, NutritionFacts)
    assert facts.kcal_per_100g == 130


def test_nutrition_lookup_unknown_ingredient_raises_aiservice_error(fake_nutrition):
    svc = NutritionService(provider=fake_nutrition)

    with pytest.raises(AIServiceError):
        svc.lookup("does-not-exist-in-db")


def test_nutrition_lookup_uses_cache(fake_nutrition):
    calls = {"n": 0}
    original_lookup = fake_nutrition.lookup

    def counted(name):
        calls["n"] += 1
        return original_lookup(name)

    fake_nutrition.lookup = counted
    svc = NutritionService(provider=fake_nutrition)

    svc.lookup("white rice (cooked)")
    svc.lookup("white rice (cooked)")
    svc.lookup("WHITE RICE (COOKED)")  # same ingredient, different case

    assert calls["n"] == 1, "second and third lookups should be served from cache"


def test_nutrition_cache_expires_after_ttl(fake_nutrition, monkeypatch):
    from src import config as config_module

    monkeypatch.setattr(config_module.settings, "nutrition_cache_ttl_seconds", 0)

    calls = {"n": 0}
    original_lookup = fake_nutrition.lookup

    def counted(name):
        calls["n"] += 1
        return original_lookup(name)

    fake_nutrition.lookup = counted
    svc = NutritionService(provider=fake_nutrition)

    svc.lookup("white rice (cooked)")
    svc.lookup("white rice (cooked)")  # TTL is 0 -> should miss cache, call again

    assert calls["n"] == 2
    
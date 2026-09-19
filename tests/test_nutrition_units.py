"""Regression tests for the USDA energy-unit defect.

The numbers in `TestLiveIncident` are the real values returned by a live
`POST /analyze` run on 2026-09-19 against `data/rice_chicken.png`, which is
how the defect was found: the endpoint reported 1975.2 kcal for a 240 g
plate. See `src/nutrition.py` for the root-cause analysis.

Everything here runs offline — the provider is a stub, no HTTP is issued.
"""

from __future__ import annotations

import os

import pytest

# src/config.py builds Settings() at import time and these have no defaults.
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("USDA_API_KEY", "test-usda-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")

import ai  # noqa: E402
from ai.nutrition import NutritionProvider  # noqa: E402
from ai.schemas import Ingredient, NutritionFacts  # noqa: E402

from src.nutrition import (  # noqa: E402
    KJ_PER_KCAL,
    UnitCheckedNutritionProvider,
    atwater_kcal,
    correct_energy_units,
    get_nutrition_provider,
)


def facts(name="x", kcal=100.0, protein=0.0, carbs=0.0, fat=0.0) -> NutritionFacts:
    return NutritionFacts(
        name=name,
        kcal_per_100g=kcal,
        protein_g_per_100g=protein,
        carbs_g_per_100g=carbs,
        fat_g_per_100g=fat,
    )


class StubProvider(NutritionProvider):
    """Returns canned facts; records what it was asked for."""

    def __init__(self, to_return: NutritionFacts) -> None:
        self.to_return = to_return
        self.calls: list[str] = []

    def lookup(self, ingredient_name: str) -> NutritionFacts:
        self.calls.append(ingredient_name)
        return self.to_return


class TestAtwater:
    def test_sums_macros_with_atwater_factors(self):
        # 10 g protein + 20 g carbs + 5 g fat = 40 + 80 + 45
        assert atwater_kcal(facts(protein=10, carbs=20, fat=5)) == pytest.approx(165.0)

    def test_zero_macros_give_zero(self):
        assert atwater_kcal(facts(kcal=999.0)) == 0.0


class TestCorrectEnergyUnits:
    def test_kilojoule_value_is_converted(self):
        # 1240 kJ/100 g against macros implying ~288 kcal -> ratio 4.3.
        out = correct_energy_units(facts(kcal=1240.0, protein=23.0, carbs=0.0, fat=21.8))
        assert out.kcal_per_100g == pytest.approx(1240.0 / KJ_PER_KCAL)
        assert out.kcal_per_100g == pytest.approx(296.4, abs=0.5)

    def test_correct_kcal_value_is_left_alone(self):
        original = facts(kcal=296.0, protein=23.0, carbs=0.0, fat=21.8)
        assert correct_energy_units(original).kcal_per_100g == 296.0

    def test_macros_are_never_touched(self):
        out = correct_energy_units(facts(kcal=1240.0, protein=23.0, carbs=0.0, fat=21.8))
        assert (out.protein_g_per_100g, out.carbs_g_per_100g, out.fat_g_per_100g) == (
            23.0,
            0.0,
            21.8,
        )

    def test_record_without_macros_is_passed_through(self):
        # Nothing to audit against — e.g. USDA did not populate the macros.
        original = facts(kcal=1240.0)
        assert correct_energy_units(original) is original

    def test_zero_energy_is_passed_through(self):
        original = facts(kcal=0.0, protein=23.0, fat=21.8)
        assert correct_energy_units(original) is original

    @pytest.mark.parametrize("ratio", [1.0, 1.25, 0.8, 2.0, 8.0])
    def test_ratios_outside_the_kilojoule_band_are_left_alone(self, ratio):
        # Ordinary Atwater error (fibre, polyols) must never trigger a
        # conversion; only the narrow band around 4.184 does.
        estimate = 23.0 * 4 + 21.8 * 9
        original = facts(kcal=estimate * ratio, protein=23.0, fat=21.8)
        assert correct_energy_units(original).kcal_per_100g == pytest.approx(
            estimate * ratio
        )


class TestUnitCheckedProvider:
    def test_delegates_to_inner_provider(self):
        inner = StubProvider(facts(kcal=296.0, protein=23.0, fat=21.8))
        UnitCheckedNutritionProvider(inner).lookup("beef patty")
        assert inner.calls == ["beef patty"]

    def test_corrects_what_the_inner_provider_returns(self):
        inner = StubProvider(facts(kcal=1240.0, protein=23.0, fat=21.8))
        out = UnitCheckedNutritionProvider(inner).lookup("beef patty")
        assert out.kcal_per_100g == pytest.approx(296.4, abs=0.5)

    def test_factory_wraps_the_ai_provider(self, monkeypatch):
        inner = StubProvider(facts())
        monkeypatch.setattr(ai, "get_nutrition_provider", lambda: inner)
        provider = get_nutrition_provider()
        assert isinstance(provider, UnitCheckedNutritionProvider)
        assert provider.inner is inner


class TestLiveIncident:
    """The exact payload that exposed the defect, end to end through compute_totals."""

    # Per-100 g figures USDA returned, with energy still in kJ.
    RICE = facts(name="cooked white rice", kcal=406.0, protein=2.02, carbs=21.1, fat=0.19)
    BEEF = facts(name="grilled beef patty", kcal=1240.0, protein=23.0, carbs=0.0, fat=21.8)

    def test_uncorrected_totals_reproduce_the_bad_number(self):
        ingredients = [
            Ingredient(name=self.RICE.name, estimated_grams=120.0, confidence=0.7),
            Ingredient(name=self.BEEF.name, estimated_grams=120.0, confidence=0.7),
        ]
        totals = ai.compute_totals(
            ingredients, {self.RICE.name: self.RICE, self.BEEF.name: self.BEEF}
        )
        # This is what the API actually returned before the fix.
        assert totals.kcal == pytest.approx(1975.2, abs=1.0)

    def test_corrected_totals_are_physically_plausible(self):
        ingredients = [
            Ingredient(name=self.RICE.name, estimated_grams=120.0, confidence=0.7),
            Ingredient(name=self.BEEF.name, estimated_grams=120.0, confidence=0.7),
        ]
        corrected = {
            self.RICE.name: correct_energy_units(self.RICE),
            self.BEEF.name: correct_energy_units(self.BEEF),
        }
        totals = ai.compute_totals(ingredients, corrected)

        assert totals.kcal == pytest.approx(472.0, abs=5.0)
        # And the totals now agree with their own macros, which is the
        # invariant that was violated before.
        implied = totals.protein_g * 4 + totals.carbs_g * 4 + totals.fat_g * 9
        assert totals.kcal == pytest.approx(implied, rel=0.10)

    def test_no_beef_patty_exceeds_pure_fat(self):
        """A sanity bound: nothing edible has more than 900 kcal/100 g."""
        assert correct_energy_units(self.BEEF).kcal_per_100g < 900.0

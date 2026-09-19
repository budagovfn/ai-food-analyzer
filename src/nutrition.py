"""Defensive wrapper around the provided USDA nutrition adapter.

Why this module exists
----------------------
`ai/nutrition.py` matches USDA nutrients by name only::

    name = fn.get("nutrientName")
    if name in _USDA_NUTRIENT_NAMES:
        out[name] = float(fn.get("value", 0.0))

USDA's ``/foods/search`` endpoint returns the ``Energy`` nutrient **twice**
for most foods — once with ``unitName: "KCAL"`` and once with
``unitName: "KJ"``. Because the loop ignores ``unitName`` and overwrites on
each match, whichever record comes last wins, and in practice that is the
kilojoule one. The macronutrients are unaffected; only energy is wrong, and
it is wrong by exactly the kJ->kcal factor of 4.184.

We cannot fix this at the source: §4.8 of the brief forbids editing the
provided `ai/` package, and Appendix C says to file an issue with the
instructor instead (done — see the report). What we *can* do is what §4.3
asks of the SE layer: "Validate the structure of the model's response
before passing it downstream ... you must still defend against
semantically wrong outputs."

How the check works
-------------------
Energy and macronutrients are not independent: the Atwater factors give
4 kcal/g for protein and carbohydrate and 9 kcal/g for fat. That makes the
returned energy value auditable against the macros that came with it. If
the reported figure sits at roughly 4.2x the Atwater estimate, it is
kilojoules mislabelled as kilocalories and we convert it. Anything close
to the estimate is left untouched.

The test is deliberately conservative. Atwater is an approximation (it
ignores fibre, polyols and alcohol), so we only act inside a band that a
mere estimation error cannot reach, and we never touch a record whose
macros are all zero because there is then nothing to check against.
"""

from __future__ import annotations

import logging

import ai
from ai.nutrition import NutritionProvider
from ai.schemas import NutritionFacts

logger = logging.getLogger("foodanalyzer")

# 1 kcal = 4.184 kJ (thermochemical calorie, the convention USDA uses).
KJ_PER_KCAL = 4.184

# Atwater factors, kcal per gram.
KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_CARBS = 4.0
KCAL_PER_G_FAT = 9.0

# A reported/estimated ratio inside this band means the value is in kJ.
# The band is centred on 4.184 and stops well short of 1.0, so ordinary
# Atwater error (rarely worse than +-25%) can never trip it.
_KJ_RATIO_LOW = 3.0
_KJ_RATIO_HIGH = 5.5

# Below this, the macros are too small to audit anything against.
_MIN_AUDITABLE_KCAL = 5.0


def atwater_kcal(facts: NutritionFacts) -> float:
    """Energy per 100 g implied by the macronutrients, via Atwater factors."""
    return (
        facts.protein_g_per_100g * KCAL_PER_G_PROTEIN
        + facts.carbs_g_per_100g * KCAL_PER_G_CARBS
        + facts.fat_g_per_100g * KCAL_PER_G_FAT
    )


def correct_energy_units(facts: NutritionFacts) -> NutritionFacts:
    """Return `facts` with the energy value coerced to kcal per 100 g.

    Leaves the record untouched unless the reported energy is roughly
    `KJ_PER_KCAL` times the Atwater estimate, which is the signature of a
    kilojoule value reported in a kilocalorie field.
    """
    estimate = atwater_kcal(facts)
    if estimate < _MIN_AUDITABLE_KCAL:
        # Nothing to cross-check against (e.g. water, black coffee, or a
        # record whose macros USDA did not populate). Pass it through.
        return facts

    reported = facts.kcal_per_100g
    if reported <= 0:
        return facts

    ratio = reported / estimate
    if not (_KJ_RATIO_LOW <= ratio <= _KJ_RATIO_HIGH):
        return facts

    corrected = reported / KJ_PER_KCAL
    logger.warning(
        "energy for %r looked like kilojoules (%.1f reported vs %.1f implied by "
        "macros, ratio %.2f); converted to %.1f kcal/100g",
        facts.name,
        reported,
        estimate,
        ratio,
        corrected,
    )
    # NutritionFacts is frozen, so build a corrected copy rather than mutate.
    return facts.model_copy(update={"kcal_per_100g": corrected})


class UnitCheckedNutritionProvider(NutritionProvider):
    """Delegates lookups to another provider, then audits the energy unit.

    Composition rather than inheritance on purpose: the wrapped provider
    owns the HTTP call and the USDA payload shape, and this class owns one
    orthogonal concern — validating what comes back. That keeps the
    provided `ai/` adapter a clean, replaceable boundary; swapping in a
    different `NutritionProvider` needs no change here.
    """

    def __init__(self, inner: NutritionProvider | None = None) -> None:
        self._inner = inner if inner is not None else ai.get_nutrition_provider()

    @property
    def inner(self) -> NutritionProvider:
        """The wrapped provider, exposed for tests and diagnostics."""
        return self._inner

    def lookup(self, ingredient_name: str) -> NutritionFacts:
        return correct_energy_units(self._inner.lookup(ingredient_name))


def get_nutrition_provider() -> NutritionProvider:
    """Factory mirroring `ai.get_nutrition_provider`, with the unit check applied.

    Callers in the SE layer use this instead of the `ai` factory directly so
    that every nutrition lookup in the application is audited, whichever
    concrete provider `NUTRITION_PROVIDER` selects.
    """
    return UnitCheckedNutritionProvider(ai.get_nutrition_provider())

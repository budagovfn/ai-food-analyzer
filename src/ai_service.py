"""
ai.identify_ingredients ve ai.NutritionProvider ustune retry + logging elave edir.
Qayda: burda VLM ve ya USDA-ya birbasa muraciet YOXDUR, hamisi ai.* uzerinden gedir.
"""

from __future__ import annotations

import logging

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import ai
from ai.providers.base import ProviderError
from ai.schemas import Ingredient, NutritionFacts

from src.config import settings

logger = logging.getLogger("foodanalyzer")
logger.setLevel(settings.log_level)

# Her ikisi ucun eyni retry qaydasi: 3 cehd, cehdler arasi geden vaxt artir (1s, 2s, 4s...)
RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(ProviderError),
    reraise=True,
)


@RETRY
def identify_ingredients(image_path: str) -> list[Ingredient]:
    """Sekildeki inqredientleri tapir. Yemek taninmasa bos list [] qaytarir (xeta deyil)."""
    ingredients = ai.identify_ingredients(image_path)
    logger.info("image=%s -> %d ingredient tapildi", image_path, len(ingredients))
    return ingredients


class NutritionService:
    """Bir inqredientin qidalilik melumatini tapir (USDA ile)."""

    def __init__(self, provider: ai.NutritionProvider | None = None) -> None:
        self._provider = provider or ai.get_nutrition_provider()

    @RETRY
    def lookup(self, ingredient_name: str) -> NutritionFacts:
        facts = self._provider.lookup(ingredient_name)
        logger.info("%s -> %.0f kcal/100g", ingredient_name, facts.kcal_per_100g)
        return facts
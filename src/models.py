"""
API cavabi ucun pydantic modelleri.

Qeyd: DB-de saxlanan record (MealAnalysisRecord) artiq src/storage/repository.py-da
tanimlanib - o, ai.schemas.Ingredient/Nutrition-i birbasa isledir. Bu faylda onu
tekrar yazmiriq ki, iki ferqli "history record" strukturu olmasin.
Bu fayl yalniz API-nin (POST /analyze) qaytardigi cavabin formasini teyin edir.
"""

from __future__ import annotations

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class IngredientResult(BaseModel):
    """Bir inqredientin API cavabindaki gorunusu (adi + qidalilik melumati)."""

    name: str = Field(description="Ingredient name")
    weight_g: float = Field(description="Estimated weight in grams")
    confidence: float = Field(description="AI detection confidence (0.0-1.0)")
    kcal: float = Field(description="Energy in kilocalories")
    protein: float = Field(description="Protein content in grams")
    carbs: float = Field(description="Carbohydrate content in grams")
    fat: float = Field(description="Fat content in grams")


class AnalysisResponse(BaseModel):
    """POST /analyze endpoint-inin qaytardigi cavab."""

    meal_recognized: bool = Field(description="Whether a valid meal was recognized in the image")
    message: Optional[str] = Field(default=None, description="Optional status or error message")
    ingredients: List[IngredientResult] = Field(default_factory=list, description="Recognized ingredients")
    total_weight_g: float = Field(default=0.0, description="Total meal weight in grams")
    totals: Dict[str, float] = Field(
        default_factory=lambda: {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0},
        description="Nutritional totals summary",
    )

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

# --- API Response Models ---

class IngredientResult(BaseModel):
    name: str = Field(description="Name of the ingredient")
    weight_g: float = Field(description="Estimated weight in grams")
    confidence: float = Field(description="AI detection confidence score (0.0 - 1.0)")
    kcal: float = Field(description="Energy in kilocalories")
    protein: float = Field(description="Protein content in grams")
    carbs: float = Field(description="Carbohydrate content in grams")
    fat: float = Field(description="Fat content in grams")


class AnalysisResponse(BaseModel):
    meal_recognized: bool = Field(description="Indicates whether a valid meal was recognized in the image")
    message: Optional[str] = Field(default=None, description="Optional status or error message")
    ingredients: List[IngredientResult] = Field(default_factory=list, description="List of recognized ingredients")
    total_weight_g: float = Field(default=0.0, description="Total meal weight in grams")
    totals: Dict[str, float] = Field(
        default_factory=lambda: {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0},
        description="Nutritional totals summary"
    )


# --- Database Persistence Models ---

class AnalysisRecordDB(BaseModel):
    id: Optional[int] = Field(default=None, description="Database primary key")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the analysis (UTC)"
    )
    image_path: str = Field(description="Path to the uploaded meal image")
    meal_recognized: bool = Field(description="Indicates if the meal was recognized")
    totals_json: Dict[str, Any] = Field(description="JSON serialized summary of the complete analysis result")
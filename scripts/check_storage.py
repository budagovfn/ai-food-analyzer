"""Manual smoke check for AnalysisRepository against a REAL Postgres.

Not a pytest test — pytest tests must be offline/mocked per the brief.
Run this once by hand whenever you change the storage layer:

    python scripts/check_storage.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Running this file directly (``python scripts/check_storage.py``) only puts
# scripts/ on sys.path, not the project root — so ``ai`` and ``src`` aren't
# importable. Add the project root explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.schemas import Ingredient, Nutrition
from src.config import settings
from src.storage import AnalysisRepository


async def main() -> None:
    async with AnalysisRepository(settings.database_url) as repo:
        ingredients = [
            Ingredient(name="chicken breast", estimated_grams=150, confidence=0.92),
            Ingredient(name="white rice", estimated_grams=200, confidence=0.88),
        ]
        totals = Nutrition(kcal=430.0, protein_g=45.0, carbs_g=55.0, fat_g=6.0)

        new_id = await repo.save_analysis("data/sample_meal.jpg", ingredients, totals)
        print(f"saved analysis id={new_id}")

        record = await repo.get_by_id(new_id)
        assert record is not None, "just-saved record should be retrievable"
        print("get_by_id ->", record)

        history = await repo.get_history(limit=5)
        print(f"get_history -> {len(history)} record(s)")
        for row in history:
            print(" ", row.id, row.created_at, row.image_path)

        missing = await repo.get_by_id(-1)
        assert missing is None, "nonexistent id should return None, not raise"
        print("get_by_id(-1) -> None (as expected)")


if __name__ == "__main__":
    asyncio.run(main())
"""Command-line interface for the Food Analyzer.

Usage
-----
    python -m foodanalyzer analyze data/sample_meal.jpg
    python -m foodanalyzer analyze data/sample_meal.jpg --no-save
    python -m foodanalyzer history --limit 5

This ties together every SE-layer piece written so far into one runnable
command, as required by the Day 3-5 "Skeleton" milestone: config -> AI
service wrapper -> storage, working end-to-end against the real ai/
package (or fails with a clear message, never a raw stack trace, per the
brief's robustness requirements in §4.5).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import ai
from ai.schemas import Ingredient, Nutrition

from src.ai_service import AIServiceError, identify_ingredients
from src.config import settings
from src.pipeline import lookup_all
from src.storage.repository import AnalysisRepository, StorageError

logger = logging.getLogger("foodanalyzer")

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


class InputValidationError(Exception):
    """Raised when user-supplied input fails validation.

    Kept separate from AIServiceError/StorageError so the CLI can print a
    short, specific message instead of a stack trace for bad input
    (§4.5: "Reject malformed payloads with a clear error message, not a
    stack trace").
    """


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _validate_image_path(raw_path: str) -> Path:
    """Validate a user-supplied image path before it reaches the AI layer.

    Checks existence, extension, and size — the three cheap checks that
    catch most bad input before spending an API call on it.
    """
    path = Path(raw_path)
    if not path.is_file():
        raise InputValidationError(f"Image not found: {raw_path}")

    if path.suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise InputValidationError(
            f"Unsupported file type {path.suffix!r}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > settings.max_image_size_mb:
        raise InputValidationError(
            f"Image is {size_mb:.1f} MB, exceeds the {settings.max_image_size_mb} MB limit"
        )

    return path


def _render_table(rows: list[tuple[str, float, Nutrition]], totals: Nutrition) -> str:
    """Format ingredient rows + totals as an aligned text table."""
    headers = ("ingredient", "g", "kcal", "protein", "carbs", "fat")
    body = [
        (
            name,
            f"{grams:.0f}",
            f"{n.kcal:.0f}",
            f"{n.protein_g:.1f}",
            f"{n.carbs_g:.1f}",
            f"{n.fat_g:.1f}",
        )
        for name, grams, n in rows
    ]
    body.append(
        (
            "TOTAL",
            f"{sum(g for _, g, _ in rows):.0f}",
            f"{totals.kcal:.0f}",
            f"{totals.protein_g:.1f}",
            f"{totals.carbs_g:.1f}",
            f"{totals.fat_g:.1f}",
        )
    )
    widths = [max(len(headers[i]), max(len(r[i]) for r in body)) for i in range(len(headers))]

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(row))

    rule = "-" * (sum(widths) + 2 * (len(widths) - 1))
    return "\n".join([fmt(headers), rule, *[fmt(r) for r in body[:-1]], rule, fmt(body[-1])])


async def _save_to_db(
    image_path: str, ingredients: list[Ingredient], totals: Nutrition
) -> int:
    async with AnalysisRepository(settings.database_url) as repo:
        return await repo.save_analysis(image_path, ingredients, totals)


async def _fetch_history(limit: int) -> list:
    async with AnalysisRepository(settings.database_url) as repo:
        return await repo.get_history(limit=limit)


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run one meal photo through the full pipeline: validate -> AI -> save."""
    try:
        image_path = _validate_image_path(args.image)
    except InputValidationError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        return 2

    try:
        ingredients = identify_ingredients(str(image_path))
    except AIServiceError as e:
        print(f"AI analysis failed: {e}", file=sys.stderr)
        return 1

    if not ingredients:
        print("Meal not recognized in image.")
        return 0

    # Nutrition lookups run in parallel (bounded by a semaphore inside
    # lookup_all), instead of one at a time — see src/pipeline.py.
    facts_by_name, failures = asyncio.run(lookup_all(ingredients))
    for ing, error in failures:
        # Graceful degradation (§4.5): one bad ingredient lookup
        # shouldn't kill the whole analysis.
        print(f"  ! Skipping {ing.name!r}: {error}", file=sys.stderr)

    rows: list[tuple[str, float, Nutrition]] = [
        (ing.name, ing.estimated_grams, facts_by_name[ing.name].for_grams(ing.estimated_grams))
        for ing in ingredients
        if ing.name in facts_by_name
    ]

    if not rows:
        print("No ingredients could be priced (all nutrition lookups failed).")
        return 1

    totals = ai.compute_totals(ingredients, facts_by_name)
    print(_render_table(rows, totals))

    if not args.no_save:
        try:
            analysis_id = asyncio.run(_save_to_db(str(image_path), ingredients, totals))
            print(f"\nSaved as analysis #{analysis_id}")
        except StorageError as e:
            # A DB outage shouldn't hide the analysis the user already got —
            # print the result above, then report the save failure clearly.
            print(f"\nWarning: could not save to database: {e}", file=sys.stderr)
            return 1

    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Print the most recent saved analyses."""
    try:
        records = asyncio.run(_fetch_history(args.limit))
    except StorageError as e:
        print(f"Could not fetch history: {e}", file=sys.stderr)
        return 1

    if not records:
        print("No analyses saved yet.")
        return 0

    for r in records:
        print(f"#{r.id}  {r.created_at:%Y-%m-%d %H:%M}  {r.image_path}  "
              f"{r.totals.kcal:.0f} kcal  ({len(r.ingredients)} ingredients)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foodanalyzer", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Analyze one meal photo")
    p_analyze.add_argument("image", help="Path to a meal image (.png/.jpg/.jpeg)")
    p_analyze.add_argument(
        "--no-save", action="store_true", help="Skip writing the result to the database"
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_history = sub.add_parser("history", help="List recent saved analyses")
    p_history.add_argument("--limit", type=int, default=10, help="Max rows to show (default: 10)")
    p_history.set_defaults(func=cmd_history)

    return parser


def main() -> None:
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

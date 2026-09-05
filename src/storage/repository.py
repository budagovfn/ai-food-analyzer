"""Persistent storage for meal analysis history (Topic 2 — Food Analyzer).

Stores every analyzed meal (timestamp, image path, identified ingredients,
computed nutrition totals) in PostgreSQL, per the brief's requirement
(§5.2.2): "History log: every analyzed meal is stored in PostgreSQL or
AsyncPG (timestamp, image path, ingredients, totals)."

Design notes
------------
- Uses `asyncpg` directly (not SQLAlchemy) — simplest option that satisfies
  the brief's storage requirement, and keeps the SE layer's dependency
  surface small.
- `DATABASE_URL` in .env follows the SQLAlchemy convention
  (postgresql+asyncpg://...). `asyncpg.connect`/`create_pool` don't
  understand the "+asyncpg" driver suffix, so `_normalize_dsn` strips it.
- Pydantic models (`Ingredient`, `Nutrition` from ai.schemas, plus our own
  `MealAnalysisRecord`) cross every module boundary here — no naked dicts,
  per the brief's OOP requirement (§4.2).
- All I/O is wrapped so a database failure surfaces as `StorageError`
  rather than a raw asyncpg exception leaking into callers.
- asyncpg does NOT decode jsonb columns to Python objects by default — it
  hands back the raw JSON text. `_init_connection` registers a type codec
  on every pooled connection so jsonb round-trips as list/dict on both the
  write side (INSERT ... VALUES ($2, $3)) and the read side (SELECT).
  See: https://magicstack.github.io/asyncpg/current/usage.html#example-automatic-json-conversion
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ai.schemas import Ingredient, Nutrition

try:
    import asyncpg
except ImportError as e:  # pragma: no cover - import-time guard
    raise ImportError(
        "The `asyncpg` package is required for storage. "
        "Install with `pip install asyncpg`."
    ) from e


class StorageError(Exception):
    """Raised when a storage operation fails.

    Wraps underlying asyncpg errors so callers never need to catch
    asyncpg-specific exception types directly.
    """


class MealAnalysisRecord(BaseModel):
    """One stored analysis, as read back from the database."""

    model_config = ConfigDict(extra="forbid")

    id: int
    created_at: datetime
    image_path: str
    ingredients: list[Ingredient]
    totals: Nutrition


def _normalize_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy-style DSN to one asyncpg understands.

    ``postgresql+asyncpg://...`` (as written in .env.example) becomes
    ``postgresql://...``. A DSN that is already in asyncpg's expected form
    is returned unchanged.
    """
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class AnalysisRepository:
    """Repository for meal analysis history.

    Usage
    -----
    ```python
    async with AnalysisRepository(settings.database_url) as repo:
        analysis_id = await repo.save_analysis(image_path, ingredients, totals)
        history = await repo.get_history(limit=10)
    ```
    """

    def __init__(self, database_url: str) -> None:
        self._dsn = _normalize_dsn(database_url)
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open the connection pool and ensure the schema exists."""
        try:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=5,
                init=self._init_connection,
            )
        except Exception as e:
            raise StorageError(f"Could not connect to database: {e}") from e
        await self._init_schema()

    @staticmethod
    async def _init_connection(conn: "asyncpg.Connection") -> None:
        """Register the jsonb codec on a freshly-acquired pool connection.

        Without this, jsonb columns come back as raw JSON strings instead
        of decoded Python objects, and INSERT would need an explicit
        ``::jsonb`` cast plus a pre-serialized string on the write side.
        """
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
            format="text",
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> "AnalysisRepository":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _init_schema(self) -> None:
        """Create the meal_analyses table if it doesn't exist yet."""
        ddl = """
        CREATE TABLE IF NOT EXISTS meal_analyses (
            id           SERIAL PRIMARY KEY,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            image_path   TEXT NOT NULL,
            ingredients  JSONB NOT NULL,
            totals       JSONB NOT NULL
        );
        """
        try:
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                await conn.execute(ddl)
        except Exception as e:
            raise StorageError(f"Could not initialize schema: {e}") from e

    async def save_analysis(
        self,
        image_path: str,
        ingredients: list[Ingredient],
        totals: Nutrition,
    ) -> int:
        """Persist one analyzed meal. Returns the new row's id."""
        assert self._pool is not None, "call connect() before using the repository"
        # No manual json.dumps / ::jsonb cast needed — the jsonb codec
        # registered in _init_connection serializes these automatically.
        ingredients_data = [ing.model_dump() for ing in ingredients]
        totals_data = totals.to_dict()
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO meal_analyses (image_path, ingredients, totals)
                    VALUES ($1, $2, $3)
                    RETURNING id;
                    """,
                    image_path,
                    ingredients_data,
                    totals_data,
                )
        except Exception as e:
            raise StorageError(f"Could not save analysis for {image_path!r}: {e}") from e
        return row["id"]

    async def get_history(self, limit: int = 20) -> list[MealAnalysisRecord]:
        """Return the most recent analyses, newest first."""
        assert self._pool is not None, "call connect() before using the repository"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, created_at, image_path, ingredients, totals
                    FROM meal_analyses
                    ORDER BY created_at DESC
                    LIMIT $1;
                    """,
                    limit,
                )
        except Exception as e:
            raise StorageError(f"Could not fetch history: {e}") from e
        return [_row_to_record(row) for row in rows]

    async def get_by_id(self, analysis_id: int) -> MealAnalysisRecord | None:
        """Return one analysis by id, or None if it doesn't exist."""
        assert self._pool is not None, "call connect() before using the repository"
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, created_at, image_path, ingredients, totals
                    FROM meal_analyses
                    WHERE id = $1;
                    """,
                    analysis_id,
                )
        except Exception as e:
            raise StorageError(f"Could not fetch analysis {analysis_id}: {e}") from e
        return _row_to_record(row) if row is not None else None


def _row_to_record(row: Any) -> MealAnalysisRecord:
    """Convert an asyncpg Record into a validated MealAnalysisRecord.

    With the jsonb codec registered in `_init_connection`, asyncpg returns
    the jsonb columns as decoded Python objects (list/dict), so no extra
    json.loads is needed here.
    """
    return MealAnalysisRecord(
        id=row["id"],
        created_at=row["created_at"],
        image_path=row["image_path"],
        ingredients=[Ingredient(**item) for item in row["ingredients"]],
        totals=Nutrition(**row["totals"]),
    )
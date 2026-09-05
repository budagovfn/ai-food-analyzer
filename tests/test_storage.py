"""Unit and error-path tests for src.storage.repository.

All tests here mock asyncpg completely — no real database connection is
made, per the brief's requirement that tests must run offline (§4.6).

Uses unittest.IsolatedAsyncioTestCase for the async cases instead of
pytest-asyncio, since the latter isn't in requirements.txt yet and this
avoids adding a dependency just for tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import pytest

from ai.schemas import Ingredient, Nutrition
from src.storage.repository import (
    AnalysisRepository,
    MealAnalysisRecord,
    StorageError,
    _normalize_dsn,
)


# ---------------------------------------------------------------------------
# Fakes standing in for asyncpg's Pool / Connection / acquire() context manager
# ---------------------------------------------------------------------------


class FakeConnection:
    """Minimal stand-in for asyncpg.Connection.

    Configure a result and/or an error per method; every call is recorded
    in `.calls` as (method_name, query, args) so tests can assert on the
    SQL and parameters the repository sent.
    """

    def __init__(
        self,
        *,
        execute_result: object = None,
        fetchrow_result: object = None,
        fetch_result: list | None = None,
        execute_error: Exception | None = None,
        fetchrow_error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.execute_result = execute_result
        self.fetchrow_result = fetchrow_result
        self.fetch_result = fetch_result if fetch_result is not None else []
        self.execute_error = execute_error
        self.fetchrow_error = fetchrow_error
        self.fetch_error = fetch_error
        self.calls: list[tuple[str, str, tuple]] = []

    async def execute(self, query: str, *args: object) -> object:
        self.calls.append(("execute", query, args))
        if self.execute_error:
            raise self.execute_error
        return self.execute_result

    async def fetchrow(self, query: str, *args: object) -> object:
        self.calls.append(("fetchrow", query, args))
        if self.fetchrow_error:
            raise self.fetchrow_error
        return self.fetchrow_result

    async def fetch(self, query: str, *args: object) -> list:
        self.calls.append(("fetch", query, args))
        if self.fetch_error:
            raise self.fetch_error
        return self.fetch_result


class FakeAcquireContext:
    """Stand-in for the async context manager returned by pool.acquire()."""

    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class FakePool:
    """Stand-in for asyncpg.Pool — always hands back the same connection."""

    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.closed = False

    def acquire(self) -> FakeAcquireContext:
        return FakeAcquireContext(self.conn)

    async def close(self) -> None:
        self.closed = True


async def _make_connected_repo(conn: FakeConnection) -> AnalysisRepository:
    """Build a repo whose pool creation is mocked to succeed immediately."""
    pool = FakePool(conn)
    repo = AnalysisRepository("postgresql+asyncpg://u:p@h/db")
    with patch(
        "src.storage.repository.asyncpg.create_pool",
        AsyncMock(return_value=pool),
    ):
        await repo.connect()
    return repo


# ---------------------------------------------------------------------------
# Pure function — no mocking needed
# ---------------------------------------------------------------------------


def test_normalize_dsn_strips_asyncpg_suffix() -> None:
    assert _normalize_dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"


def test_normalize_dsn_leaves_plain_dsn_unchanged() -> None:
    assert _normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class TestConnect(IsolatedAsyncioTestCase):
    async def test_connect_success_creates_pool_and_runs_schema_ddl(self) -> None:
        conn = FakeConnection(execute_result="CREATE TABLE")
        pool = FakePool(conn)
        repo = AnalysisRepository("postgresql+asyncpg://u:p@h/db")

        with patch(
            "src.storage.repository.asyncpg.create_pool",
            AsyncMock(return_value=pool),
        ) as mock_create:
            await repo.connect()

        mock_create.assert_awaited_once()
        # +asyncpg suffix must be stripped before reaching asyncpg itself
        args, _kwargs = mock_create.call_args
        assert args[0] == "postgresql://u:p@h/db"
        assert repo._pool is pool
        assert conn.calls[0][0] == "execute"  # schema DDL ran

    async def test_connect_wraps_pool_creation_errors(self) -> None:
        repo = AnalysisRepository("postgresql://u:p@h/db")
        with patch(
            "src.storage.repository.asyncpg.create_pool",
            AsyncMock(side_effect=OSError("connection refused")),
        ):
            with self.assertRaises(StorageError):
                await repo.connect()

    async def test_connect_wraps_schema_init_errors(self) -> None:
        conn = FakeConnection(execute_error=RuntimeError("permission denied"))
        pool = FakePool(conn)
        repo = AnalysisRepository("postgresql://u:p@h/db")
        with patch(
            "src.storage.repository.asyncpg.create_pool",
            AsyncMock(return_value=pool),
        ):
            with self.assertRaises(StorageError):
                await repo.connect()


# ---------------------------------------------------------------------------
# save_analysis()
# ---------------------------------------------------------------------------


class TestSaveAnalysis(IsolatedAsyncioTestCase):
    async def test_save_analysis_returns_new_id(self) -> None:
        conn = FakeConnection(fetchrow_result={"id": 42})
        repo = await _make_connected_repo(conn)
        ingredients = [Ingredient(name="egg", estimated_grams=50, confidence=0.9)]
        totals = Nutrition(kcal=70, protein_g=6, carbs_g=0.5, fat_g=5)

        new_id = await repo.save_analysis("data/egg.jpg", ingredients, totals)

        assert new_id == 42
        method, query, args = conn.calls[-1]
        assert method == "fetchrow"
        assert "INSERT INTO meal_analyses" in query
        assert args[0] == "data/egg.jpg"
        assert args[1] == [ingredients[0].model_dump()]
        assert args[2] == totals.to_dict()

    async def test_save_analysis_wraps_db_errors(self) -> None:
        conn = FakeConnection(fetchrow_error=RuntimeError("db down"))
        repo = await _make_connected_repo(conn)
        with self.assertRaises(StorageError):
            await repo.save_analysis("data/x.jpg", [], Nutrition())

    async def test_save_analysis_before_connect_raises(self) -> None:
        repo = AnalysisRepository("postgresql://u:p@h/db")  # connect() never called
        with self.assertRaises(AssertionError):
            await repo.save_analysis("data/x.jpg", [], Nutrition())


# ---------------------------------------------------------------------------
# get_by_id()
# ---------------------------------------------------------------------------


class TestGetById(IsolatedAsyncioTestCase):
    async def test_get_by_id_returns_none_when_missing(self) -> None:
        conn = FakeConnection(fetchrow_result=None)
        repo = await _make_connected_repo(conn)

        result = await repo.get_by_id(999)

        assert result is None

    async def test_get_by_id_returns_typed_record(self) -> None:
        row = {
            "id": 1,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "image_path": "data/x.jpg",
            "ingredients": [{"name": "egg", "estimated_grams": 50.0, "confidence": 0.9}],
            "totals": {"kcal": 70.0, "protein_g": 6.0, "carbs_g": 0.5, "fat_g": 5.0},
        }
        conn = FakeConnection(fetchrow_result=row)
        repo = await _make_connected_repo(conn)

        record = await repo.get_by_id(1)

        assert isinstance(record, MealAnalysisRecord)
        assert record.ingredients[0] == Ingredient(
            name="egg", estimated_grams=50, confidence=0.9
        )
        assert record.totals.kcal == 70.0

    async def test_get_by_id_wraps_db_errors(self) -> None:
        conn = FakeConnection(fetchrow_error=RuntimeError("boom"))
        repo = await _make_connected_repo(conn)
        with self.assertRaises(StorageError):
            await repo.get_by_id(1)


# ---------------------------------------------------------------------------
# get_history()
# ---------------------------------------------------------------------------


class TestGetHistory(IsolatedAsyncioTestCase):
    async def test_get_history_returns_records_newest_first(self) -> None:
        rows = [
            {
                "id": 2,
                "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "image_path": "b.jpg",
                "ingredients": [],
                "totals": {},
            },
            {
                "id": 1,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "image_path": "a.jpg",
                "ingredients": [],
                "totals": {},
            },
        ]
        conn = FakeConnection(fetch_result=rows)
        repo = await _make_connected_repo(conn)

        history = await repo.get_history(limit=5)

        assert [r.id for r in history] == [2, 1]
        method, _query, args = conn.calls[-1]
        assert method == "fetch"
        assert args[0] == 5  # limit passed through

    async def test_get_history_wraps_db_errors(self) -> None:
        conn = FakeConnection(fetch_error=RuntimeError("timeout"))
        repo = await _make_connected_repo(conn)
        with self.assertRaises(StorageError):
            await repo.get_history()


# ---------------------------------------------------------------------------
# async context manager (__aenter__ / __aexit__)
# ---------------------------------------------------------------------------


class TestContextManager(IsolatedAsyncioTestCase):
    async def test_context_manager_connects_and_closes_pool(self) -> None:
        conn = FakeConnection(execute_result="CREATE TABLE")
        pool = FakePool(conn)

        with patch(
            "src.storage.repository.asyncpg.create_pool",
            AsyncMock(return_value=pool),
        ):
            async with AnalysisRepository("postgresql://u:p@h/db") as repo:
                assert repo._pool is pool

        assert pool.closed is True

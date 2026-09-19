"""Tests for src/cli.py — the command-line interface."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("USDA_API_KEY", "test-usda-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")

import ai  # noqa: E402

import src.cli as cli  # noqa: E402
from src.ai_service import AIServiceError  # noqa: E402
from src.config import settings  # noqa: E402
from src.storage.repository import AnalysisRepository, StorageError  # noqa: E402


def make_ingredient(name="egg", grams=50.0, confidence=0.9):
    return SimpleNamespace(name=name, estimated_grams=grams, confidence=confidence)


def make_portion(kcal=155.0, protein=13.0, carbs=1.1, fat=11.0):
    return SimpleNamespace(kcal=kcal, protein_g=protein, carbs_g=carbs, fat_g=fat)


def make_facts(portion=None):
    portion = portion or make_portion()
    return SimpleNamespace(kcal_per_100g=portion.kcal, for_grams=lambda g: portion)


@pytest.fixture
def image_path(tmp_path):
    p = tmp_path / "meal.jpg"
    p.write_bytes(b"\xff\xd8\xff fake-jpeg-bytes")
    return p


@pytest.fixture(autouse=True)
def stub_repository(monkeypatch):
    """No real Postgres connection for any CLI test."""
    monkeypatch.setattr(AnalysisRepository, "connect", AsyncMock(return_value=None))
    monkeypatch.setattr(AnalysisRepository, "close", AsyncMock(return_value=None))


class TestCmdAnalyzeValidation:
    def test_nonexistent_file_returns_2(self, capsys):
        args = argparse.Namespace(image="does/not/exist.jpg", no_save=True)
        code = cli.cmd_analyze(args)
        assert code == 2
        assert "Invalid input" in capsys.readouterr().err

    def test_unsupported_extension_returns_2(self, tmp_path, capsys):
        bad_file = tmp_path / "meal.txt"
        bad_file.write_text("not an image")
        args = argparse.Namespace(image=str(bad_file), no_save=True)
        code = cli.cmd_analyze(args)
        assert code == 2
        assert "Unsupported file type" in capsys.readouterr().err

    def test_oversized_file_returns_2(self, image_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "max_image_size_mb", 0.00001)
        args = argparse.Namespace(image=str(image_path), no_save=True)
        code = cli.cmd_analyze(args)
        assert code == 2
        assert "exceeds" in capsys.readouterr().err


class TestCmdAnalyzeAIResults:
    def test_meal_not_recognized(self, image_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "identify_ingredients", lambda path: [])
        args = argparse.Namespace(image=str(image_path), no_save=True)

        code = cli.cmd_analyze(args)

        assert code == 0
        assert "Meal not recognized" in capsys.readouterr().out

    def test_ai_service_error_returns_1(self, image_path, monkeypatch, capsys):
        def boom(path):
            raise AIServiceError("provider timed out")

        monkeypatch.setattr(cli, "identify_ingredients", boom)
        args = argparse.Namespace(image=str(image_path), no_save=True)

        code = cli.cmd_analyze(args)

        assert code == 1
        assert "AI analysis failed" in capsys.readouterr().err

    def test_all_lookups_fail_returns_1(self, image_path, monkeypatch, capsys):
        ingredient = make_ingredient()
        monkeypatch.setattr(cli, "identify_ingredients", lambda path: [ingredient])

        async def fake_lookup_all(ingredients):
            return {}, [(ingredient, "USDA down")]

        monkeypatch.setattr(cli, "lookup_all", fake_lookup_all)
        args = argparse.Namespace(image=str(image_path), no_save=True)

        code = cli.cmd_analyze(args)

        out = capsys.readouterr()
        assert code == 1
        assert "No ingredients could be priced" in out.out
        assert "Skipping" in out.err


class TestCmdAnalyzeHappyPath:
    def _patch_pipeline(self, monkeypatch, ingredient, facts):
        monkeypatch.setattr(cli, "identify_ingredients", lambda path: [ingredient])

        async def fake_lookup_all(ingredients):
            return {ingredient.name: facts}, []

        monkeypatch.setattr(cli, "lookup_all", fake_lookup_all)
        monkeypatch.setattr(
            ai,
            "compute_totals",
            lambda ingredients, facts_by_name: SimpleNamespace(
                kcal=155.0, protein_g=13.0, carbs_g=1.1, fat_g=11.0
            ),
        )

    def test_saves_by_default(self, image_path, monkeypatch, capsys):
        ingredient = make_ingredient()
        facts = make_facts()
        self._patch_pipeline(monkeypatch, ingredient, facts)
        monkeypatch.setattr(
            AnalysisRepository, "save_analysis", AsyncMock(return_value=42)
        )
        args = argparse.Namespace(image=str(image_path), no_save=False)

        code = cli.cmd_analyze(args)

        out = capsys.readouterr().out
        assert code == 0
        assert "TOTAL" in out
        assert "Saved as analysis #42" in out

    def test_no_save_flag_skips_db(self, image_path, monkeypatch, capsys):
        ingredient = make_ingredient()
        facts = make_facts()
        self._patch_pipeline(monkeypatch, ingredient, facts)
        save_mock = AsyncMock(return_value=42)
        monkeypatch.setattr(AnalysisRepository, "save_analysis", save_mock)
        args = argparse.Namespace(image=str(image_path), no_save=True)

        code = cli.cmd_analyze(args)

        assert code == 0
        save_mock.assert_not_called()
        assert "Saved as analysis" not in capsys.readouterr().out

    def test_storage_error_on_save_returns_1(self, image_path, monkeypatch, capsys):
        ingredient = make_ingredient()
        facts = make_facts()
        self._patch_pipeline(monkeypatch, ingredient, facts)
        monkeypatch.setattr(
            AnalysisRepository,
            "save_analysis",
            AsyncMock(side_effect=StorageError("db is down")),
        )
        args = argparse.Namespace(image=str(image_path), no_save=False)

        code = cli.cmd_analyze(args)

        assert code == 1
        assert "could not save to database" in capsys.readouterr().err


class TestCmdHistory:
    def test_no_records(self, monkeypatch, capsys):
        monkeypatch.setattr(
            AnalysisRepository, "get_history", AsyncMock(return_value=[])
        )
        args = argparse.Namespace(limit=10)

        code = cli.cmd_history(args)

        assert code == 0
        assert "No analyses saved yet" in capsys.readouterr().out

    def test_prints_records(self, monkeypatch, capsys):
        record = SimpleNamespace(
            id=7,
            created_at=datetime(2026, 1, 5, 12, 30),
            image_path="data/meal.jpg",
            ingredients=[make_ingredient(), make_ingredient(name="toast")],
            totals=make_portion(kcal=430.0),
        )
        monkeypatch.setattr(
            AnalysisRepository, "get_history", AsyncMock(return_value=[record])
        )
        args = argparse.Namespace(limit=10)

        code = cli.cmd_history(args)

        out = capsys.readouterr().out
        assert code == 0
        assert "#7" in out
        assert "430 kcal" in out
        assert "(2 ingredients)" in out

    def test_storage_error_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            AnalysisRepository,
            "get_history",
            AsyncMock(side_effect=StorageError("connection refused")),
        )
        args = argparse.Namespace(limit=10)

        code = cli.cmd_history(args)

        assert code == 1
        assert "Could not fetch history" in capsys.readouterr().err
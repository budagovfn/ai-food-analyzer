"""Tests for src/api.py — the FastAPI HTTP interface."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Settings() is instantiated at import time in src/config.py and requires
# these three fields — set safe dummy values before anything imports it.
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("USDA_API_KEY", "test-usda-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")

import ai  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import app  # noqa: E402
from src.ai_service import AIServiceError  # noqa: E402
from src.config import settings  # noqa: E402
from src.storage.repository import AnalysisRepository  # noqa: E402


def make_ingredient(name="egg", grams=50.0, confidence=0.9):
    return SimpleNamespace(name=name, estimated_grams=grams, confidence=confidence)


def make_portion(kcal=155.0, protein=13.0, carbs=1.1, fat=11.0):
    return SimpleNamespace(kcal=kcal, protein_g=protein, carbs_g=carbs, fat_g=fat)


def make_facts(portion=None):
    portion = portion or make_portion()
    return SimpleNamespace(kcal_per_100g=portion.kcal, for_grams=lambda g: portion)


@pytest.fixture
def client(monkeypatch):
    """TestClient with the DB layer fully stubbed out (no real Postgres needed)."""
    monkeypatch.setattr(AnalysisRepository, "connect", AsyncMock(return_value=None))
    monkeypatch.setattr(AnalysisRepository, "close", AsyncMock(return_value=None))
    monkeypatch.setattr(AnalysisRepository, "save_analysis", AsyncMock(return_value=1))
    with TestClient(app) as c:
        yield c
    # api.py writes uploaded images to disk — clean up after ourselves.
    upload_dir = Path(settings.image_upload_dir)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)
    # src/api.py creates UPLOAD_DIR once, at import time. If the teardown
    # leaves it deleted, every later test in this file fails with
    # FileNotFoundError when it tries to save an upload, so put it back.
    upload_dir.mkdir(parents=True, exist_ok=True)


def _jpg_file(name="meal.jpg", content=b"\xff\xd8\xff fake-jpeg-bytes"):
    return {"file": (name, content, "image/jpeg")}


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAnalyzeHappyPath:
    def test_meal_recognized_returns_totals(self, client, monkeypatch):
        ingredient = make_ingredient(name="egg", grams=50.0, confidence=0.9)
        portion = make_portion(kcal=78.0, protein=6.5, carbs=0.5, fat=5.5)
        facts = make_facts(portion)

        monkeypatch.setattr("src.api.identify_ingredients", lambda path: [ingredient])
        monkeypatch.setattr(
            "src.api.lookup_all",
            AsyncMock(return_value=({"egg": facts}, [])),
        )
        monkeypatch.setattr(
            ai,
            "compute_totals",
            lambda ingredients, facts_by_name: SimpleNamespace(
                kcal=78.0, protein_g=6.5, carbs_g=0.5, fat_g=5.5
            ),
        )

        resp = client.post("/analyze", files=_jpg_file())

        assert resp.status_code == 200
        body = resp.json()
        assert body["meal_recognized"] is True
        assert body["ingredients"] == [
            {
                "name": "egg",
                "weight_g": 50.0,
                "confidence": 0.9,
                "kcal": 78.0,
                "protein": 6.5,
                "carbs": 0.5,
                "fat": 5.5,
            }
        ]
        assert body["total_weight_g"] == 50.0
        assert body["totals"] == {"kcal": 78.0, "protein": 6.5, "carbs": 0.5, "fat": 5.5}

    def test_ingredient_with_failed_lookup_is_skipped(self, client, monkeypatch):
        good = make_ingredient(name="egg")
        bad = make_ingredient(name="mystery-sauce")
        facts = make_facts()

        monkeypatch.setattr("src.api.identify_ingredients", lambda path: [good, bad])
        monkeypatch.setattr(
            "src.api.lookup_all",
            AsyncMock(return_value=({"egg": facts}, [(bad, "not found in USDA")])),
        )
        monkeypatch.setattr(
            ai, "compute_totals", lambda ingredients, facts_by_name: SimpleNamespace(
                kcal=155.0, protein_g=13.0, carbs_g=1.1, fat_g=11.0
            ),
        )

        resp = client.post("/analyze", files=_jpg_file())

        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["ingredients"]]
        assert names == ["egg"]


class TestAnalyzeEdgeCases:
    def test_meal_not_recognized(self, client, monkeypatch):
        monkeypatch.setattr("src.api.identify_ingredients", lambda path: [])

        resp = client.post("/analyze", files=_jpg_file())

        assert resp.status_code == 200
        body = resp.json()
        assert body["meal_recognized"] is False
        assert body["ingredients"] == []

    def test_all_nutrition_lookups_fail(self, client, monkeypatch):
        ingredient = make_ingredient()
        monkeypatch.setattr("src.api.identify_ingredients", lambda path: [ingredient])
        monkeypatch.setattr(
            "src.api.lookup_all",
            AsyncMock(return_value=({}, [(ingredient, "USDA down")])),
        )

        resp = client.post("/analyze", files=_jpg_file())

        assert resp.status_code == 200
        body = resp.json()
        assert body["meal_recognized"] is True
        assert "nutrition lookup failed" in body["message"]

    def test_ai_service_error_returns_502(self, client, monkeypatch):
        def boom(path):
            raise AIServiceError("provider timed out")

        monkeypatch.setattr("src.api.identify_ingredients", boom)

        resp = client.post("/analyze", files=_jpg_file())

        assert resp.status_code == 502
        assert "AI analysis failed" in resp.json()["detail"]


class TestAnalyzeValidation:
    def test_rejects_unsupported_extension(self, client):
        resp = client.post(
            "/analyze",
            files={"file": ("meal.txt", b"not an image", "text/plain")},
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    def test_rejects_empty_file(self, client):
        resp = client.post(
            "/analyze",
            files={"file": ("meal.jpg", b"", "image/jpeg")},
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_rejects_oversized_file(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_image_size_mb", 0.00001)  # ~10 bytes
        resp = client.post("/analyze", files=_jpg_file(content=b"x" * 1000))
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]
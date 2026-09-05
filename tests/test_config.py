"""Unit tests for src.config.Settings.

Every test passes ``_env_file=None`` to bypass the project's real .env
file entirely, and clears the matching OS env var with monkeypatch where
a test relies on a field being *absent*. This keeps the tests fully
isolated from whatever happens to be in the developer's local .env or
shell environment.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings

REQUIRED = dict(
    google_api_key="g-key",
    usda_api_key="u-key",
    database_url="postgresql://u:p@h/db",
)


def test_settings_loads_explicit_values() -> None:
    settings = Settings(_env_file=None, **REQUIRED)

    assert settings.google_api_key == "g-key"
    assert settings.usda_api_key == "u-key"
    assert settings.database_url == "postgresql://u:p@h/db"


def test_settings_applies_defaults_when_not_overridden() -> None:
    settings = Settings(_env_file=None, **REQUIRED)

    assert settings.llm_provider == "gemini"
    assert settings.nutrition_provider == "usda"
    assert settings.log_level == "INFO"
    assert settings.nutrition_cache_ttl_seconds == 86400
    assert settings.max_image_size_mb == 5
    assert settings.http_port == 8000


def test_settings_overrides_defaults_when_given() -> None:
    settings = Settings(_env_file=None, llm_provider="openai", http_port=9000, **REQUIRED)

    assert settings.llm_provider == "openai"
    assert settings.http_port == 9000


def test_settings_requires_google_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, usda_api_key="u-key", database_url="postgresql://u:p@h/db")


def test_settings_requires_usda_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USDA_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, google_api_key="g-key", database_url="postgresql://u:p@h/db")


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, google_api_key="g-key", usda_api_key="u-key")


def test_settings_ignores_unknown_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_RANDOM_VAR", "whatever")

    settings = Settings(_env_file=None, **REQUIRED)

    assert not hasattr(settings, "some_random_var")

from __future__ import annotations
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
# Absolute path to .env at the project root — independent of the working directory
# from which the process is launched (PyCharm sometimes starts it from src/).

# IMPORTANT: SettingsConfigDict(env_file=...) below only populates OUR
# Settings object. The provided ai/ package (which we cannot edit) reads
# LLM_PROVIDER / ANTHROPIC_API_KEY / GOOGLE_API_KEY etc. directly via
# os.getenv(...), so it never sees anything unless the values are also
# pushed into the real process environment — that's what load_dotenv does.
load_dotenv(dotenv_path=_ENV_FILE)


class Settings(BaseSettings):
    # AI and VLM settings (using Gemini)
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.6-flash"
    google_api_key: str

    # Nutrition Provider Settings (USDA)
    nutrition_provider: str = "usda"
    usda_api_key: str

    # System and SE-settings (according to .env)
    log_level: str = "INFO"
    database_url: str
    nutrition_cache_ttl_seconds: int = 86400
    max_image_size_mb: int = 5
    http_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instance of settings for import to other modules
settings = Settings()
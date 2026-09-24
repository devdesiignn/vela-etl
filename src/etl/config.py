"""Pipeline configuration, loaded from environment (.env in local dev).

Settings is constructed lazily via get_settings() rather than as a module-
level singleton: not every extractor path needs Azure credentials (the
pytesseract adapter needs none), so importing this module must not force
validation of Azure-specific env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_doc_intel_endpoint: str
    azure_doc_intel_key: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

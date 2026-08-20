"""Configuration — read once, from the environment, validated at startup.

Why a settings *class* on day one, when two constants would do?

Because the alternative — `os.getenv("PORT")` sprinkled through the code — fails
in the worst possible way: silently, in production, with `None` where an int was
expected. `pydantic-settings` reads the environment once, coerces the types, and
raises at import time if something is missing or malformed. A container that
refuses to start is a far better outcome than one that starts and misbehaves.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every knob the app has. Override any of them with an env var."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SHELFSPACE_",
        extra="ignore",
    )

    app_name: str = "Shelfspace API"
    environment: str = "development"
    debug: bool = True

    # Uvicorn binds here. 8001 = day 01; each day uses its own port so you can
    # leave yesterday's server running while you compare behaviour.
    host: str = "127.0.0.1"
    port: int = 8001

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process, not per request.

    `lru_cache` also makes this a natural FastAPI dependency (Day 08) and gives
    tests a single, overridable seam.
    """
    return Settings()

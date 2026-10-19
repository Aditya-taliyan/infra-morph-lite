from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]
# One isolated directory per generation run (gitignored).
GENERATED_DIR = BACKEND_DIR / "generated"
# Shared by all runs so the AWS provider is downloaded once (gitignored).
PLUGIN_CACHE_DIR = BACKEND_DIR / ".terraform-plugin-cache"


class Settings(BaseSettings):
    app_env: str = "development"
    debug: bool = False
    # init downloads providers, so it gets longer than the other commands.
    terraform_init_timeout_seconds: float = 120
    terraform_timeout_seconds: float = 60

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

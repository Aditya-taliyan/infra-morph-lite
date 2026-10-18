from app.core.config import Settings


def test_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "true")

    settings = Settings(_env_file=None)

    assert settings.app_env == "production"
    assert settings.debug is True


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.debug is False

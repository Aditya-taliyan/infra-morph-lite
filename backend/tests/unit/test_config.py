from app.core.config import Settings


def test_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "true")

    settings = Settings(_env_file=None)

    assert settings.app_env == "production"
    assert settings.debug is True


def test_infracost_settings(monkeypatch):
    monkeypatch.setenv("INFRACOST_API_KEY", "ico-test")
    monkeypatch.delenv("INFRACOST_TIMEOUT_SECONDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.infracost_api_key.get_secret_value() == "ico-test"
    assert "ico-test" not in repr(settings)
    assert settings.infracost_timeout_seconds == 90


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.debug is False

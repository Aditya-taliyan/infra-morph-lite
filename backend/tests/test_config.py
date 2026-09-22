from app.core.config import settings


def test_settings_load_from_environment():
    assert settings.app_env == "development"
    assert settings.debug is True

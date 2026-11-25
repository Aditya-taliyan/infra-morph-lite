import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def no_real_infracost_key(monkeypatch):
    """A developer's .env must not make the tests call the Infracost API."""
    monkeypatch.setattr(settings, "infracost_api_key", None)

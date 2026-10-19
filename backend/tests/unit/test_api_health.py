import pytest
from fastapi.testclient import TestClient

from app.core.version import __version__
from app.main import app


@pytest.mark.parametrize("available", [True, False])
def test_health_reports_version_and_terraform(monkeypatch, available):
    monkeypatch.setattr("app.services.health.terraform_available", lambda: available)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "terraform_available": available,
    }


def test_app_metadata():
    assert app.title == "InfraMorph AI"
    assert app.version == __version__


def test_unknown_route_uses_error_shape():
    response = TestClient(app).get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

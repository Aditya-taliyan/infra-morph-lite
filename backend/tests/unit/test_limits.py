"""The documented limits: default values, and that exactly the limit is still accepted.

Over-the-limit and timeout behaviour: test_api_errors.py, test_api_validate.py,
test_api_interpret.py, test_terraform_runner.py, test_infracost_runner.py.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_spec_engine
from app.core.config import Settings
from app.main import app
from tests.api_helpers import spec_json
from tests.ollama_helpers import FakeOllama, demo_json

VALIDATE = "/api/v1/specifications/validate"


@pytest.mark.parametrize("name, value", [
    ("api_max_body_bytes", 100_000),
    ("api_max_resources", 50),
    ("terraform_init_timeout_seconds", 120),
    ("terraform_timeout_seconds", 60),
    ("infracost_timeout_seconds", 90),
])
def test_default_limits(monkeypatch, name, value):
    monkeypatch.delenv(name.upper(), raising=False)
    assert getattr(Settings(_env_file=None), name) == value


@pytest.mark.parametrize("size, status", [(100_000, 200), (100_001, 413)])
def test_body_at_and_over_the_limit(size, status):
    body = json.dumps(spec_json("demo2")).encode().ljust(size)
    response = TestClient(app).post(
        VALIDATE, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == status


def test_exactly_50_resources_is_accepted():
    spec = spec_json("demo2", lambda d: d.update(resources=[
        {"kind": "StorageBucket", "name": f"bucket-{i:03d}"} for i in range(50)
    ]))
    assert TestClient(app).post(VALIDATE, json=spec).status_code == 200


def test_exactly_2000_characters_is_accepted():
    app.dependency_overrides[get_spec_engine] = FakeOllama(demo_json("demo2")).engine
    try:
        response = TestClient(app).post(
            "/api/v1/specifications",
            json={"requirement": "web server ".ljust(2000, "x"), "provider": "aws"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200

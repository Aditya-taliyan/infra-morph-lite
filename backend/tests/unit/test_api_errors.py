import json
import logging
from pathlib import Path

import pytest

from app.api.errors import field_path
from app.core.config import settings
from app.main import app
from tests.api_helpers import FakeRunner, make_client, spec_json

GENERATE = "/api/v1/generate"
VALIDATE = "/api/v1/specifications/validate"


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_schema_error_uses_structured_shape(tmp_path: Path):
    runner = FakeRunner()
    data = spec_json("demo2")
    del data["region"]
    data["unexpected"] = True

    response = make_client(runner, tmp_path).post(GENERATE, json=data)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "schema_validation_failed"
    fields = {d["field"] for d in body["error"]["details"]}
    assert fields == {"region", "unexpected"}
    assert runner.calls == []


def test_malformed_json_is_a_schema_error(tmp_path: Path):
    response = make_client(FakeRunner(), tmp_path).post(
        VALIDATE, content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_validation_failed"


def test_unexpected_exception_returns_500_without_traceback(tmp_path: Path, caplog):
    runner = FakeRunner(error=RuntimeError("secret internal detail"))
    client = make_client(runner, tmp_path, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = client.post(GENERATE, json=spec_json("demo2"))

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred. See the server log.",
            "details": None,
        }
    }
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text
    # ...but it is in the server log.
    assert "secret internal detail" in caplog.text


def test_oversize_body_rejected_by_content_length(tmp_path: Path):
    body = json.dumps({"padding": "x" * (settings.api_max_body_bytes + 1)}).encode()

    response = make_client(FakeRunner(), tmp_path).post(
        GENERATE, content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_oversize_chunked_body_rejected(tmp_path: Path):
    def chunks():
        # No Content-Length: the middleware must count the bytes itself.
        for _ in range(settings.api_max_body_bytes // 1000 + 2):
            yield b" " * 1000

    response = make_client(FakeRunner(), tmp_path).post(
        GENERATE, content=chunks(), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


@pytest.mark.parametrize(
    "loc, expected",
    [
        (("body", "resources", 0, "ComputeInstance", "size"), "resources[0].ComputeInstance.size"),
        (("body", "name"), "name"),
        (("body",), "(body)"),
    ],
)
def test_field_path(loc, expected):
    assert field_path(loc) == expected

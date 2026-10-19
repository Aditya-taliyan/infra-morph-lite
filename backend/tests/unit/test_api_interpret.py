import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_spec_engine
from app.main import app
from tests.ollama_helpers import REFUSED, FakeOllama, demo_json

URL = "/api/v1/specifications"
DEMO2_TEXT = "Create an AWS VPC with a public subnet and a web server"


@pytest.fixture
def use_ollama():
    """Make the endpoint's engine talk to a FakeOllama with the given replies."""

    def install(*replies) -> FakeOllama:
        fake = FakeOllama(*replies)
        app.dependency_overrides[get_spec_engine] = fake.engine
        return fake

    yield install
    app.dependency_overrides.clear()


client = TestClient(app)


def test_ollama_spec_is_returned_with_validation(use_ollama):
    use_ollama(demo_json("demo2"))

    response = client.post(URL, json={"requirement": DEMO2_TEXT, "provider": "aws"})

    assert response.status_code == 200
    body = response.json()
    assert body["interpreter"] == "ollama"
    assert body["attempts"] == 1
    assert body["fallback_reason"] is None
    assert body["spec"]["name"] == "vpc-web"
    assert body["validation"]["is_valid"] is True
    assert [w["code"] for w in body["validation"]["warnings"]] == ["W01"]


def test_ollama_down_uses_rule_based(use_ollama):
    use_ollama(REFUSED)

    response = client.post(URL, json={"requirement": DEMO2_TEXT, "provider": "aws"})

    assert response.status_code == 200
    body = response.json()
    assert body["interpreter"] == "rule-based"
    assert "unavailable" in body["fallback_reason"]
    assert body["spec"]["provider"] == "aws"


def test_returned_spec_is_accepted_by_validate(use_ollama):
    use_ollama(REFUSED)
    spec = client.post(URL, json={"requirement": DEMO2_TEXT, "provider": "aws"}).json()["spec"]

    # Human in the loop: the reviewed spec is what the client sends on.
    response = client.post(f"{URL}/validate", json=spec)

    assert response.status_code == 200
    assert response.json()["is_valid"] is True


def test_no_spec_is_422_with_errors(use_ollama):
    use_ollama(REFUSED)

    response = client.post(URL, json={"requirement": "make me a sandwich", "provider": "aws"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "interpretation_failed"
    assert error["details"]["interpreter"] == "rule-based"
    assert any("No supported infrastructure" in e for e in error["details"]["errors"])


@pytest.mark.parametrize("body", [
    {"requirement": "x" * 2001, "provider": "aws"},
    {"requirement": "   ", "provider": "aws"},
    {"requirement": "web server", "provider": "gcp"},
    {"requirement": "web server"},
    {"requirement": "web server", "provider": "aws", "skip_validation": True},
])
def test_bad_request_body_is_rejected(use_ollama, body):
    fake = use_ollama()

    response = client.post(URL, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_validation_failed"
    assert fake.requests == []

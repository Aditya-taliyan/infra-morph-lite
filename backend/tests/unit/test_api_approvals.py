from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_approval_service
from app.infrastructure.approval_store import ApprovalStore
from app.main import app
from app.services.approval import ApprovalService
from app.services.generation import InfrastructureGenerationService
from tests.api_helpers import FakeRunner
from tests.helpers import load_spec

URL = "/api/v1/approvals"


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app.dependency_overrides[get_approval_service] = lambda: ApprovalService(
        ApprovalStore(tmp_path / "approvals"), tmp_path / "generated"
    )
    return TestClient(app)


def checked_run(tmp_path: Path, fail: str | None = None) -> str:
    return InfrastructureGenerationService(
        FakeRunner(fail=fail), generated_root=tmp_path / "generated"
    ).run_pipeline(load_spec("demo2")).run_id


def test_approve_then_get_and_list(client: TestClient, tmp_path: Path):
    first, second = checked_run(tmp_path), checked_run(tmp_path)

    response = client.post(URL, json={"run_id": first, "approved_by": "Aditya", "comment": "ok"})
    client.post(URL, json={"run_id": second, "approved_by": "Aditya"})

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == first
    assert body["comment"] == "ok"
    assert body["plan_summary"]["to_add"] == 11
    assert len(body["spec_hash"]) == len(body["files_hash"]) == 64
    assert body["approved_at"].endswith("Z")

    assert client.get(f"{URL}/{body['approval_id']}").json() == body
    listed = client.get(URL).json()
    assert [r["run_id"] for r in listed] == [second, first]


def test_unknown_run_is_404(client: TestClient):
    response = client.post(URL, json={"run_id": "20261124T100000Z-000000", "approved_by": "A"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_reapproval_is_409(client: TestClient, tmp_path: Path):
    run_id = checked_run(tmp_path)
    first = client.post(URL, json={"run_id": run_id, "approved_by": "A"}).json()

    response = client.post(URL, json={"run_id": run_id, "approved_by": "B"})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "already_approved"
    assert error["details"] == {"approval_id": first["approval_id"]}


@pytest.mark.parametrize("fail, code", [("validate", "validation_not_passed"), ("plan", "plan_not_passed")])
def test_run_that_did_not_pass_is_422(client: TestClient, tmp_path: Path, fail: str, code: str):
    response = client.post(URL, json={"run_id": checked_run(tmp_path, fail), "approved_by": "A"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "body",
    [
        {"run_id": "x", "approved_by": ""},
        {"run_id": "x", "approved_by": "A" * 81},
        {"run_id": "x", "approved_by": "A", "comment": "c" * 501},
        {"run_id": "x", "approved_by": "A", "files_hash": "0" * 64},  # hashes are server-side
    ],
)
def test_bad_request_body_is_422(client: TestClient, body: dict):
    response = client.post(URL, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_validation_failed"


@pytest.mark.parametrize("approval_id", ["0" * 32, "not-an-id"])
def test_unknown_approval_is_404(client: TestClient, approval_id: str):
    response = client.get(f"{URL}/{approval_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "approval_not_found"


def test_empty_list(client: TestClient):
    assert client.get(URL).json() == []

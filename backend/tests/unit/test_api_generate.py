import json
import threading
from pathlib import Path

import pytest

from app.generators.terraform_generator import FILES
from app.infrastructure.infracost_runner import InfracostRunner, parse_breakdown
from app.main import app
from tests.api_helpers import FakeRunner, make_client, spec_json
from tests.helpers import FIXTURES, resource

URL = "/api/v1/generate"


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_generate_success(tmp_path: Path):
    runner = FakeRunner()

    response = make_client(runner, tmp_path).post(URL, json=spec_json("demo2"))

    assert response.status_code == 200
    body = response.json()
    assert (tmp_path / body["run_id"]).is_dir()
    assert sorted(body["files"]) == sorted(FILES)
    assert 'resource "aws_vpc"' in body["files"]["main.tf"]
    assert [s["step"] for s in body["steps"]] == [
        "terraform init", "terraform fmt", "terraform validate", "terraform plan", "terraform show"
    ]
    assert body["terraform_ok"] is True
    assert body["plan_summary"]["to_add"] == 11
    assert body["cost"]["status"] == "unavailable"
    assert body["cost"]["message"].startswith("Cost estimation unavailable. ")
    assert body["cost"]["monthly_total"] is None
    assert [w["code"] for w in body["validation"]["warnings"]] == ["W01"]
    assert runner.calls == ["init", "fmt", "validate", "plan", "show"]


def test_cost_wording_in_the_response(tmp_path: Path, monkeypatch):
    sample = json.loads((FIXTURES / "infracost" / "breakdown.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(InfracostRunner, "estimate", lambda *_: parse_breakdown(sample))

    body = make_client(FakeRunner(), tmp_path).post(URL, json=spec_json("demo2")).json()

    pricing = {r["address"]: r["pricing"] for r in body["cost"]["resources"]}
    assert pricing["aws_vpc.main"] == "no charge"
    assert pricing["aws_example_thing.x"] == "not priced"


def test_failed_terraform_step_is_still_200(tmp_path: Path):
    runner = FakeRunner(fail="validate")

    response = make_client(runner, tmp_path).post(URL, json=spec_json("demo2"))

    assert response.status_code == 200
    body = response.json()
    assert body["terraform_ok"] is False
    assert body["steps"][-1]["step"] == "terraform validate"
    assert body["steps"][-1]["status"] == "failed"
    assert "simulated failure" in body["steps"][-1]["stderr"]
    assert body["plan_summary"] is None
    assert "plan" not in runner.calls


def test_semantic_errors_return_422_and_never_run_terraform(tmp_path: Path):
    runner = FakeRunner()

    def subnet_outside_network(data):
        resource(data, "main-vpc")["subnets"][0]["cidr"] = "10.99.1.0/24"

    response = make_client(runner, tmp_path).post(
        URL, json=spec_json("demo2", subnet_outside_network)
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "semantic_validation_failed"
    assert "E05" in [i["code"] for i in error["details"]["issues"]]
    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []  # no run directory either


def test_azure_generates_and_skips_plan(tmp_path: Path):
    runner = FakeRunner()

    response = make_client(runner, tmp_path).post(URL, json=spec_json("demo4_azure"))

    assert response.status_code == 200
    body = response.json()
    assert 'resource "azurerm_linux_virtual_machine"' in body["files"]["main.tf"]
    assert [s["step"] for s in body["steps"]] == [
        "terraform init", "terraform fmt", "terraform validate", "terraform plan"
    ]
    assert body["steps"][-1]["status"] == "skipped"
    assert body["plan_summary"] is None
    assert "plan" not in runner.calls


def test_concurrent_generate_returns_409(tmp_path: Path):
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    client = make_client(runner, tmp_path)
    first: dict = {}

    def run_first():
        first["response"] = client.post(URL, json=spec_json("demo2"))

    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert runner.started.wait(timeout=10), "first generation never started"

        second = client.post(URL, json=spec_json("demo2"))

        assert second.status_code == 409
        assert second.json()["error"] == {
            "code": "generation_in_progress",
            "message": "another generation is in progress",
            "details": None,
        }
    finally:
        gate.set()
        thread.join(timeout=10)

    assert first["response"].status_code == 200
    # The lock is released afterwards, so the next run is accepted.
    assert client.post(URL, json=spec_json("demo2")).status_code == 200

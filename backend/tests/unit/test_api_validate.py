from fastapi.testclient import TestClient

from app.main import app
from tests.api_helpers import spec_json
from tests.helpers import resource

URL = "/api/v1/specifications/validate"
client = TestClient(app)


def test_valid_spec_returns_warnings():
    response = client.post(URL, json=spec_json("demo2"))

    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is True
    assert body["errors"] == []
    assert [w["code"] for w in body["warnings"]] == ["W01"]
    assert body["warnings"][0]["severity"] == "warning"


def test_semantically_invalid_spec_returns_errors_with_200():
    def subnet_outside_network(data):
        resource(data, "main-vpc")["subnets"][0]["cidr"] = "10.99.1.0/24"

    response = client.post(URL, json=spec_json("demo2", subnet_outside_network))

    # Validation itself worked; the answer is "not valid".
    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is False
    assert "E05" in [e["code"] for e in body["errors"]]


def test_azure_spec_can_be_validated():
    def to_azure(data):
        data["provider"] = "azure"
        data["region"] = "eastus"

    response = client.post(URL, json=spec_json("demo2", to_azure))

    assert response.status_code == 200
    assert response.json()["is_valid"] is True


def test_schema_error_has_readable_field_path():
    def bad_size(data):
        resource(data, "web-server")["size"] = "huge"

    response = client.post(URL, json=spec_json("demo2", bad_size))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "schema_validation_failed"
    assert [d["field"] for d in error["details"]] == ["resources[2].ComputeInstance.size"]


def test_too_many_resources_rejected():
    def many_buckets(data):
        data["resources"] = [
            {"kind": "StorageBucket", "name": f"bucket-{i:03d}"} for i in range(51)
        ]

    response = client.post(URL, json=spec_json("demo2", many_buckets))

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details[0]["field"] == "resources"
    assert "at most 50" in details[0]["message"]

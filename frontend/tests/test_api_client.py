"""The frontend's HTTP client, against httpx.MockTransport (no real backend)."""

import json

import httpx
import pytest

from api_client import (
    DEFAULT_BACKEND_URL,
    ApiError,
    BackendUnreachable,
    InfraMorphClient,
    backend_url,
)

BASE = "http://backend.test"


def client_with(handler) -> InfraMorphClient:
    return InfraMorphClient(BASE, transport=httpx.MockTransport(handler))


def test_health_success_calls_v1_path():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "terraform_available": True})

    assert client_with(handler).health()["terraform_available"] is True
    assert seen == [("GET", f"{BASE}/api/v1/health")]


def test_create_specification_sends_requirement_and_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/specifications"
        assert json.loads(request.content) == {"requirement": "Create an AWS storage bucket.", "provider": "aws"}
        return httpx.Response(200, json={"interpreter": "rule-based", "attempts": 1})

    result = client_with(handler).create_specification("Create an AWS storage bucket.", "aws")
    assert result == {"interpreter": "rule-based", "attempts": 1}


def test_validate_and_generate_post_the_spec_as_is():
    spec = {"name": "demo", "resources": []}
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert json.loads(request.content) == spec
        return httpx.Response(200, json={"ok": True})

    client = client_with(handler)
    client.validate_specification(spec)
    client.generate(spec)
    assert paths == ["/api/v1/specifications/validate", "/api/v1/generate"]


def test_backend_down_raises_unreachable_with_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(BackendUnreachable) as info:
        client_with(handler).health()
    assert info.value.url == BASE
    assert "ConnectError" in info.value.reason


def test_timeout_raises_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(BackendUnreachable) as info:
        client_with(handler).generate({})
    assert "no answer within" in info.value.reason


def test_api_error_shape_is_parsed():
    details = {"issues": [{"code": "E01", "severity": "error", "message": "bad", "resource_name": "x"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"error": {"code": "semantic_validation_failed", "message": "Has errors.", "details": details}},
        )

    with pytest.raises(ApiError) as info:
        client_with(handler).generate({})
    assert info.value.status_code == 422
    assert info.value.code == "semantic_validation_failed"
    assert info.value.message == "Has errors."
    assert info.value.details == details


def test_error_without_error_body_still_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(ApiError) as info:
        client_with(handler).health()
    assert info.value.status_code == 502
    assert info.value.code == "unexpected_response"


def test_backend_url_from_env(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    assert backend_url() == DEFAULT_BACKEND_URL
    monkeypatch.setenv("BACKEND_URL", "http://example.test:9000/")
    assert backend_url() == "http://example.test:9000"
    assert InfraMorphClient().base_url == "http://example.test:9000"

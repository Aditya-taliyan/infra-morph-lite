"""Thin HTTP client for the InfraMorph backend API.

The frontend talks to the backend only through this module. It holds no
business logic: it sends requests and turns failures into two exceptions
the UI can show as friendly messages.
"""

import os
from typing import Any

import httpx

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
API_PREFIX = "/api/v1"

HEALTH_TIMEOUT_SECONDS = 3
# The backend may try Ollama 3 times at up to 120 s each, and a Terraform
# run is init + fmt + validate + plan; both stay under this limit.
SLOW_TIMEOUT_SECONDS = 400


def backend_url() -> str:
    return os.environ.get("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


class BackendUnreachable(Exception):
    """No HTTP answer at all (backend not started, wrong URL, timeout)."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"Backend not reachable at {url}: {reason}")
        self.url = url
        self.reason = reason


class ApiError(Exception):
    """The backend answered with an error response {"error": {...}}."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class InfraMorphClient:
    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or backend_url()).rstrip("/")
        # `transport` lets tests replace the network with httpx.MockTransport.
        self._transport = transport

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=HEALTH_TIMEOUT_SECONDS)

    def create_specification(self, requirement: str, provider: str) -> dict:
        body = {"requirement": requirement, "provider": provider}
        return self._request("POST", "/specifications", json=body, timeout=SLOW_TIMEOUT_SECONDS)

    def validate_specification(self, spec: dict) -> dict:
        return self._request(
            "POST", "/specifications/validate", json=spec, timeout=SLOW_TIMEOUT_SECONDS
        )

    def generate(self, spec: dict) -> dict:
        return self._request("POST", "/generate", json=spec, timeout=SLOW_TIMEOUT_SECONDS)

    def _request(self, method: str, path: str, timeout: float, json: Any = None) -> dict:
        try:
            with httpx.Client(
                base_url=self.base_url + API_PREFIX, timeout=timeout, transport=self._transport
            ) as client:
                response = client.request(method, path, json=json)
        except httpx.TimeoutException:
            raise BackendUnreachable(self.base_url, f"no answer within {timeout:g} s") from None
        except httpx.TransportError as exc:
            raise BackendUnreachable(self.base_url, type(exc).__name__) from None

        if response.is_success:
            return response.json()
        raise _api_error(response)


def _api_error(response: httpx.Response) -> ApiError:
    """Read the backend's error shape; fall back to a generic message."""
    try:
        error = response.json()["error"]
        return ApiError(
            response.status_code, error["code"], error["message"], error.get("details")
        )
    except (ValueError, KeyError, TypeError):
        return ApiError(
            response.status_code,
            "unexpected_response",
            f"The backend answered HTTP {response.status_code} without an error body.",
        )

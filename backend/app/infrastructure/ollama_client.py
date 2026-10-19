"""Tiny client for a local Ollama server's REST API (POST /api/chat).

Plain httpx, no SDK. The JSON schema passed as "format" makes Ollama
constrain the model's output to that structure; temperature 0 makes the
answer repeatable. The schema only shapes the output: the caller still
validates everything the model returns.

Prompts and answers are logged at DEBUG only, never at INFO.
"""

import logging
from typing import Any

import httpx

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Ollama answered, but not with a usable chat response."""


class OllamaUnavailableError(OllamaError):
    """Ollama could not be reached or did not answer in time."""


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        # Tests pass an httpx.MockTransport; nothing then touches the network.
        self.transport = transport

    @classmethod
    def from_settings(cls, config: Settings = settings) -> "OllamaClient":
        return cls(config.ollama_url, config.ollama_model, config.ollama_timeout_seconds)

    def chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        """Send a chat and return the assistant message's content."""
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        logger.debug("Ollama request to %s: %s", self.model, messages)
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError(
                f"Ollama did not answer within {self.timeout_seconds:g}s"
            ) from exc
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(f"cannot reach Ollama at {self.base_url}: {exc}") from exc

        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned HTTP {response.status_code}: {_error_text(response)}"
            )
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaError("Ollama returned a response without message.content") from exc
        if not isinstance(content, str):
            raise OllamaError("Ollama returned a non-text message.content")
        logger.debug("Ollama answer: %s", content)
        return content


def _error_text(response: httpx.Response) -> str:
    # e.g. {"error": "model 'qwen2.5:7b' not found"}
    try:
        return str(response.json().get("error", response.text))[:300]
    except ValueError:
        return response.text[:300]

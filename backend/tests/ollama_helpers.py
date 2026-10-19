"""A scripted fake Ollama server built on httpx.MockTransport (no network)."""

import json

import httpx

from app.domain.enums import Provider
from app.infrastructure.ollama_client import OllamaClient
from app.interpreters.ollama_interpreter import OllamaInterpreter
from app.interpreters.rule_based import RuleBasedInterpreter
from app.services.spec_engine import SpecEngine
from tests.helpers import FIXTURES

REFUSED = httpx.ConnectError("[WinError 10061] connection refused")


def demo_json(name: str, mutate=None) -> str:
    """fixtures/specs/<name>.json as a JSON string, the way a model would answer."""
    data = json.loads((FIXTURES / "specs" / f"{name}.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    return json.dumps(data)


class FakeOllama:
    """Answers each /api/chat request with the next scripted reply.

    A reply is the assistant's content (str), an httpx.Response, or an
    exception to raise (e.g. REFUSED). `requests` holds every request body.
    """

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        self.requests.append(json.loads(request.content))
        assert self.replies, "the model was called more often than scripted"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(
            200, json={"model": "fake", "message": {"role": "assistant", "content": reply},
                       "done": True}
        )

    def client(self) -> OllamaClient:
        return OllamaClient(
            "http://ollama.test", "fake-model", 5, transport=httpx.MockTransport(self.handler)
        )

    def interpreter(self) -> OllamaInterpreter:
        return OllamaInterpreter(self.client())

    def engine(self) -> SpecEngine:
        return SpecEngine(self.interpreter(), RuleBasedInterpreter())

    def messages(self, call: int) -> list[dict]:
        return self.requests[call]["messages"]


AWS = Provider.AWS
AZURE = Provider.AZURE

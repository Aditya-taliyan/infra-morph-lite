import httpx
import pytest

from app.core.config import Settings
from app.infrastructure.ollama_client import OllamaClient, OllamaError, OllamaUnavailableError
from tests.ollama_helpers import REFUSED, FakeOllama

SCHEMA = {"type": "object"}
MESSAGES = [{"role": "user", "content": "hi"}]


def test_sends_schema_format_no_stream_and_temperature_zero():
    fake = FakeOllama('{"ok": true}')

    content = fake.client().chat(MESSAGES, SCHEMA)

    assert content == '{"ok": true}'
    body = fake.requests[0]
    assert body["model"] == "fake-model"
    assert body["messages"] == MESSAGES
    assert body["stream"] is False
    assert body["format"] == SCHEMA
    assert body["options"] == {"temperature": 0}


def test_connection_refused_is_unavailable():
    with pytest.raises(OllamaUnavailableError, match="cannot reach Ollama"):
        FakeOllama(REFUSED).client().chat(MESSAGES, SCHEMA)


def test_timeout_is_unavailable():
    fake = FakeOllama(httpx.ReadTimeout("timed out"))

    with pytest.raises(OllamaUnavailableError, match="did not answer within 5s"):
        fake.client().chat(MESSAGES, SCHEMA)


def test_http_error_includes_ollama_message():
    fake = FakeOllama(httpx.Response(404, json={"error": "model 'fake-model' not found"}))

    with pytest.raises(OllamaError, match="HTTP 404: model 'fake-model' not found") as exc_info:
        fake.client().chat(MESSAGES, SCHEMA)
    assert not isinstance(exc_info.value, OllamaUnavailableError)


def test_response_without_content_is_an_error():
    fake = FakeOllama(httpx.Response(200, json={"done": True}))

    with pytest.raises(OllamaError, match="without message.content"):
        fake.client().chat(MESSAGES, SCHEMA)


def test_settings_defaults(monkeypatch):
    for name in ("OLLAMA_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    client = OllamaClient.from_settings(Settings(_env_file=None))

    assert client.base_url == "http://127.0.0.1:11434"
    assert client.model == "qwen2.5:7b"
    assert client.timeout_seconds == 120

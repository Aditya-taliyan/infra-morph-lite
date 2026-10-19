"""Calls the real local Ollama. Excluded from "pytest -q" by pytest.ini; run with "pytest -m integration".

Skips when Ollama is not running or the configured model is not pulled.
"""

import httpx
import pytest

from app.core.config import settings
from app.domain.enums import Provider
from app.interpreters.ollama_interpreter import OllamaInterpreter


def _ollama_ready() -> str | None:
    """None when usable, otherwise the reason to skip."""
    try:
        response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
        models = {m["name"] for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return f"Ollama is not running at {settings.ollama_url}"
    if settings.ollama_model not in models:
        return f"model {settings.ollama_model} is not pulled (ollama pull {settings.ollama_model})"
    return None


_SKIP_REASON = _ollama_ready()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]


def test_real_model_makes_a_valid_web_server_spec():
    result = OllamaInterpreter().interpret("Create a basic AWS web server", Provider.AWS)

    assert result.ok, result.errors
    assert result.spec.provider is Provider.AWS
    assert result.validation.is_valid
    assert "ComputeInstance" in {r.kind for r in result.spec.resources}

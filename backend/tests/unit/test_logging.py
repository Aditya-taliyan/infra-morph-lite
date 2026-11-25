import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_spec_engine
from app.core.logging import (
    REQUEST_FIELDS,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    redact,
)
from app.main import app
from tests.api_helpers import FakeRunner, make_client, spec_json
from tests.ollama_helpers import REFUSED, FakeOllama

API = "/api/v1"
# Stands for text a user typed; must never appear in any log record.
MARKER = "zebra-42"


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- redaction ---


@pytest.mark.parametrize("text", [
    "api_key=hunter2",
    '{"db_password": "hunter2 and more"}',
    "SECRET: hunter2",
    "token: Bearer hunter2",
    "found ico-hunter2 in output",
    "AWS said AKIA" + "HUNTER2",
])
def test_secrets_are_masked(text):
    assert "***" in redact(text)
    assert "hunter2" not in redact(text).lower()


def test_ordinary_text_is_unchanged():
    text = "infracost breakdown: exit code 0 in 1.20 s"
    assert redact(text) == text


def emit(log) -> dict:
    """Send one record from a child logger through the startup handler setup."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("app.test_child")
    logger.addHandler(handler)
    try:
        log(logger)
    finally:
        logger.removeHandler(handler)
    return json.loads(stream.getvalue())


def test_message_arguments_are_masked():
    assert emit(lambda log: log.error("api_key=%s", "hunter2"))["message"] == "api_key=***"


def test_extra_fields_are_masked():
    record = logging.makeLogRecord({
        "msg": "x",
        "api_token": "hunter2",
        "data": {"password": "hunter2", "list": ["ico-hunter2"]},
    })

    clean = RedactionFilter().filter(record)

    assert clean.api_token == "***"
    assert clean.data == {"password": "***", "list": ["***"]}
    assert record.api_token == "hunter2"  # the original is not changed


def test_traceback_has_frames_but_no_exception_message():
    def fail(log):
        try:
            raise ValueError(" ".join(["web server for", MARKER]))
        except ValueError:
            log.exception("failed")

    line = emit(fail)
    assert "test_logging.py" in line["traceback"] and "ValueError" in line["traceback"]
    assert MARKER not in line["traceback"]


def test_uvicorn_colour_message_is_dropped():
    record = logging.makeLogRecord({"msg": "port %d", "args": (80,), "color_message": "port %d!"})
    clean = RedactionFilter().filter(record)
    assert clean.getMessage() == "port 80" and not hasattr(clean, "color_message")


def test_bad_log_call_does_not_raise():
    record = logging.makeLogRecord({"msg": "%d", "args": ("x",)})
    assert "could not be formatted" in RedactionFilter().filter(record).getMessage()


def test_startup_covers_root_and_uvicorn_once(monkeypatch):
    handler = logging.StreamHandler()
    monkeypatch.setattr(logging.getLogger("uvicorn"), "handlers", [handler])
    root = logging.getLogger()
    # Own handlers only: pytest adds and removes its own around each test.
    monkeypatch.setattr(root, "handlers", [])

    configure_logging()
    configure_logging()

    assert len(root.handlers) == 1
    for h in [*root.handlers, handler]:
        assert len([f for f in h.filters if isinstance(f, RedactionFilter)]) == 1


# --- structured request log ---


def request_record(caplog) -> logging.LogRecord:
    (record,) = [r for r in caplog.records if r.name == "app.api.middleware"]
    assert all(hasattr(record, f) for f in REQUEST_FIELDS)
    return record


def assert_not_logged(caplog, text: str = MARKER) -> None:
    """Looks at every attribute of every app record, not only the message.

    The TestClient's own httpx/asyncio records are client side.
    """
    records = [r for r in caplog.records if r.name.startswith("app")]
    assert records
    assert all(text not in repr(r.__dict__) + r.getMessage() for r in records)


def test_generate_record(tmp_path, caplog):
    client = make_client(FakeRunner(fail="plan"), tmp_path)
    spec = spec_json("demo2", lambda d: d["tags"].update({"Note": MARKER}))
    with caplog.at_level(logging.DEBUG):
        response = client.post(f"{API}/generate", json=spec, headers={"X-Request-ID": "abc-1"})

    record = request_record(caplog)
    assert response.headers["X-Request-ID"] == record.request_id == "abc-1"
    assert record.operation == "generate"
    assert record.provider == "aws"
    assert record.resource_count == len(spec["resources"])
    assert record.validation_status == "valid"
    assert record.terraform_status == "failed"
    assert record.duration_ms >= 0
    assert record.error_type is None
    assert_not_logged(caplog)


@pytest.mark.parametrize("text, status", [
    (f"AWS VPC with a web server called {MARKER}", "valid"),
    (f"make me a {MARKER}", "invalid"),
])
def test_requirement_text_is_never_logged(caplog, text, status):
    app.dependency_overrides[get_spec_engine] = FakeOllama(REFUSED).engine
    with caplog.at_level(logging.DEBUG):
        TestClient(app).post(f"{API}/specifications", json={"requirement": text, "provider": "aws"})

    record = request_record(caplog)
    assert record.operation == "interpret_requirement"
    assert record.provider == "aws"
    assert record.validation_status == status
    assert record.error_type == (None if status == "valid" else "interpretation_failed")
    assert_not_logged(caplog)


def test_rejected_text_is_not_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        TestClient(app).post(
            f"{API}/specifications", json={"requirement": MARKER * 300, "provider": "aws"}
        )
    assert request_record(caplog).error_type == "schema_validation_failed"
    assert_not_logged(caplog)


def test_validate_record(caplog):
    with caplog.at_level(logging.INFO):
        TestClient(app).post(f"{API}/specifications/validate", json=spec_json("demo2"))

    record = request_record(caplog)
    assert (record.operation, record.provider) == ("validate_specification", "aws")
    assert record.validation_status == "valid"
    assert record.terraform_status is None


def test_oversize_request_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        TestClient(app).post(f"{API}/generate", content=b"x" * 100_001)
    assert request_record(caplog).error_type == "request_too_large"


def test_crash_is_logged_with_type_and_id(tmp_path, caplog):
    client = make_client(
        FakeRunner(error=RuntimeError(f"boom {MARKER}")), tmp_path, raise_server_exceptions=False
    )
    with caplog.at_level(logging.INFO):
        response = client.post(f"{API}/generate", json=spec_json("demo2"))

    record = request_record(caplog)
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == record.request_id
    assert record.error_type == "RuntimeError"
    assert_not_logged(caplog)


@pytest.mark.parametrize("sent", [None, "", "a b", "x" * 65, '{"fake": 1}'])
def test_missing_or_unsafe_request_id_is_replaced(sent):
    headers = {} if sent is None else {"X-Request-ID": sent}
    ids = {
        TestClient(app).get(f"{API}/health", headers=headers).headers["X-Request-ID"]
        for _ in range(2)
    }
    assert len(ids) == 2 and sent not in ids


def test_unknown_url_is_not_logged(caplog):
    with caplog.at_level(logging.INFO):
        TestClient(app).get(f"/{MARKER}")

    record = request_record(caplog)
    assert (record.operation, record.error_type) == ("unmatched", "not_found")
    assert_not_logged(caplog)

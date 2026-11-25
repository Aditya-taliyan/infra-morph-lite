"""Log redaction, JSON log lines and the fields of the per-request log record."""

import copy
import json
import logging
import re
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

MASK = "***"

REQUEST_FIELDS = (
    "request_id",
    "operation",
    "provider",
    "resource_count",
    "validation_status",
    "terraform_status",
    "duration_ms",
    "error_type",
)

_SENSITIVE_NAME = re.compile(r"key|secret|token|password", re.IGNORECASE)
# api_key=x, "password": "x", token: Bearer x
_SENSITIVE_PAIR = re.compile(
    r"""((?:key|secret|token|password)[\w-]*["']?\s*[:=]\s*)"""
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:Bearer\s+)?[^\s,;&}]+)""",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"\bico-[\w-]+|AKIA[0-9A-Z]+")
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)

_request: ContextVar[dict[str, Any] | None] = ContextVar("request_fields", default=None)


def redact(text: str) -> str:
    return _SECRET_VALUE.sub(MASK, _SENSITIVE_PAIR.sub(rf"\1{MASK}", text))


def _redact_value(name: str, value: Any) -> Any:
    if _SENSITIVE_NAME.search(name):
        return MASK
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value("", v) for v in value]
    return redact(value) if isinstance(value, str) else value


def safe_traceback(exc: BaseException) -> str:
    """Stack frames and exception type. No message: it can quote user input."""
    frames = "".join(traceback.format_tb(exc.__traceback__))
    return f"Traceback (most recent call last):\n{frames}{type(exc).__name__}"


class RedactionFilter(logging.Filter):
    """Masks secrets. Returns a copy, so other handlers see the original record."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        clean = copy.copy(record)
        try:
            message = record.getMessage()
        except Exception:  # a wrong logging call must not break the request
            message = "(log message could not be formatted)"
        exc = record.exc_info[1] if record.exc_info else None
        clean.msg, clean.args = redact(message), None
        # uvicorn's coloured copy of the unformatted message.
        clean.__dict__.pop("color_message", None)
        for name, value in list(clean.__dict__.items()):
            if name not in _STANDARD_ATTRS:
                clean.__dict__[name] = _redact_value(name, value)
        clean.exc_info = None
        clean.exc_text = redact(safe_traceback(exc)) if exc else None
        return clean


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line |= {f: getattr(record, f) for f in REQUEST_FIELDS if hasattr(record, f)}
        if record.exc_text:
            line["traceback"] = record.exc_text
        return json.dumps(line, default=str)


def configure_logging() -> None:
    """Called at startup; safe to call again.

    The filter goes on handlers: a filter on the root logger itself would not
    see records of child loggers such as app.services.
    """
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Raw URLs are client text; the request log record replaces this logger.
    logging.getLogger("uvicorn.access").disabled = True
    for logger in (root, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
        for handler in logger.handlers:
            if not any(isinstance(f, RedactionFilter) for f in handler.filters):
                handler.addFilter(RedactionFilter())


def begin_request(request_id: str) -> dict[str, Any]:
    """Start collecting log fields. The dict is shared with worker threads."""
    fields: dict[str, Any] = dict.fromkeys(REQUEST_FIELDS)
    fields["request_id"] = request_id
    _request.set(fields)
    return fields


def end_request() -> None:
    _request.set(None)


def record_request(**fields: Any) -> None:
    """Add fields to the current request's log record; no-op outside a request (CLI)."""
    current = _request.get()
    if current is not None:
        current.update(fields)

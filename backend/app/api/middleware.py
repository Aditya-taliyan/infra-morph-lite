"""Request logging, and rejection of bodies larger than a limit before the app parses them."""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response
from app.core.logging import begin_request, end_request

logger = logging.getLogger(__name__)

# Anything else from the client is replaced: the id is written to the log.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestLogMiddleware:
    """One log record per request. Holds no request text: only the fields that
    services and error handlers add with record_request()."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        sent = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = sent if _REQUEST_ID.fullmatch(sent) else uuid.uuid4().hex
        # Also for the 500 handler, which runs outside this middleware.
        scope.setdefault("state", {})["request_id"] = request_id
        fields = begin_request(request_id)
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []), (b"x-request-id", request_id.encode())
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            fields["error_type"] = type(exc).__name__
            raise
        finally:
            # The route function's name; the raw URL is client text.
            fields["operation"] = getattr(scope.get("endpoint"), "__name__", "unmatched")
            fields["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
            logger.info("request", extra=fields)
            end_request()


class BodySizeLimitMiddleware:
    """Pure ASGI middleware: checks Content-Length, then counts the bytes
    actually received (covers chunked bodies sent without Content-Length)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        # Buffer the whole body (at most max_bytes), then hand it to the app.
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            more_body = message.get("more_body", False)

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(
            413,
            "request_too_large",
            f"The request body is larger than {self.max_bytes} bytes.",
        )
        await response(scope, receive, send)

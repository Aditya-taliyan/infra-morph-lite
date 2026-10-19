"""Reject request bodies larger than a limit before the app parses them."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response


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

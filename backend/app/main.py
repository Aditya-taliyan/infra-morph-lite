"""FastAPI application. Run with: uvicorn app.main:app"""

from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestLogMiddleware
from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.version import __version__


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="InfraMorph AI", version=__version__)
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.api_max_body_bytes)
    # Added last = outermost, so rejected (413) requests are logged too.
    app.add_middleware(RequestLogMiddleware)
    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()

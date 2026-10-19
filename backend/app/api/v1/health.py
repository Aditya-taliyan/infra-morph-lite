from fastapi import APIRouter

from app.models.health import HealthResponse
from app.services.health import health_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> HealthResponse:
    return HealthResponse(**health_status())

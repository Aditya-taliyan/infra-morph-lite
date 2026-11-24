from fastapi import APIRouter

from app.api.v1 import approvals, generate, health, specifications

router = APIRouter()
router.include_router(health.router)
router.include_router(specifications.router)
router.include_router(generate.router)
router.include_router(approvals.router)

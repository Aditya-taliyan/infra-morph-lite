from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_spec_engine
from app.api.errors import ApiError
from app.models.errors import ErrorResponse
from app.models.interpretation import InterpretRequest, InterpretResponse
from app.models.specifications import SpecificationRequest, ValidationResponse
from app.services.spec_engine import SpecEngine
from app.validators.semantic import validate

router = APIRouter(prefix="/specifications", tags=["specifications"])


@router.post(
    "",
    responses={422: {"model": ErrorResponse, "description": "No valid spec could be made"}},
)
def interpret_requirement(
    body: InterpretRequest, engine: Annotated[SpecEngine, Depends(get_spec_engine)]
) -> InterpretResponse:
    """Natural language -> validated spec. Does not generate Terraform: the
    user reviews the spec first, then sends it to /generate."""
    # Sync handler: the blocking Ollama call runs in a worker thread.
    result = engine.interpret(body.requirement, body.provider)
    if not result.ok:
        raise ApiError(
            422,
            "interpretation_failed",
            "No valid specification could be made from the requirement.",
            {
                "interpreter": result.final.interpreter,
                "fallback_reason": result.fallback_reason,
                "errors": result.errors,
                "notes": result.final.notes,
            },
        )
    return InterpretResponse.from_result(result)


@router.post("/validate", responses={422: {"model": ErrorResponse}})
def validate_specification(spec: SpecificationRequest) -> ValidationResponse:
    """Semantic validation only; never runs Terraform."""
    return ValidationResponse.from_result(validate(spec))

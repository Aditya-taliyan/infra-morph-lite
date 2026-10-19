from fastapi import APIRouter

from app.models.errors import ErrorResponse
from app.models.specifications import SpecificationRequest, ValidationResponse
from app.validators.semantic import validate

router = APIRouter(prefix="/specifications", tags=["specifications"])


@router.post("/validate", responses={422: {"model": ErrorResponse}})
def validate_specification(spec: SpecificationRequest) -> ValidationResponse:
    """Semantic validation only; never runs Terraform."""
    return ValidationResponse.from_result(validate(spec))

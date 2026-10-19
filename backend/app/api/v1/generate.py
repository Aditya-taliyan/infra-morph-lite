from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_generation_service
from app.api.errors import ApiError
from app.models.errors import ErrorResponse
from app.models.generation import GenerateResponse
from app.models.specifications import SpecificationRequest
from app.services.generation import InfrastructureGenerationService

router = APIRouter(tags=["generation"])


@router.post(
    "/generate",
    responses={
        400: {"model": ErrorResponse, "description": "Provider not supported yet"},
        409: {"model": ErrorResponse, "description": "Another generation is running"},
        422: {"model": ErrorResponse, "description": "Schema or semantic errors"},
    },
)
def generate(
    spec: SpecificationRequest,
    service: Annotated[InfrastructureGenerationService, Depends(get_generation_service)],
) -> GenerateResponse:
    """Full pipeline. A failed Terraform step is still a 200: the request
    worked, the infrastructure did not pass; see `steps` and `terraform_ok`."""
    # Sync handler: FastAPI runs it in a worker thread, so blocking
    # Terraform calls do not stall the event loop.
    result = service.run_pipeline(spec)
    if not result.validation.is_valid:
        raise ApiError(
            422,
            "semantic_validation_failed",
            "The specification has semantic errors; Terraform was not run.",
            {"issues": [i.model_dump(mode="json") for i in result.validation.issues]},
        )
    return GenerateResponse.from_result(result)

"""Request body and validation response for specification endpoints."""

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.resources import Resource
from app.domain.spec import InfrastructureSpecification
from app.domain.validation import ValidationIssue, ValidationResult


class SpecificationRequest(InfrastructureSpecification):
    """The IR as received over HTTP, plus a size limit that protects the server.

    The limit is a transport concern, so it lives here and not in the IR.
    """

    resources: list[Resource] = Field(min_length=1, max_length=settings.api_max_resources)


class ValidationResponse(BaseModel):
    is_valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]

    @classmethod
    def from_result(cls, result: ValidationResult) -> "ValidationResponse":
        return cls(is_valid=result.is_valid, errors=result.errors, warnings=result.warnings)

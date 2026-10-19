"""Request and response of POST /specifications (natural language -> IR)."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Provider
from app.domain.spec import InfrastructureSpecification
from app.models.specifications import ValidationResponse
from app.services.spec_engine import SpecEngineResult

MAX_REQUIREMENT_CHARS = 2000


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement: str = Field(min_length=1, max_length=MAX_REQUIREMENT_CHARS)
    provider: Provider


class InterpretResponse(BaseModel):
    interpreter: str
    # Model calls the producing interpreter needed (rule-based is always 1).
    attempts: int
    spec: InfrastructureSpecification
    validation: ValidationResponse
    # Set when the rule-based fallback produced the spec.
    fallback_reason: str | None
    notes: list[str]

    @classmethod
    def from_result(cls, result: SpecEngineResult) -> "InterpretResponse":
        final = result.final
        return cls(
            interpreter=final.interpreter,
            attempts=final.attempts,
            spec=final.spec,
            validation=ValidationResponse.from_result(final.validation),
            fallback_reason=result.fallback_reason,
            notes=final.notes,
        )

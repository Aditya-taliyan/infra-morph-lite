"""Natural language -> IR: the interface every interpreter implements.

An interpreter turns a requirement into a candidate IR document (a dict).
check_candidate() is the only way a candidate becomes a spec, and every
interpreter must use it: the user's provider is forced, then Pydantic schema
validation, then semantic validation. A spec with semantic errors is never
returned, so no interpreter can hand a half-valid spec to the generator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.domain.enums import Provider
from app.domain.spec import InfrastructureSpecification
from app.domain.validation import ValidationResult
from app.validators.semantic import validate


class InterpretationStatus(StrEnum):
    SUCCESS = "success"
    # The interpreter could not run at all (e.g. Ollama is not reachable).
    UNAVAILABLE = "unavailable"
    # It ran, but produced no valid spec.
    FAILED = "failed"


@dataclass
class InterpretationResult:
    interpreter: str
    status: InterpretationStatus
    # Set only on SUCCESS; always schema- and semantically valid then.
    spec: InfrastructureSpecification | None = None
    # Validation of the returned spec (warnings included); None without a spec.
    validation: ValidationResult | None = None
    attempts: int = 0
    # Why attempts failed, prefixed with the attempt number when retried.
    errors: list[str] = field(default_factory=list)
    # Things the user should know that are not errors (e.g. ignored requests).
    notes: list[str] = field(default_factory=list)
    # The last raw model output, for debugging only.
    raw_output: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is InterpretationStatus.SUCCESS


class SpecInterpreter(ABC):
    name: str

    @abstractmethod
    def interpret(self, requirement: str, provider: Provider) -> InterpretationResult:
        """Never raises for bad input or bad model output; reports it in the result."""


@dataclass
class CandidateCheck:
    spec: InfrastructureSpecification | None
    validation: ValidationResult | None
    errors: list[str]


def format_schema_errors(exc: ValidationError) -> list[str]:
    """("resources", 2, "kind") -> "resources[2].kind: <message>"."""
    lines = []
    for error in exc.errors():
        path = ""
        for part in error["loc"]:
            path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
        lines.append(f"{path or '(root)'}: {error['msg']}")
    return lines


def check_candidate(data: Any, provider: Provider) -> CandidateCheck:
    """Schema + semantic validation of a candidate IR document.

    The provider chosen by the user always wins over the candidate's.
    Warnings do not fail the check; errors do.
    """
    if not isinstance(data, dict):
        return CandidateCheck(None, None, ["the output must be a JSON object (the IR document)"])
    data = {**data, "provider": provider.value}

    try:
        spec = InfrastructureSpecification.model_validate(data)
    except ValidationError as exc:
        return CandidateCheck(None, None, format_schema_errors(exc))

    # Same limit the API puts on specs it receives.
    if len(spec.resources) > settings.api_max_resources:
        return CandidateCheck(
            None, None, [f"too many resources ({len(spec.resources)}); the limit is "
                         f"{settings.api_max_resources}"]
        )

    validation = validate(spec)
    if not validation.is_valid:
        return CandidateCheck(
            None, validation, [f"{i.code}: {i.message}" for i in validation.errors]
        )
    return CandidateCheck(spec, validation, [])

"""Result types of semantic validation (docs/IR_SCHEMA.md §5)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[EW]\d{2}$")
    severity: Severity
    message: str
    resource_name: str | None = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        # Warnings never block the pipeline; only errors do.
        return not self.errors

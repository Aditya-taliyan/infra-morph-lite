"""Response of POST /generate."""

from pydantic import BaseModel

from app.domain.terraform import CommandResult, CommandStatus, PlanSummary
from app.models.specifications import ValidationResponse
from app.services.generation import GenerationResult


class StepResult(BaseModel):
    step: str  # e.g. "terraform init"
    status: CommandStatus
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float

    @classmethod
    def from_command(cls, result: CommandResult) -> "StepResult":
        return cls(step=result.step, **result.model_dump())


class GenerateResponse(BaseModel):
    run_id: str
    validation: ValidationResponse
    # filename -> content
    files: dict[str, str]
    # Steps in the order they ran; the pipeline stops after the first failure.
    steps: list[StepResult]
    # False when a step failed or timed out; that step is the last in `steps`.
    terraform_ok: bool
    plan_summary: PlanSummary | None

    @classmethod
    def from_result(cls, result: GenerationResult) -> "GenerateResponse":
        return cls(
            run_id=result.run_id,
            validation=ValidationResponse.from_result(result.validation),
            files=result.files,
            steps=[StepResult.from_command(s) for s in result.steps],
            terraform_ok=result.terraform_ok,
            plan_summary=result.plan_summary,
        )

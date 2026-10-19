import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.enums import Environment, Provider, Region
from app.domain.resources import Subnet, VirtualNetwork
from app.core.config import GENERATED_DIR
from app.domain.spec import InfrastructureSpecification
from app.domain.terraform import CommandResult, CommandStatus, PlanSummary
from app.domain.validation import ValidationResult
from app.generators.terraform_generator import TerraformGenerator
from app.infrastructure.terraform_runner import TerraformRunner
from app.infrastructure.workspace import new_run_dir
from app.providers.registry import get_provider
from app.validators.semantic import validate

# Terraform runs share one data dir (see terraform_runner), so only one
# pipeline may run at a time in this process. A second caller is refused
# instead of queued, so an API client gets an immediate answer.
_PIPELINE_LOCK = threading.Lock()


class PipelineBusyError(RuntimeError):
    """Another run_pipeline call is still in progress."""


def build_minimal_spec(name: str) -> InfrastructureSpecification:
    """Smallest valid spec for a project name: one AWS network, one subnet."""
    return InfrastructureSpecification(
        schema_version="1",
        name=name,
        provider=Provider.AWS,
        region=Region.US_EAST_1,
        environment=Environment.DEV,
        resources=[
            VirtualNetwork(
                kind="VirtualNetwork",
                name="main-vpc",
                cidr="10.0.0.0/16",
                subnets=[Subnet(name="public-a", cidr="10.0.1.0/24", public=True)],
            )
        ],
    )


@dataclass
class GenerationResult:
    validation: ValidationResult
    # filename -> content; empty when validation found errors.
    files: dict[str, str] = field(default_factory=dict)
    # Set by run_pipeline only.
    run_id: str | None = None
    output_dir: Path | None = None
    # Terraform steps in the order they ran; stops after the first failure.
    steps: list[CommandResult] = field(default_factory=list)
    plan_summary: PlanSummary | None = None

    @property
    def terraform_ok(self) -> bool:
        """No step failed or timed out (skipped steps are not failures)."""
        return all(
            s.status in (CommandStatus.PASSED, CommandStatus.SKIPPED) for s in self.steps
        )


class InfrastructureGenerationService:
    """Semantic validation -> provider adapter -> Terraform generator.

    run_pipeline continues with terraform init -> fmt check -> validate -> plan.
    """

    def __init__(
        self, runner: TerraformRunner | None = None, generated_root: Path = GENERATED_DIR
    ) -> None:
        self.runner = runner or TerraformRunner()
        self.generated_root = generated_root

    def generate(
        self, spec: InfrastructureSpecification, output_dir: Path | None = None
    ) -> GenerationResult:
        validation = validate(spec)
        if not validation.is_valid:
            return GenerationResult(validation=validation)

        adapter = get_provider(spec.provider)
        context = adapter.build_context(spec)
        generator = TerraformGenerator(adapter.template_dir)
        files = generator.render(context)

        if output_dir is not None:
            generator.write(files, output_dir)

        return GenerationResult(validation=validation, files=files)

    def run_pipeline(
        self, spec: InfrastructureSpecification, generated_root: Path | None = None
    ) -> GenerationResult:
        """Generate into a fresh run directory, then check it with Terraform.

        Raises UnsupportedProviderError (before any validation or file is
        written) and PipelineBusyError (if another run is in progress).
        """
        get_provider(spec.provider)
        if not _PIPELINE_LOCK.acquire(blocking=False):
            raise PipelineBusyError("another generation is in progress")
        try:
            return self._run_pipeline(spec, generated_root or self.generated_root)
        finally:
            _PIPELINE_LOCK.release()

    def _run_pipeline(
        self, spec: InfrastructureSpecification, generated_root: Path
    ) -> GenerationResult:
        validation = validate(spec)
        if not validation.is_valid:
            # No run directory for a spec that never reaches generation.
            return GenerationResult(validation=validation)

        run_id, run_dir = new_run_dir(generated_root)
        result = self.generate(spec, output_dir=run_dir)
        result.run_id = run_id
        result.output_dir = run_dir

        for check in (self.runner.init, self.runner.fmt_check, self.runner.validate):
            step = check(run_dir)
            result.steps.append(step)
            if not result.terraform_ok:
                return result

        plan_steps, result.plan_summary = self.runner.plan(run_dir)
        result.steps.extend(plan_steps)
        return result

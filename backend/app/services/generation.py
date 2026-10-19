from dataclasses import dataclass, field
from pathlib import Path

from app.domain.enums import Environment, Provider, Region
from app.domain.resources import Subnet, VirtualNetwork
from app.domain.spec import InfrastructureSpecification
from app.domain.validation import ValidationResult
from app.generators.terraform_generator import TerraformGenerator
from app.providers.registry import get_provider
from app.validators.semantic import validate


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


class InfrastructureGenerationService:
    """Semantic validation -> provider adapter -> Terraform generator."""

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

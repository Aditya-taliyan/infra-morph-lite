from pathlib import Path

from app.domain.enums import Environment, Provider, Region
from app.domain.resources import Subnet, VirtualNetwork
from app.domain.spec import InfrastructureSpecification
from app.generators.terraform.basic import BasicTerraformGenerator
from app.services.output import TerraformOutputService


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


class InfrastructureGenerationService:
    def __init__(self) -> None:
        self.generator = BasicTerraformGenerator()
        self.output_service = TerraformOutputService()

    def generate(self, name: str, output_path: Path | None = None) -> str:
        spec = build_minimal_spec(name)
        content = self.generator.generate(spec)

        if output_path is not None:
            self.output_service.write(content, output_path)

        return content

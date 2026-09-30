from pathlib import Path

from app.generators.terraform.basic import BasicTerraformGenerator
from app.models.infrastructure import InfrastructureSpec
from app.services.output import TerraformOutputService


class InfrastructureGenerationService:
    def __init__(self) -> None:
        self.generator = BasicTerraformGenerator()
        self.output_service = TerraformOutputService()

    def generate(self, name: str, output_path: Path | None = None) -> str:
        spec = InfrastructureSpec(name=name)
        content = self.generator.generate(spec)

        if output_path is not None:
            self.output_service.write(content, output_path)

        return content
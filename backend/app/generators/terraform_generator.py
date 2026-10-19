"""Render a RenderContext to Terraform files with Jinja2.

Deterministic: the same context always gives byte-identical files. There are
no timestamps or random IDs, files come out in a fixed order, and everything
inside them follows the order of the context.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.generators import hcl
from app.providers.base import RenderContext

TEMPLATES_DIR = Path(__file__).parent / "templates"
FILES = ("providers.tf", "main.tf", "variables.tf", "outputs.tf")


class TerraformGenerator:
    def __init__(self, template_dir: str) -> None:
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR / template_dir),
            undefined=StrictUndefined,  # a missing value fails loudly
            autoescape=False,  # HCL, not HTML; strings are escaped by hcl.quote
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self.env.filters["quote"] = hcl.quote
        self.env.filters["hcl_value"] = hcl.value
        self.env.filters["hcl_body"] = hcl.body

    def render(self, context: RenderContext) -> dict[str, str]:
        variables = {
            "provider": context.provider,
            "locals": context.locals,
            "variables": context.variables,
            "resources": context.resources,
            "outputs": context.outputs,
        }
        return {
            name: self.env.get_template(f"{name}.j2").render(variables) for name in FILES
        }

    @staticmethod
    def write(files: dict[str, str], directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        written = []
        for name, content in files.items():
            path = directory / name
            # newline="\n": identical bytes on Windows and Linux.
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(path)
        return written

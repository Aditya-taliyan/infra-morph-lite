"""Provider adapter interface and the render context it produces.

An adapter maps a validated IR to a RenderContext: plain data describing the
Terraform to write. The mapping is one-to-many (one IR resource becomes several
Terraform resources). The context holds no HCL text and knows nothing about
Jinja2; turning it into files is the generator's job.
"""

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.domain.spec import InfrastructureSpecification


@dataclass(frozen=True)
class Expression:
    """A raw HCL expression, written unquoted (e.g. aws_vpc.main_vpc.id).

    Every plain str in a context is rendered as a quoted, escaped string, so
    only adapter code can produce an Expression, never user text.
    """

    text: str


@dataclass(frozen=True)
class Block:
    """A nested block inside a resource, e.g. route { ... }."""

    type: str
    attributes: dict[str, Any]


@dataclass(frozen=True)
class TerraformResource:
    type: str
    label: str
    attributes: dict[str, Any]

    @property
    def address(self) -> str:
        return f"{self.type}.{self.label}"


@dataclass(frozen=True)
class Variable:
    name: str
    type: str
    description: str
    default: Any = None


@dataclass(frozen=True)
class Local:
    name: str
    value: Any
    comment: str | None = None


@dataclass(frozen=True)
class Output:
    name: str
    value: Expression
    description: str


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    source: str
    version: str
    settings: dict[str, Any]


@dataclass(frozen=True)
class RenderContext:
    provider: ProviderConfig
    locals: list[Local] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    resources: list[TerraformResource] = field(default_factory=list)
    outputs: list[Output] = field(default_factory=list)


def terraform_label(*names: str) -> str:
    """IR names to a Terraform label: web-server -> web_server."""
    return "_".join(name.replace("-", "_") for name in names)


class ProviderAdapter(ABC):
    #: Template directory under generators/templates/ for this provider.
    template_dir: str

    @abstractmethod
    def build_context(self, spec: InfrastructureSpecification) -> RenderContext:
        """Map a semantically valid spec to Terraform resources."""
        raise NotImplementedError

    @staticmethod
    def check_unique_addresses(context: RenderContext) -> None:
        # Labels join IR names with "_", so network "a-b" + subnet "c" and
        # network "a" + subnet "b-c" would both become "a_b_c".
        counts = Counter(r.address for r in context.resources)
        counts.update(f"output.{o.name}" for o in context.outputs)
        duplicates = sorted(address for address, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(
                "Names in the spec map to the same Terraform address: "
                f"{', '.join(duplicates)}. Rename one of the resources."
            )

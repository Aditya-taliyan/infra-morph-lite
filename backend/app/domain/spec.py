"""Top-level IR document (docs/IR_SCHEMA.md §1)."""

from typing import Annotated, Literal

from pydantic import Field

from app.domain.enums import Environment, Provider, Region
from app.domain.resources import IRModel, Resource

ProjectName = Annotated[str, Field(pattern=r"^[a-z0-9-]{3,32}$")]


class InfrastructureSpecification(IRModel):
    schema_version: Literal["1"]
    name: ProjectName
    provider: Provider
    region: Region
    environment: Environment
    tags: dict[str, str] = Field(default_factory=dict, max_length=20)
    resources: list[Resource] = Field(min_length=1)

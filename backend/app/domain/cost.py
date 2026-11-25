"""Cost estimate of a generated run (docs/PROJECT_SPEC.md §17)."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

COST_UNAVAILABLE = "Cost estimation unavailable."


class CostStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ResourceCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Terraform address; "<type> (<count>)" where Infracost reports only counts.
    address: str
    # None = no price known. Never 0 as a stand-in.
    monthly_cost: Decimal | None
    note: str = ""


class CostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CostStatus
    currency: str | None = None
    # None when unavailable, or when nothing in the run has a price.
    monthly_total: Decimal | None = None
    resources: list[ResourceCost] = Field(default_factory=list)
    unsupported_count: int = 0
    message: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> "CostEstimate":
        return cls(status=CostStatus.UNAVAILABLE, message=f"{COST_UNAVAILABLE} {reason}")

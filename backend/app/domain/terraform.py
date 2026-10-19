"""Result types of running Terraform (docs/PROJECT_SPEC.md §13)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CommandStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # terraform is not installed


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    # None when the process never finished (timeout) or never started (skipped).
    exit_code: int | None
    stdout: str = ""
    # For timeout and skipped, holds the explanation shown to the user.
    stderr: str = ""
    duration_seconds: float
    status: CommandStatus

    @property
    def step(self) -> str:
        """Readable step name, e.g. "terraform init"."""
        return " ".join(self.command[:2])


class PlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_add: int = 0
    to_change: int = 0
    to_destroy: int = 0
    # Addresses of resources the plan would act on, in plan order.
    resource_addresses: list[str] = Field(default_factory=list)

    @classmethod
    def from_show_json(cls, document: dict[str, Any]) -> "PlanSummary":
        """Count actions the way Terraform's "Plan: X to add..." line does.

        A replace is ["delete", "create"] (or the reverse) and counts as one
        add and one destroy. "no-op" and "read" change nothing.
        """
        summary = cls()
        for change in document.get("resource_changes", []):
            actions = change["change"]["actions"]
            if "create" in actions:
                summary.to_add += 1
            if "delete" in actions:
                summary.to_destroy += 1
            if "update" in actions:
                summary.to_change += 1
            if {"create", "delete", "update"} & set(actions):
                summary.resource_addresses.append(change["address"])
        return summary

    def line(self) -> str:
        return (
            f"Plan: {self.to_add} to add, {self.to_change} to change, "
            f"{self.to_destroy} to destroy."
        )

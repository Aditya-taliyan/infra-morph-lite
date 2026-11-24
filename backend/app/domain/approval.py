"""Stored human approval of a checked run (docs/PROJECT_SPEC.md §16).

The record proves what was approved: both hashes are computed by the
server from the run directory, never taken from the client.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, field_validator

from app.domain.enums import Provider
from app.domain.terraform import PlanSummary

ApproverName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ApprovalComment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    run_id: str
    approved_by: ApproverName
    comment: ApprovalComment | None = None
    approved_at: AwareDatetime  # always UTC
    provider: Provider
    # None for Azure: its plan is skipped by design.
    plan_summary: PlanSummary | None
    spec_hash: str
    files_hash: str

    @field_validator("approved_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def spec_hash(spec: dict[str, Any]) -> str:
    """sha256 of the canonical JSON: sorted keys, no whitespace."""
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def files_hash(files: dict[str, bytes]) -> str:
    """sha256 over name + content of each file in sorted name order.

    The name is included so renaming a file also changes the hash.
    """
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name] + b"\0")
    return digest.hexdigest()

"""Human approval of a checked run. Records a decision; never deploys.

`terraform apply` is out of scope by design: the system stops at an
approved, planned artifact.
"""

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import GENERATED_DIR
from app.domain.approval import ApprovalRecord, files_hash, spec_hash
from app.domain.enums import Provider
from app.domain.terraform import CommandStatus, PlanSummary
from app.infrastructure.approval_store import ApprovalStore
from app.infrastructure.workspace import find_run_dir, read_run_record, read_tf_files
from app.providers.registry import get_provider

# The "already approved?" check and the save must not interleave.
_APPROVAL_LOCK = threading.Lock()


class RunNotFoundError(LookupError):
    """No checked run with this id."""


class AlreadyApprovedError(RuntimeError):
    def __init__(self, existing: ApprovalRecord) -> None:
        super().__init__(f"run {existing.run_id} is already approved")
        self.existing = existing


class RunNotApprovableError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ApprovalService:
    def __init__(
        self, store: ApprovalStore | None = None, generated_root: Path = GENERATED_DIR
    ) -> None:
        self.store = store or ApprovalStore()
        self.generated_root = generated_root

    def approve(self, run_id: str, approved_by: str, comment: str | None = None) -> ApprovalRecord:
        run_dir = find_run_dir(self.generated_root, run_id)
        if run_dir is None:
            raise RunNotFoundError(f"no generated run with id {run_id!r}")
        run = read_run_record(run_dir)
        provider = Provider(run["provider"])
        _check_steps(provider, {s["step"]: s["status"] for s in run["steps"]})

        current_files_hash = files_hash(read_tf_files(run_dir))
        if current_files_hash != run["files_hash"]:
            raise RunNotApprovableError(
                "run_modified",
                "The Terraform files changed after they were validated; generate again.",
            )

        with _APPROVAL_LOCK:
            existing = next((r for r in self.store.list() if r.run_id == run_id), None)
            if existing is not None:
                raise AlreadyApprovedError(existing)
            record = ApprovalRecord(
                approval_id=uuid.uuid4().hex,
                run_id=run_id,
                approved_by=approved_by,
                comment=(comment or "").strip() or None,
                approved_at=datetime.now(UTC),
                provider=provider,
                plan_summary=PlanSummary(**run["plan_summary"]) if run["plan_summary"] else None,
                spec_hash=spec_hash(run["spec"]),
                files_hash=current_files_hash,
            )
            self.store.save(record)
        return record

    def list(self) -> list[ApprovalRecord]:
        return self.store.list()

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self.store.get(approval_id)


def _check_steps(provider: Provider, statuses: dict[str, str]) -> None:
    # "skipped" validate means terraform was not installed: nothing was checked.
    if statuses.get("terraform validate") != CommandStatus.PASSED:
        raise RunNotApprovableError(
            "validation_not_passed", "terraform validate did not pass for this run."
        )
    plan = statuses.get("terraform plan")
    if get_provider(provider).offline_plan:
        if plan != CommandStatus.PASSED:
            raise RunNotApprovableError(
                "plan_not_passed", "terraform plan did not pass for this run."
            )
    elif plan != CommandStatus.SKIPPED:
        raise RunNotApprovableError(
            "plan_not_passed", "terraform plan did not finish as expected for this run."
        )

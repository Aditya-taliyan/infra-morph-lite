"""Approval records as JSON files: backend/approvals/<approval_id>.json. No database."""

import re
from pathlib import Path

from app.core.config import APPROVALS_DIR
from app.domain.approval import ApprovalRecord

# uuid4().hex. Checked before any path is built from a client-sent id.
APPROVAL_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class ApprovalStore:
    def __init__(self, root: Path = APPROVALS_DIR) -> None:
        self.root = root

    def save(self, record: ApprovalRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # Mode "x": never overwrite an existing record.
        with (self.root / f"{record.approval_id}.json").open("x", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))

    def get(self, approval_id: str) -> ApprovalRecord | None:
        if not APPROVAL_ID_PATTERN.fullmatch(approval_id):
            return None
        path = self.root / f"{approval_id}.json"
        if not path.is_file():
            return None
        return ApprovalRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[ApprovalRecord]:
        """Newest first."""
        if not self.root.is_dir():
            return []
        # shortcut: reads every file per call, fine for a demo; index if there are thousands.
        records = [
            ApprovalRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in self.root.glob("*.json")
        ]
        return sorted(records, key=lambda r: r.approved_at, reverse=True)

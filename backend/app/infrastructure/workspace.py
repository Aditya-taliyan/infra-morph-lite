"""Isolated per-run directories under backend/generated/."""

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Saved next to the .tf files; Terraform ignores .json files.
RUN_RECORD = "run.json"
RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}")


def new_run_id() -> str:
    """UTC timestamp plus a short random suffix, e.g. 20261019T143005Z-a1b2c3."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def new_run_dir(root: Path) -> tuple[str, Path]:
    run_id = new_run_id()
    path = root / run_id
    # exist_ok=False: two runs never share a directory.
    path.mkdir(parents=True, exist_ok=False)
    return run_id, path


def find_run_dir(root: Path, run_id: str) -> Path | None:
    """The run's directory if it exists. The id format is checked first, so
    a client-sent id like "../x" never becomes a path."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    path = root / run_id
    return path if (path / RUN_RECORD).is_file() else None


def read_tf_files(run_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in run_dir.glob("*.tf")}


def write_run_record(run_dir: Path, record: dict[str, Any]) -> None:
    (run_dir / RUN_RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")


def read_run_record(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / RUN_RECORD).read_text(encoding="utf-8"))

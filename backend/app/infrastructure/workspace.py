"""Isolated per-run directories under backend/generated/."""

import secrets
from datetime import UTC, datetime
from pathlib import Path


def new_run_id() -> str:
    """UTC timestamp plus a short random suffix, e.g. 20261019T143005Z-a1b2c3."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def new_run_dir(root: Path) -> tuple[str, Path]:
    run_id = new_run_id()
    path = root / run_id
    # exist_ok=False: two runs never share a directory.
    path.mkdir(parents=True, exist_ok=False)
    return run_id, path

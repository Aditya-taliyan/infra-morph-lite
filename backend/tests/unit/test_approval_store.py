from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError

from app.domain.approval import ApprovalRecord, files_hash, spec_hash
from app.domain.terraform import PlanSummary
from app.infrastructure.approval_store import ApprovalStore


def record(run_id: str = "20261124T100000Z-a1b2c3", minutes: int = 0) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=uuid.uuid4().hex,
        run_id=run_id,
        approved_by="Aditya",
        comment="looks fine",
        approved_at=datetime(2026, 11, 24, 10, tzinfo=UTC) + timedelta(minutes=minutes),
        provider="aws",
        plan_summary=PlanSummary(to_add=11),
        spec_hash="a" * 64,
        files_hash="b" * 64,
    )


def test_round_trip(tmp_path: Path):
    store = ApprovalStore(tmp_path)
    saved = record()

    store.save(saved)

    assert (tmp_path / f"{saved.approval_id}.json").is_file()
    assert store.get(saved.approval_id) == saved


def test_list_is_newest_first(tmp_path: Path):
    store = ApprovalStore(tmp_path)
    old, new = record(minutes=0), record(minutes=5)
    store.save(old)
    store.save(new)

    assert [r.approval_id for r in store.list()] == [new.approval_id, old.approval_id]


def test_empty_store_and_unknown_or_malformed_ids(tmp_path: Path):
    store = ApprovalStore(tmp_path / "missing")

    assert store.list() == []
    assert store.get(uuid.uuid4().hex) is None
    assert store.get("../../etc/passwd") is None


def test_save_never_overwrites(tmp_path: Path):
    store = ApprovalStore(tmp_path)
    saved = record()
    store.save(saved)

    with pytest.raises(FileExistsError):
        store.save(saved)


def test_record_rejects_extra_fields_and_long_name():
    data = record().model_dump()
    with pytest.raises(ValidationError):
        ApprovalRecord(**data, deployed=True)
    with pytest.raises(ValidationError):
        ApprovalRecord(**{**data, "approved_by": "x" * 81})


def test_approved_at_is_stored_as_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    r = ApprovalRecord(**{**record().model_dump(), "approved_at": datetime(2026, 11, 24, 15, 30, tzinfo=ist)})
    assert r.approved_at == datetime(2026, 11, 24, 10, tzinfo=UTC)
    assert r.approved_at.utcoffset() == timedelta(0)


def test_hashes_are_canonical_and_cover_names():
    assert spec_hash({"a": 1, "b": 2}) == spec_hash({"b": 2, "a": 1})
    assert files_hash({"a.tf": b"x", "b.tf": b"y"}) == files_hash({"b.tf": b"y", "a.tf": b"x"})
    assert files_hash({"a.tf": b"x"}) != files_hash({"c.tf": b"x"})

from pathlib import Path

import pytest

from app.domain.approval import files_hash, spec_hash
from app.infrastructure.approval_store import ApprovalStore
from app.infrastructure.workspace import read_run_record, read_tf_files
from app.services.approval import (
    AlreadyApprovedError,
    ApprovalService,
    RunNotApprovableError,
    RunNotFoundError,
)
from app.services.generation import InfrastructureGenerationService
from tests.api_helpers import FakeRunner
from tests.helpers import load_spec


def checked_run(tmp_path: Path, spec: str = "demo2", fail: str | None = None) -> str:
    result = InfrastructureGenerationService(
        FakeRunner(fail=fail), generated_root=tmp_path / "generated"
    ).run_pipeline(load_spec(spec))
    return result.run_id


def service(tmp_path: Path) -> ApprovalService:
    return ApprovalService(ApprovalStore(tmp_path / "approvals"), tmp_path / "generated")


def test_aws_run_with_passing_plan_is_approved_and_stored(tmp_path: Path):
    run_id = checked_run(tmp_path)

    record = service(tmp_path).approve(run_id, "  Aditya  ", "ok")

    assert record.run_id == run_id
    assert record.approved_by == "Aditya"
    assert record.provider == "aws"
    assert record.plan_summary.to_add == 11
    run = read_run_record(tmp_path / "generated" / run_id)
    assert record.spec_hash == spec_hash(run["spec"])
    assert record.files_hash == run["files_hash"]
    assert service(tmp_path).get(record.approval_id) == record


def test_refused_when_validate_failed(tmp_path: Path):
    run_id = checked_run(tmp_path, fail="validate")

    with pytest.raises(RunNotApprovableError) as exc:
        service(tmp_path).approve(run_id, "Aditya")
    assert exc.value.code == "validation_not_passed"


def test_refused_when_aws_plan_failed(tmp_path: Path):
    run_id = checked_run(tmp_path, fail="plan")

    with pytest.raises(RunNotApprovableError) as exc:
        service(tmp_path).approve(run_id, "Aditya")
    assert exc.value.code == "plan_not_passed"


def test_azure_allowed_with_plan_skipped(tmp_path: Path):
    run_id = checked_run(tmp_path, spec="demo4_azure")

    record = service(tmp_path).approve(run_id, "Aditya")

    assert record.provider == "azure"
    assert record.plan_summary is None


def test_reapproval_is_refused(tmp_path: Path):
    run_id = checked_run(tmp_path)
    first = service(tmp_path).approve(run_id, "Aditya")

    with pytest.raises(AlreadyApprovedError) as exc:
        service(tmp_path).approve(run_id, "Someone else")
    assert exc.value.existing.approval_id == first.approval_id
    assert len(service(tmp_path).list()) == 1


@pytest.mark.parametrize("run_id", ["20261124T100000Z-000000", "../generated", "x"])
def test_unknown_run(tmp_path: Path, run_id: str):
    with pytest.raises(RunNotFoundError):
        service(tmp_path).approve(run_id, "Aditya")


def test_files_hash_changes_when_a_tf_file_changes(tmp_path: Path):
    run_id = checked_run(tmp_path)
    run_dir = tmp_path / "generated" / run_id
    before = files_hash(read_tf_files(run_dir))
    assert before == read_run_record(run_dir)["files_hash"]

    main_tf = run_dir / "main.tf"
    main_tf.write_text(main_tf.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    assert files_hash(read_tf_files(run_dir)) != before
    # The edited run no longer matches what was validated.
    with pytest.raises(RunNotApprovableError) as exc:
        service(tmp_path).approve(run_id, "Aditya")
    assert exc.value.code == "run_modified"

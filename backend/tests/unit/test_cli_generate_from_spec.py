import json
from pathlib import Path

import pytest

from app.cli.main import main
from app.domain.terraform import CommandStatus
from tests.helpers import FIXTURES
from tests.unit.test_generation_pipeline import FakeRunner

DEMO2 = FIXTURES / "specs" / "demo2.json"


@pytest.fixture
def fake_pipeline(monkeypatch, tmp_path):
    """Route the CLI's pipeline to a fake runner and a temporary generated/ dir."""
    from app.services import generation

    runner = FakeRunner()
    original = generation.InfrastructureGenerationService.run_pipeline

    def run_pipeline(self, spec, generated_root=None):
        self.runner = runner
        return original(self, spec, generated_root=tmp_path)

    monkeypatch.setattr(generation.InfrastructureGenerationService, "run_pipeline", run_pipeline)
    return runner


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr("sys.argv", ["infra-morph", "generate-from-spec", *argv])
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


def test_success_prints_warnings_steps_and_plan(monkeypatch, capsys, fake_pipeline):
    assert _run(monkeypatch, str(DEMO2)) == 0

    captured = capsys.readouterr()
    assert "WARNING W01" in captured.err
    assert "PASSED   terraform init (0.1s)" in captured.out
    assert "PASSED   terraform plan" in captured.out
    assert "Plan: 11 to add, 0 to change, 0 to destroy." in captured.out


def test_failed_step_exits_1(monkeypatch, capsys, fake_pipeline):
    fake_pipeline.fail = "validate"

    assert _run(monkeypatch, str(DEMO2)) == 1
    assert "FAILED   terraform validate" in capsys.readouterr().out


def test_skipped_steps_exit_0(monkeypatch, capsys, fake_pipeline):
    fake_pipeline.status = CommandStatus.SKIPPED

    assert _run(monkeypatch, str(DEMO2)) == 0
    assert "SKIPPED  terraform init" in capsys.readouterr().out


def test_missing_file_exits_1(monkeypatch, capsys, tmp_path: Path):
    assert _run(monkeypatch, str(tmp_path / "nope.json")) == 1
    assert "cannot read" in capsys.readouterr().err


def test_invalid_json_exits_1(monkeypatch, capsys, tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    assert _run(monkeypatch, str(bad)) == 1
    assert "is not valid JSON" in capsys.readouterr().err


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_powershell_redirect_encodings_load(
    monkeypatch, tmp_path: Path, fake_pipeline, encoding
):
    # PowerShell's ">" writes UTF-8 with BOM (7.x settings) or UTF-16 (5.1 default).
    spec = tmp_path / "spec.json"
    spec.write_text(DEMO2.read_text(encoding="utf-8"), encoding=encoding)

    assert _run(monkeypatch, str(spec)) == 0


def test_schema_errors_are_readable(monkeypatch, capsys, tmp_path: Path):
    data = json.loads(DEMO2.read_text(encoding="utf-8"))
    data["resources"][2]["size"] = "enormous"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")

    assert _run(monkeypatch, str(bad)) == 1
    err = capsys.readouterr().err
    assert "does not match the IR schema" in err
    assert "resources.2" in err and "size" in err


def test_semantic_errors_exit_1(monkeypatch, capsys, tmp_path: Path, fake_pipeline):
    data = json.loads(DEMO2.read_text(encoding="utf-8"))
    data["resources"][2]["security_groups"] = ["missing-sg"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")

    assert _run(monkeypatch, str(bad)) == 1
    assert "ERROR E02" in capsys.readouterr().err
    assert fake_pipeline.calls == []

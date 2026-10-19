from pathlib import Path

import pytest

from app.generators.terraform_generator import FILES
from app.services.generation import InfrastructureGenerationService, build_minimal_spec
from tests.helpers import load_spec, resource


def test_valid_spec_generates_files_and_keeps_warnings():
    result = InfrastructureGenerationService().generate(load_spec("demo2"))
    assert result.validation.is_valid
    assert [i.code for i in result.validation.warnings] == ["W01"]
    assert tuple(result.files) == FILES
    assert 'resource "aws_instance" "web_server"' in result.files["main.tf"]


def test_output_dir_receives_files(tmp_path: Path):
    result = InfrastructureGenerationService().generate(load_spec("demo5"), output_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(FILES)
    assert (tmp_path / "main.tf").read_text(encoding="utf-8") == result.files["main.tf"]


def test_minimal_spec_generates():
    result = InfrastructureGenerationService().generate(build_minimal_spec("demo"))
    assert 'resource "aws_vpc" "main_vpc"' in result.files["main.tf"]


def _forbid(*_args, **_kwargs):
    raise AssertionError("an invalid spec reached provider mapping or generation")


def test_invalid_spec_never_reaches_adapter_or_generator(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("app.services.generation.get_provider", _forbid)
    monkeypatch.setattr("app.services.generation.TerraformGenerator", _forbid)
    # E02: the instance points at a security group that does not exist.
    spec = load_spec(
        "demo2", lambda d: resource(d, "web-server").update(security_groups=["missing-sg"])
    )

    result = InfrastructureGenerationService().generate(spec, output_dir=tmp_path / "out")

    assert not result.validation.is_valid
    assert "E02" in [i.code for i in result.validation.errors]
    assert result.files == {}
    assert not (tmp_path / "out").exists()


def test_azure_spec_reports_not_implemented():
    spec = load_spec("demo5", lambda d: d.update(provider="azure", region="centralindia"))
    with pytest.raises(NotImplementedError, match="Milestone 2"):
        InfrastructureGenerationService().generate(spec)

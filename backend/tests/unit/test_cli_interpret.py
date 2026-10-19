import json

import pytest

from app.cli.main import main
from tests.ollama_helpers import REFUSED, FakeOllama, demo_json
from tests.unit.test_cli_generate_from_spec import fake_pipeline  # noqa: F401 (fixture)

DEMO2_TEXT = "Create an AWS VPC with a public subnet and a web server"


@pytest.fixture
def use_ollama(monkeypatch):
    def install(*replies) -> FakeOllama:
        fake = FakeOllama(*replies)
        monkeypatch.setattr("app.cli.main.SpecEngine", fake.engine)
        return fake

    return install


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr("sys.argv", ["infra-morph", "interpret", *argv])
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


def test_prints_spec_json_and_interpreter(monkeypatch, capsys, use_ollama):
    use_ollama(REFUSED)

    assert _run(monkeypatch, DEMO2_TEXT, "--provider", "aws") == 0

    captured = capsys.readouterr()
    # stdout is only the spec, so it can be redirected to a file.
    spec = json.loads(captured.out)
    assert spec["provider"] == "aws"
    assert "Interpreter: rule-based (1 attempt)" in captured.err
    assert "Fallback: ollama is unavailable" in captured.err


def test_ollama_spec_shows_warnings(monkeypatch, capsys, use_ollama):
    use_ollama(demo_json("demo2"))

    assert _run(monkeypatch, DEMO2_TEXT) == 0

    captured = capsys.readouterr()
    assert "Interpreter: ollama (1 attempt)" in captured.err
    assert "WARNING W01" in captured.err
    assert json.loads(captured.out)["name"] == "vpc-web"


def test_unknown_request_exits_1(monkeypatch, capsys, use_ollama):
    use_ollama(REFUSED)

    assert _run(monkeypatch, "make me a sandwich") == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No valid specification" in captured.err


def test_generate_runs_the_pipeline(monkeypatch, capsys, use_ollama, fake_pipeline):  # noqa: F811
    use_ollama(REFUSED)

    assert _run(monkeypatch, DEMO2_TEXT, "--generate") == 0

    out = capsys.readouterr().out
    assert "PASSED   terraform plan" in out
    assert "Plan: 11 to add" in out


def test_generate_azure_reports_unsupported_provider(monkeypatch, capsys, use_ollama):
    use_ollama(REFUSED)

    code = _run(monkeypatch, "Create an Azure virtual network with a virtual machine",
                "--provider", "azure", "--generate")

    assert code == 1
    assert "Azure Terraform generation is not available yet" in capsys.readouterr().err

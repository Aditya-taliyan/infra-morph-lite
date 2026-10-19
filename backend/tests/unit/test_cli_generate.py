import pytest

from app.cli.main import main


def test_generate_command_prints_all_files(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["infra-morph", "generate", "demo"])

    main()

    out = capsys.readouterr().out
    for name in ("providers.tf", "main.tf", "variables.tf", "outputs.tf"):
        assert f"# --- {name} ---\n" in out
    assert 'resource "aws_vpc" "main_vpc"' in out


def test_generate_command_requires_name(monkeypatch):
    monkeypatch.setattr("sys.argv", ["infra-morph", "generate"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_generate_command_rejects_invalid_name(monkeypatch):
    monkeypatch.setattr("sys.argv", ["infra-morph", "generate", "Bad Name"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2

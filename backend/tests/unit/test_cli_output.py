from pathlib import Path

from app.cli.main import main


def test_generate_command_writes_output_directory(tmp_path: Path, capsys, monkeypatch):
    output_dir = tmp_path / "run"

    monkeypatch.setattr(
        "sys.argv",
        ["infra-morph", "generate", "demo", "--output", str(output_dir)],
    )

    main()

    assert sorted(p.name for p in output_dir.iterdir()) == [
        "main.tf",
        "outputs.tf",
        "providers.tf",
        "variables.tf",
    ]
    assert f"Generated Terraform: {output_dir}" in capsys.readouterr().out

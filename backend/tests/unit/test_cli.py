from app.cli.main import main


def test_version_command(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["infra-morph", "version"])

    main()

    captured = capsys.readouterr()

    assert captured.out.strip() == "Infra Morph Lite 0.1.0"
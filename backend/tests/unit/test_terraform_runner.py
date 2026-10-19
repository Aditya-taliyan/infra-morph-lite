import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.domain.terraform import CommandStatus, PlanSummary
from app.infrastructure.terraform_runner import TerraformRunner
from tests.helpers import FIXTURES

FAKE_BINARY = "/fake/bin/terraform"
SHOW_JSON = (FIXTURES / "terraform" / "plan_show.json").read_text(encoding="utf-8")


@pytest.fixture
def runner(tmp_path: Path) -> TerraformRunner:
    config = Settings(
        _env_file=None, terraform_init_timeout_seconds=120, terraform_timeout_seconds=60
    )
    return TerraformRunner(config=config, plugin_cache_dir=tmp_path / "cache")


@pytest.fixture
def run_mock(monkeypatch) -> MagicMock:
    """subprocess.run that succeeds; terraform is "installed"."""
    monkeypatch.setattr(
        "app.infrastructure.terraform_runner.shutil.which", lambda _name: FAKE_BINARY
    )
    mock = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
    )
    monkeypatch.setattr("app.infrastructure.terraform_runner.subprocess.run", mock)
    return mock


@pytest.mark.parametrize(
    ("method", "expected_args", "expected_timeout"),
    [
        ("init", ["init", "-backend=false", "-input=false", "-no-color"], 120),
        ("fmt_check", ["fmt", "-check", "-diff", "-no-color"], 60),
        ("validate", ["validate", "-no-color"], 60),
    ],
)
def test_commands_use_argument_lists(
    runner, run_mock, tmp_path, method, expected_args, expected_timeout
):
    result = getattr(runner, method)(tmp_path)

    args, kwargs = run_mock.call_args
    assert args[0] == [FAKE_BINARY, *expected_args]
    assert "-no-color" in args[0]
    assert "shell" not in kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == expected_timeout
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert result.status is CommandStatus.PASSED
    assert result.command == ["terraform", *expected_args]
    assert result.exit_code == 0 and result.stdout == "ok"


def test_plan_runs_plan_then_show_and_parses_summary(runner, run_mock, tmp_path):
    run_mock.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="planned", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout=SHOW_JSON, stderr=""),
    ]

    steps, summary = runner.plan(tmp_path)

    calls = [c.args[0] for c in run_mock.call_args_list]
    assert calls == [
        [FAKE_BINARY, "plan", "-input=false", "-no-color", "-out=plan.tfplan"],
        [FAKE_BINARY, "show", "-json", "-no-color", "plan.tfplan"],
    ]
    assert [s.status for s in steps] == [CommandStatus.PASSED, CommandStatus.PASSED]
    assert summary == PlanSummary.from_show_json(json.loads(SHOW_JSON))


def test_failed_plan_does_not_run_show(runner, run_mock, tmp_path):
    run_mock.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Error: bad config"
    )

    steps, summary = runner.plan(tmp_path)

    assert run_mock.call_count == 1
    assert steps[0].status is CommandStatus.FAILED
    assert steps[0].exit_code == 1 and steps[0].stderr == "Error: bad config"
    assert summary is None


def test_unparseable_show_output_fails_the_step(runner, run_mock, tmp_path):
    run_mock.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="not json", stderr=""
    )

    steps, summary = runner.plan(tmp_path)

    assert steps[-1].status is CommandStatus.FAILED
    assert "Could not parse" in steps[-1].stderr
    assert summary is None


def test_nonzero_exit_is_failed(runner, run_mock, tmp_path):
    # fmt -check exits 3 when a file is not formatted.
    run_mock.return_value = subprocess.CompletedProcess(
        args=[], returncode=3, stdout="main.tf\n", stderr=""
    )

    result = runner.fmt_check(tmp_path)

    assert result.status is CommandStatus.FAILED and result.exit_code == 3


def test_timeout_returns_timeout_status(runner, run_mock, tmp_path):
    run_mock.side_effect = subprocess.TimeoutExpired(cmd="terraform", timeout=120)

    result = runner.init(tmp_path)

    assert result.status is CommandStatus.TIMEOUT
    assert result.exit_code is None
    assert "Timed out after 120 seconds" in result.stderr


def test_missing_binary_is_skipped_without_running(runner, run_mock, monkeypatch, tmp_path):
    monkeypatch.setattr("app.infrastructure.terraform_runner.shutil.which", lambda _name: None)

    result = runner.validate(tmp_path)

    run_mock.assert_not_called()
    assert result.status is CommandStatus.SKIPPED
    assert result.exit_code is None
    assert "not installed" in result.stderr


def test_missing_binary_skips_plan(runner, run_mock, monkeypatch, tmp_path):
    monkeypatch.setattr("app.infrastructure.terraform_runner.shutil.which", lambda _name: None)

    steps, summary = runner.plan(tmp_path)

    run_mock.assert_not_called()
    assert [s.status for s in steps] == [CommandStatus.SKIPPED]
    assert summary is None


@pytest.mark.parametrize("subcommand", ["apply", "destroy", "import", "state", "taint", "console"])
def test_non_allowlisted_subcommands_raise_before_running(runner, run_mock, tmp_path, subcommand):
    with pytest.raises(ValueError, match="not allowed"):
        runner._run(subcommand, ["-auto-approve"], tmp_path, 60)

    run_mock.assert_not_called()


def test_cloud_credentials_never_reach_terraform(runner, run_mock, monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREALKEY")
    monkeypatch.setenv("AWS_PROFILE", "prod")
    monkeypatch.setenv("ARM_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("INFRAMORPH_KEEP_ME", "yes")

    runner.init(tmp_path)

    env = run_mock.call_args.kwargs["env"]
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_PROFILE" not in env
    assert not [k for k in env if k.upper().startswith(("AWS_", "ARM_", "AZURE_"))]
    assert env["INFRAMORPH_KEEP_ME"] == "yes"


def test_plugin_cache_dir_is_created_and_passed(runner, run_mock, tmp_path):
    runner.init(tmp_path)

    env = run_mock.call_args.kwargs["env"]
    assert env["TF_PLUGIN_CACHE_DIR"] == str(tmp_path / "cache")
    assert (tmp_path / "cache").is_dir()


def test_environment_is_not_in_the_result(runner, run_mock, monkeypatch, tmp_path):
    monkeypatch.setenv("INFRAMORPH_SECRET_MARKER", "do-not-leak")

    result = runner.init(tmp_path)

    assert "do-not-leak" not in result.model_dump_json()


def test_plan_summary_counts_like_terraform():
    summary = PlanSummary.from_show_json(json.loads(SHOW_JSON))

    # create x2 + replace -> 3 add; update -> 1 change; replace + delete -> 2 destroy.
    assert (summary.to_add, summary.to_change, summary.to_destroy) == (3, 1, 2)
    assert summary.resource_addresses == [
        "aws_vpc.main_vpc",
        "aws_subnet.main_vpc_public_a",
        "aws_security_group.web_sg",
        "aws_instance.web_server",
        "aws_internet_gateway.old",
    ]
    assert summary.line() == "Plan: 3 to add, 1 to change, 2 to destroy."


def test_plan_summary_of_empty_plan():
    assert PlanSummary.from_show_json({"format_version": "1.2"}) == PlanSummary()

import json
import logging
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.cost import CostEstimate, CostStatus
from app.infrastructure.infracost_runner import InfracostRunner, parse_breakdown
from tests.helpers import FIXTURES

FAKE_BINARY = "/fake/bin/infracost"
KEY = "ico-test-key-not-real"
SAMPLE = (FIXTURES / "infracost" / "breakdown.json").read_text(encoding="utf-8")


def completed(returncode=0, stdout=SAMPLE, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def runner() -> InfracostRunner:
    return InfracostRunner(
        Settings(_env_file=None, infracost_api_key=KEY, infracost_timeout_seconds=90)
    )


@pytest.fixture
def run_mock(monkeypatch) -> MagicMock:
    """subprocess.run that returns the sample; infracost is "installed"."""
    monkeypatch.setattr(
        "app.infrastructure.infracost_runner.shutil.which", lambda _name: FAKE_BINARY
    )
    mock = MagicMock(return_value=completed())
    monkeypatch.setattr("app.infrastructure.infracost_runner.subprocess.run", mock)
    return mock


def assert_unavailable(result: CostEstimate, reason: str) -> None:
    assert result.status is CostStatus.UNAVAILABLE
    assert result.message.startswith("Cost estimation unavailable. ")
    assert reason in result.message
    assert result.monthly_total is None and result.resources == []


def test_missing_binary(runner, run_mock, monkeypatch, tmp_path):
    monkeypatch.setattr("app.infrastructure.infracost_runner.shutil.which", lambda _name: None)
    assert_unavailable(runner.estimate(tmp_path), "not installed")
    run_mock.assert_not_called()


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key(run_mock, tmp_path, key):
    runner = InfracostRunner(Settings(_env_file=None, infracost_api_key=key))
    assert_unavailable(runner.estimate(tmp_path), "INFRACOST_API_KEY is not set")
    run_mock.assert_not_called()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (subprocess.TimeoutExpired("infracost", 90), "timed out after 90 seconds"),
        (OSError("boom"), "could not be started"),
    ],
)
def test_process_errors(runner, run_mock, tmp_path, error, reason):
    run_mock.side_effect = error
    assert_unavailable(runner.estimate(tmp_path), reason)


@pytest.mark.parametrize(
    "stdout",
    ["not json", "[]", "{}", "null", SAMPLE.replace('"8.392"', '"NaN"')],
)
def test_unreadable_output(runner, run_mock, tmp_path, stdout):
    run_mock.return_value = completed(stdout=stdout)
    assert_unavailable(runner.estimate(tmp_path), "could not be read")


def test_command_is_an_argument_list(runner, run_mock, tmp_path):
    runner.estimate(tmp_path)

    args, kwargs = run_mock.call_args
    assert args[0] == [
        FAKE_BINARY, "breakdown", "--path", str(tmp_path.resolve()), "--format", "json"
    ]
    assert "shell" not in kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 90


def test_relative_run_dir_becomes_absolute(runner, run_mock):
    runner.estimate(Path("some-run"))
    assert Path(run_mock.call_args.args[0][3]).is_absolute()


@pytest.mark.parametrize(
    "command", [("infracost", "upload"), ("infracost", "auth"), ("terraform", "breakdown")]
)
def test_allowlist(runner, run_mock, tmp_path, command):
    with pytest.raises(ValueError, match="not allowed"):
        runner._run(*command, [], tmp_path, 1)
    run_mock.assert_not_called()


def test_child_env(runner, run_mock, monkeypatch, tmp_path):
    for name in ("AWS_SECRET_ACCESS_KEY", "ARM_CLIENT_SECRET", "AZURE_CLIENT_SECRET"):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("INFRACOST_ENABLE_CLOUD", "true")

    runner.estimate(tmp_path)

    env = run_mock.call_args.kwargs["env"]
    assert env["INFRACOST_API_KEY"] == KEY
    assert [k for k in env if k.upper().startswith(("AWS_", "ARM_", "AZURE_", "INFRACOST_"))] == [
        "INFRACOST_API_KEY"
    ]


@pytest.mark.parametrize("returncode", [0, 1])
def test_key_never_in_logs_or_result(runner, run_mock, caplog, tmp_path, returncode):
    caplog.set_level(logging.DEBUG)
    run_mock.return_value = completed(returncode, stderr=f"invalid key {KEY}")

    result = runner.estimate(tmp_path)

    assert f"exit code {returncode}" in caplog.text
    assert "invalid key" not in caplog.text  # stderr is never logged
    assert KEY not in caplog.text
    assert KEY not in result.model_dump_json()
    assert KEY not in repr(runner.config)
    if returncode:
        assert_unavailable(result, "exit code 1")


def test_parses_sample(runner, run_mock, tmp_path):
    result = runner.estimate(tmp_path)

    assert result.status is CostStatus.AVAILABLE
    assert result.currency == "USD"
    assert result.monthly_total == Decimal("8.392")
    assert result.unsupported_count == 2
    # freeResources holds free and unsupported resources; the summary separates them.
    assert [(r.address, r.pricing.value, r.monthly_cost) for r in result.resources] == [
        ("aws_instance.web_server", "priced", Decimal("8.392")),
        ("aws_s3_bucket.assets", "usage-based", None),
        ("aws_subnet.a", "no charge", None),
        ("aws_subnet.b", "no charge", None),
        ("aws_vpc.main", "no charge", None),
        ("aws_route_table_association (1)", "no charge", None),
        ("aws_example_thing.x", "not priced", None),
        ("aws_example_thing (1)", "not priced", None),
    ]


def test_resource_of_an_uncounted_type_is_not_called_free():
    document = json.loads(SAMPLE)
    document["summary"] = {}
    pricing = {r.address: r.pricing.value for r in parse_breakdown(document).resources}
    assert pricing["aws_vpc.main"] == "not priced"


def sample_with(resources: list[dict], total: str) -> dict:
    document = json.loads(SAMPLE)
    document["projects"][0]["breakdown"]["resources"] = resources
    document["totalMonthlyCost"] = total
    return document


def test_null_cost_never_becomes_zero():
    result = parse_breakdown(sample_with([{"name": "a.b", "monthlyCost": None}], "0"))

    dumped = result.model_dump(mode="json")
    assert dumped["monthly_total"] is None
    assert {r["monthly_cost"] for r in dumped["resources"]} == {None}


def test_price_not_found_is_null_and_real_zero_stays():
    missing = {"name": "x", "monthlyCost": "0", "costComponents": [{"priceNotFound": True}]}
    result = parse_breakdown(
        sample_with(
            [
                {"name": "a.missing", "monthlyCost": "0", "subresources": [missing]},
                {**missing, "name": "a.partial", "monthlyCost": "2"},
                {"name": "a.zero", "monthlyCost": "0"},
            ],
            "2",
        )
    )
    by_address = {r.address: r for r in result.resources}
    assert by_address["a.missing"].monthly_cost is None
    assert by_address["a.missing"].pricing.value == "not priced"
    assert by_address["a.missing"].note == "Price not found."
    assert by_address["a.partial"].monthly_cost == Decimal(2)
    assert by_address["a.partial"].note.startswith("Partial")
    assert by_address["a.zero"].monthly_cost == Decimal(0)
    assert by_address["a.zero"].pricing.value == "priced"


def test_project_errors_are_unavailable():
    document = json.loads(SAMPLE)
    document["projects"][0]["metadata"]["errors"] = [{"message": "bad HCL"}]
    assert_unavailable(parse_breakdown(document), "could not evaluate")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CostEstimate(status="unavailable", guess=1)

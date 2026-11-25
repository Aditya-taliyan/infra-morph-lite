import re
from pathlib import Path

import pytest

from app.domain.cost import CostEstimate, CostStatus
from app.domain.terraform import CommandResult, CommandStatus, PlanSummary
from app.generators.terraform_generator import FILES
from app.infrastructure.workspace import RUN_RECORD, new_run_dir, read_run_record
from app.services.generation import InfrastructureGenerationService
from tests.helpers import load_spec, resource


def _result(name: str, status: CommandStatus) -> CommandResult:
    return CommandResult(
        command=["terraform", name],
        exit_code=0 if status is CommandStatus.PASSED else None,
        duration_seconds=0.1,
        status=status,
    )


class FakeRunner:
    """Records which steps ran; a step listed in `fail` fails."""

    def __init__(self, fail: str | None = None, status=CommandStatus.PASSED) -> None:
        self.fail = fail
        self.status = status
        self.calls: list[tuple[str, Path]] = []

    def _step(self, name: str, directory: Path) -> CommandResult:
        self.calls.append((name, directory))
        return _result(name, CommandStatus.FAILED if name == self.fail else self.status)

    def init(self, directory, provider="aws"):
        self.init_provider = provider
        return self._step("init", directory)

    def fmt_check(self, directory):
        return self._step("fmt", directory)

    def validate(self, directory):
        return self._step("validate", directory)

    def plan(self, directory):
        step = self._step("plan", directory)
        if step.status is not CommandStatus.PASSED:
            return [step], None
        return [step, _result("show", self.status)], PlanSummary(to_add=11)


def test_pipeline_writes_run_dir_and_runs_all_steps(tmp_path: Path):
    runner = FakeRunner()

    result = InfrastructureGenerationService(runner).run_pipeline(
        load_spec("demo2"), generated_root=tmp_path
    )

    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", result.run_id)
    assert result.output_dir == tmp_path / result.run_id
    assert sorted(p.name for p in result.output_dir.iterdir()) == sorted([*FILES, RUN_RECORD])
    record = read_run_record(result.output_dir)
    assert record["run_id"] == result.run_id
    assert record["provider"] == "aws"
    assert record["steps"][2] == {"step": "terraform validate", "status": "passed"}
    assert [name for name, _ in runner.calls] == ["init", "fmt", "validate", "plan"]
    assert {d for _, d in runner.calls} == {result.output_dir}
    assert [s.step for s in result.steps] == [
        "terraform init", "terraform fmt", "terraform validate", "terraform plan", "terraform show"
    ]
    assert result.plan_summary == PlanSummary(to_add=11)
    assert result.terraform_ok
    assert [i.code for i in result.validation.warnings] == ["W01"]


def test_pipeline_stops_at_first_failure(tmp_path: Path):
    runner = FakeRunner(fail="fmt")

    result = InfrastructureGenerationService(runner).run_pipeline(
        load_spec("demo2"), generated_root=tmp_path
    )

    assert [name for name, _ in runner.calls] == ["init", "fmt"]
    assert result.steps[-1].status is CommandStatus.FAILED
    assert not result.terraform_ok
    assert result.plan_summary is None
    assert result.files  # files are still returned


def test_pipeline_without_terraform_skips_steps_and_returns_files(tmp_path: Path):
    runner = FakeRunner(status=CommandStatus.SKIPPED)

    result = InfrastructureGenerationService(runner).run_pipeline(
        load_spec("demo2"), generated_root=tmp_path
    )

    assert {s.status for s in result.steps} == {CommandStatus.SKIPPED}
    assert result.terraform_ok
    assert tuple(result.files) == FILES


def test_invalid_spec_creates_no_run_dir_and_runs_nothing(tmp_path: Path):
    runner = FakeRunner()
    spec = load_spec(
        "demo2", lambda d: resource(d, "web-server").update(security_groups=["missing-sg"])
    )

    result = InfrastructureGenerationService(runner).run_pipeline(spec, generated_root=tmp_path)

    assert not result.validation.is_valid
    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_run_dirs_are_unique(tmp_path: Path):
    ids = {new_run_dir(tmp_path)[0] for _ in range(20)}
    assert len(ids) == 20


def test_azure_pipeline_skips_plan_without_calling_runner(tmp_path: Path):
    runner = FakeRunner()

    result = InfrastructureGenerationService(runner).run_pipeline(
        load_spec("demo4_azure"), generated_root=tmp_path
    )

    assert [name for name, _ in runner.calls] == ["init", "fmt", "validate"]
    assert runner.init_provider == "azure"
    plan = result.steps[-1]
    assert plan.step == "terraform plan"
    assert plan.status is CommandStatus.SKIPPED
    assert plan.stderr == "Azure plan requires an Azure login; not run by design"
    assert result.plan_summary is None
    assert result.terraform_ok


class FakeCostRunner:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[Path] = []

    def estimate(self, run_dir):
        self.calls.append(run_dir)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


PRICED = CostEstimate(status=CostStatus.AVAILABLE, currency="USD", monthly_total="8.39")


def run_with_cost(tmp_path, cost_result, spec="demo2", **runner_args):
    cost_runner = FakeCostRunner(cost_result)
    service = InfrastructureGenerationService(
        FakeRunner(**runner_args), generated_root=tmp_path, cost_runner=cost_runner
    )
    return service.run_pipeline(load_spec(spec)), cost_runner


@pytest.mark.parametrize("spec", ["demo2", "demo4_azure"])
def test_cost_runs_after_validate_for_both_providers(tmp_path: Path, spec):
    result, cost_runner = run_with_cost(tmp_path, PRICED, spec)

    assert result.cost == PRICED
    assert cost_runner.calls == [result.output_dir]


def test_cost_is_kept_when_plan_fails(tmp_path: Path):
    result, _ = run_with_cost(tmp_path, PRICED, fail="plan")

    assert not result.terraform_ok
    assert result.cost == PRICED


@pytest.mark.parametrize("args", [{"fail": "validate"}, {"status": CommandStatus.SKIPPED}])
def test_no_cost_unless_validate_passed(tmp_path: Path, args):
    result, cost_runner = run_with_cost(tmp_path, PRICED, **args)

    assert cost_runner.calls == []
    assert result.cost.status is CostStatus.UNAVAILABLE


def test_cost_crash_does_not_fail_the_run_or_leak(tmp_path: Path, caplog):
    result, _ = run_with_cost(tmp_path, RuntimeError("secret-text"))

    assert result.terraform_ok and result.plan_summary is not None
    assert result.cost.status is CostStatus.UNAVAILABLE
    assert "secret-text" not in caplog.text + result.cost.message

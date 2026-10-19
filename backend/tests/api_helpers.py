"""Shared pieces for the API tests: a fake Terraform runner and a client."""

import copy
import json
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_generation_service
from app.domain.terraform import CommandResult, CommandStatus, PlanSummary
from app.main import app
from app.services.generation import InfrastructureGenerationService
from tests.helpers import FIXTURES


def spec_json(name: str, mutate=None) -> dict:
    """fixtures/specs/<name>.json as a dict, optionally changed first."""
    data = json.loads((FIXTURES / "specs" / f"{name}.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    if mutate:
        mutate(data)
    return data


class FakeRunner:
    """Stands in for TerraformRunner; never starts a process.

    fail:  the step with this name fails.
    error: raised from init (simulates a bug).
    gate:  init waits for this event (lets a test hold a run open);
           `started` is set as soon as init is entered.
    """

    def __init__(
        self,
        fail: str | None = None,
        error: Exception | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.fail = fail
        self.error = error
        self.gate = gate
        self.started = threading.Event()
        self.calls: list[str] = []

    def _step(self, name: str) -> CommandResult:
        self.calls.append(name)
        failed = name == self.fail
        return CommandResult(
            command=["terraform", name],
            exit_code=1 if failed else 0,
            stderr="Error: simulated failure" if failed else "",
            duration_seconds=0.1,
            status=CommandStatus.FAILED if failed else CommandStatus.PASSED,
        )

    def init(self, directory: Path) -> CommandResult:
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=10), "test gate was never released"
        if self.error is not None:
            raise self.error
        return self._step("init")

    def fmt_check(self, directory: Path) -> CommandResult:
        return self._step("fmt")

    def validate(self, directory: Path) -> CommandResult:
        return self._step("validate")

    def plan(self, directory: Path):
        step = self._step("plan")
        if step.status is not CommandStatus.PASSED:
            return [step], None
        return [step, self._step("show")], PlanSummary(to_add=11)


def make_client(
    runner: FakeRunner, generated_root: Path, raise_server_exceptions: bool = True
) -> TestClient:
    """TestClient whose /generate uses `runner` and writes under `generated_root`.

    The caller must clear app.dependency_overrides afterwards (fixtures do).
    """
    app.dependency_overrides[get_generation_service] = lambda: InfrastructureGenerationService(
        runner, generated_root=generated_root
    )
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)

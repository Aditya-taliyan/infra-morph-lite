"""Run Terraform as a controlled subprocess (docs/PROJECT_SPEC.md §13, §15).

Safety rules enforced here:
- Only allowlisted subcommands run; apply, destroy, import, state, ... raise
  ValueError before anything is executed.
- Argument lists only, never shell=True; always a timeout; cwd is the run's
  isolated directory.
- The child environment has every AWS_*, ARM_* and AZURE_* variable removed,
  so real cloud credentials on this machine can never reach Terraform.
- The environment is never logged or returned.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from app.core.config import PLUGIN_CACHE_DIR, Settings, settings
from app.domain.terraform import CommandResult, CommandStatus, PlanSummary

ALLOWED_SUBCOMMANDS = frozenset({"init", "fmt", "validate", "plan", "show"})
CREDENTIAL_PREFIXES = ("AWS_", "ARM_", "AZURE_")
PLAN_FILE = "plan.tfplan"


class TerraformRunner:
    def __init__(
        self, config: Settings = settings, plugin_cache_dir: Path = PLUGIN_CACHE_DIR
    ) -> None:
        self.config = config
        self.plugin_cache_dir = plugin_cache_dir

    def init(self, directory: Path) -> CommandResult:
        return self._run(
            "init",
            ["-backend=false", "-input=false", "-no-color"],
            directory,
            self.config.terraform_init_timeout_seconds,
        )

    def fmt_check(self, directory: Path) -> CommandResult:
        # -check: report unformatted files (exit 3) instead of rewriting them.
        return self._run(
            "fmt",
            ["-check", "-diff", "-no-color"],
            directory,
            self.config.terraform_timeout_seconds,
        )

    def validate(self, directory: Path) -> CommandResult:
        # validate has no -input flag.
        return self._run(
            "validate", ["-no-color"], directory, self.config.terraform_timeout_seconds
        )

    def plan(self, directory: Path) -> tuple[list[CommandResult], PlanSummary | None]:
        """terraform plan -out, then terraform show -json parsed into a summary."""
        timeout = self.config.terraform_timeout_seconds
        plan = self._run(
            "plan", ["-input=false", "-no-color", f"-out={PLAN_FILE}"], directory, timeout
        )
        if plan.status is not CommandStatus.PASSED:
            return [plan], None

        show = self._run("show", ["-json", "-no-color", PLAN_FILE], directory, timeout)
        if show.status is not CommandStatus.PASSED:
            return [plan, show], None

        try:
            summary = PlanSummary.from_show_json(json.loads(show.stdout))
        except (ValueError, KeyError, TypeError) as exc:
            show = show.model_copy(
                update={
                    "status": CommandStatus.FAILED,
                    "stderr": f"Could not parse terraform show -json output: {exc}",
                }
            )
            return [plan, show], None
        return [plan, show], summary

    def _run(
        self, subcommand: str, args: list[str], cwd: Path, timeout: float
    ) -> CommandResult:
        if subcommand not in ALLOWED_SUBCOMMANDS:
            raise ValueError(
                f"terraform {subcommand!r} is not allowed; "
                f"allowed: {', '.join(sorted(ALLOWED_SUBCOMMANDS))}"
            )

        binary = shutil.which("terraform")
        command = ["terraform", subcommand, *args]
        if binary is None:
            return CommandResult(
                command=command,
                exit_code=None,
                stderr="Terraform is not installed (not found on PATH); step skipped.",
                duration_seconds=0.0,
                status=CommandStatus.SKIPPED,
            )

        start = time.monotonic()
        try:
            completed = subprocess.run(
                [binary, subcommand, *args],
                cwd=cwd,
                env=self._child_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                exit_code=None,
                stderr=f"Timed out after {timeout:g} seconds.",
                duration_seconds=round(time.monotonic() - start, 2),
                status=CommandStatus.TIMEOUT,
            )

        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=round(time.monotonic() - start, 2),
            status=CommandStatus.PASSED if completed.returncode == 0 else CommandStatus.FAILED,
        )

    def _child_env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(CREDENTIAL_PREFIXES)
        }
        self.plugin_cache_dir.mkdir(parents=True, exist_ok=True)
        env["TF_PLUGIN_CACHE_DIR"] = str(self.plugin_cache_dir)
        env["TF_IN_AUTOMATION"] = "1"  # shorter output, no "run terraform apply" hints
        env["CHECKPOINT_DISABLE"] = "1"  # no version-check call to HashiCorp
        return env

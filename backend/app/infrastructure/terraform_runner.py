"""Run Terraform as a controlled subprocess (docs/PROJECT_SPEC.md §13, §15).

Safety rules enforced here:
- Only allowlisted subcommands run; apply, destroy, import, state, ... raise
  ValueError before anything is executed.
- Argument lists only, never shell=True; always a timeout; cwd is the run's
  isolated directory.
- The child environment has every AWS_*, ARM_* and AZURE_* variable removed,
  so real cloud credentials on this machine can never reach Terraform.
- The environment is never logged or returned.

Shared data directory (performance, mainly on Windows):
Every run uses one persistent TF_DATA_DIR (backend/.terraform-shared/), so
the AWS provider is installed there once and reused. A plugin cache alone is
not enough on Windows: Terraform cannot symlink from the cache, so it copied
the provider (hundreds of MB) into every run and antivirus scanned each copy.
After the first successful init, the run's .terraform.lock.hcl is saved in
the shared directory and copied into each new run directory before init, so
later inits only verify the installed provider against the lock instead of
asking the registry. The .tf files and plan.tfplan stay in the run directory,
so runs remain isolated.

Concurrency: the shared directory assumes one Terraform command at a time.
A process-wide lock serialises commands within this process (CLI, or the API
server's worker threads). Two separate processes (e.g. two CLI runs started
at once) are NOT coordinated; this is a single-user tool, so do not run them
in parallel.
"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.core.config import SHARED_DATA_DIR, Settings, settings
from app.domain.terraform import CommandResult, CommandStatus, PlanSummary

ALLOWED_SUBCOMMANDS = frozenset({"init", "fmt", "validate", "plan", "show"})
CREDENTIAL_PREFIXES = ("AWS_", "ARM_", "AZURE_")
PLAN_FILE = "plan.tfplan"
LOCK_FILE = ".terraform.lock.hcl"

# One Terraform command at a time uses the shared data dir (see module docstring).
_SHARED_DATA_DIR_LOCK = threading.Lock()


class TerraformRunner:
    def __init__(
        self, config: Settings = settings, shared_data_dir: Path = SHARED_DATA_DIR
    ) -> None:
        self.config = config
        self.shared_data_dir = shared_data_dir

    @property
    def shared_lock_file(self) -> Path:
        return self.shared_data_dir / LOCK_FILE

    def init(self, directory: Path) -> CommandResult:
        run_lock = directory / LOCK_FILE
        seeded = self.shared_lock_file.is_file() and not run_lock.exists()
        if seeded:
            shutil.copyfile(self.shared_lock_file, run_lock)

        result = self._run(
            "init",
            ["-backend=false", "-input=false", "-no-color"],
            directory,
            self.config.terraform_init_timeout_seconds,
        )

        if result.status is CommandStatus.PASSED and run_lock.is_file():
            # Keep the shared copy current (it changes only if the provider
            # version in the templates changes).
            shutil.copyfile(run_lock, self.shared_lock_file)
        elif result.status is CommandStatus.FAILED and seeded:
            result = result.model_copy(
                update={
                    "stderr": result.stderr
                    + f"\nHint: init used the saved lock file {self.shared_lock_file}. "
                    "If the provider version in the templates changed, delete "
                    f"{self.shared_data_dir} and run again."
                }
            )
        return result

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
            with _SHARED_DATA_DIR_LOCK:
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
        self.shared_data_dir.mkdir(parents=True, exist_ok=True)
        # Absolute path: Terraform would resolve a relative one against cwd.
        env["TF_DATA_DIR"] = str(self.shared_data_dir.resolve())
        # Not needed with a shared data dir, and it would add one more copy.
        env.pop("TF_PLUGIN_CACHE_DIR", None)
        env["TF_IN_AUTOMATION"] = "1"  # shorter output, no "run terraform apply" hints
        env["CHECKPOINT_DISABLE"] = "1"  # no version-check call to HashiCorp
        return env

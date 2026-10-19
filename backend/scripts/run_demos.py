"""Run the AWS demo prompts of docs/PROJECT_SPEC.md §41 through the real pipeline.

Each prompt goes natural language -> SpecEngine -> generation service with
real terraform (init, fmt check, validate, plan with mock credentials).
Demo 4 is Azure and is not run here (Azure plan is out of scope).

Usage (PowerShell, from backend/ with the venv active):
    python scripts/run_demos.py                # Ollama, rule-based fallback
    python scripts/run_demos.py --rule-based   # rule-based interpreter only

Exit code 1 if any demo fails: no valid spec, a terraform step failed or
was skipped, or the plan adds nothing.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Allow running as a plain script from backend/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.enums import Provider  # noqa: E402
from app.domain.terraform import CommandStatus  # noqa: E402
from app.interpreters.rule_based import RuleBasedInterpreter  # noqa: E402
from app.services.generation import InfrastructureGenerationService  # noqa: E402
from app.services.spec_engine import SpecEngine  # noqa: E402

DEMOS = [
    ("Demo 1", "Create a basic AWS web server."),
    ("Demo 2", "Create an AWS VPC with a public subnet and web server."),
    ("Demo 3", "Create an AWS storage bucket."),
    ("Demo 5", "Create a web infrastructure with networking, compute, security, and storage."),
]


@dataclass
class DemoRow:
    demo: str
    interpreter: str = "-"
    attempts: int = 0
    spec_valid: bool = False
    warnings: int = 0
    validate_passed: bool = False
    plan_to_add: int | None = None
    seconds: float = 0.0
    problem: str = ""
    retries: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.spec_valid and self.validate_passed and bool(self.plan_to_add)


def run_demo(
    demo: str, prompt: str, engine: SpecEngine, service: InfrastructureGenerationService
) -> DemoRow:
    row = DemoRow(demo=demo)
    started = time.perf_counter()
    try:
        interpreted = engine.interpret(prompt, Provider.AWS)
        final = interpreted.final
        row.interpreter = final.interpreter
        row.attempts = final.attempts
        if not interpreted.ok:
            row.problem = "; ".join(interpreted.errors) or "no spec"
            return row
        row.spec_valid = True
        # Errors of attempts that were retried and then succeeded.
        row.retries = list(final.errors)

        result = service.run_pipeline(final.spec)
        row.warnings = len(result.validation.warnings)
        for step in result.steps:
            if step.step == "terraform validate":
                row.validate_passed = step.status is CommandStatus.PASSED
        if result.plan_summary is not None:
            row.plan_to_add = result.plan_summary.to_add
        bad = [s for s in result.steps if s.status is not CommandStatus.PASSED]
        if bad:
            row.problem = f"{bad[0].step} {bad[0].status}: {bad[0].stderr.strip()[:300]}"
        elif not row.plan_to_add:
            row.problem = "plan adds no resources"
        if interpreted.fallback_reason:
            row.problem = (row.problem + " | " if row.problem else "") + (
                f"fallback: {interpreted.fallback_reason}"
            )
    finally:
        row.seconds = time.perf_counter() - started
    return row


def print_table(rows: list[DemoRow]) -> None:
    def yes_no(value: bool) -> str:
        return "yes" if value else "NO"

    headers = ["demo", "interpreter", "attempts", "spec valid", "warnings",
               "validate passed", "plan to_add", "total s"]
    table = [
        [r.demo, r.interpreter, str(r.attempts), yes_no(r.spec_valid), str(r.warnings),
         yes_no(r.validate_passed), "-" if r.plan_to_add is None else str(r.plan_to_add),
         f"{r.seconds:.1f}"]
        for r in rows
    ]
    widths = [max(len(h), *(len(line[i]) for line in table)) for i, h in enumerate(headers)]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for line in table:
        print(" | ".join(cell.ljust(w) for cell, w in zip(line, widths)))
    for r in rows:
        for error in r.retries:
            print(f"{r.demo} retried: {error[:300]}")
        if r.problem:
            print(f"{r.demo}: {r.problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rule-based", action="store_true", help="Use only the rule-based interpreter."
    )
    args = parser.parse_args(argv)

    if args.rule_based:
        rule_based = RuleBasedInterpreter()
        engine = SpecEngine(primary=rule_based, fallback=rule_based)
    else:
        engine = SpecEngine()
    service = InfrastructureGenerationService()

    rows = []
    for demo, prompt in DEMOS:
        print(f"Running {demo}: {prompt}", flush=True)
        rows.append(run_demo(demo, prompt, engine, service))
    print()
    print_table(rows)
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

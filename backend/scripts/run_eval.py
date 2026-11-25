"""Run the AI evaluation set (eval/prompts.jsonl) through SpecEngine and score it.

Only interpretation is measured (natural language -> validated IR); no
Terraform runs. Each run writes eval/results/<timestamp>-<interpreter>.json
and regenerates docs/EVALUATION.md from the newest result of each interpreter.

Usage (PowerShell, venv active; from the repo root or backend/):
    python backend/scripts/run_eval.py --interpreter rule-based
    python backend/scripts/run_eval.py --interpreter ollama   # Ollama only, no fallback
    python backend/scripts/run_eval.py --interpreter auto     # Ollama, rule-based fallback
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# Allow running as a plain script without installing the package.
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.domain.enums import Provider  # noqa: E402
from app.interpreters.base import (  # noqa: E402
    InterpretationResult,
    InterpretationStatus,
    SpecInterpreter,
)
from app.interpreters.ollama_interpreter import OllamaInterpreter  # noqa: E402
from app.interpreters.rule_based import RuleBasedInterpreter  # noqa: E402
from app.services.spec_engine import SpecEngine  # noqa: E402
from app.validators.semantic import validate  # noqa: E402
from eval import scoring  # noqa: E402

PROMPTS = BACKEND / "eval" / "prompts.jsonl"
RESULTS = BACKEND / "eval" / "results"
DOC = BACKEND.parent / "docs" / "EVALUATION.md"
INTERPRETERS = ("rule-based", "ollama", "auto")


class NoFallback(SpecInterpreter):
    """For --interpreter ollama: a failed Ollama case must stay failed."""

    name = "none"

    def interpret(
        self, requirement: str, provider: Provider, unsupported: Sequence[str] = ()
    ) -> InterpretationResult:
        return InterpretationResult(self.name, InterpretationStatus.FAILED)


def build_engine(interpreter: str) -> SpecEngine:
    if interpreter == "rule-based":
        rule_based = RuleBasedInterpreter()
        return SpecEngine(primary=rule_based, fallback=rule_based)
    if interpreter == "ollama":
        return SpecEngine(primary=OllamaInterpreter(), fallback=NoFallback())
    return SpecEngine()


def outputs(result: InterpretationResult) -> tuple[int, int]:
    """(candidate specs the interpreter produced, how many failed validation)."""
    if result.interpreter == "rule-based":
        # shortcut: a failed rule-based run found nothing to build, so it produced no candidate;
        # a template bug would also land here. Count it if the rule-based parser ever retries.
        return (1, 0) if result.ok else (0, 0)
    unavailable = result.status is InterpretationStatus.UNAVAILABLE
    produced = result.attempts - unavailable
    return produced, produced - result.ok


def run_case(case: dict, engine: SpecEngine) -> dict:
    started = time.perf_counter()
    interpreted = engine.interpret(case["requirement"], Provider(case["provider"]))
    latency = time.perf_counter() - started

    if engine.primary is engine.fallback:
        # rule-based mode: a failed case ran the same parser twice; count it once.
        tried = [interpreted.final]
    else:
        tried = [r for r in (interpreted.primary, interpreted.final)
                 if r is not None and r.interpreter != NoFallback.name]
    used = interpreted.final if interpreted.ok else tried[-1]
    spec = interpreted.final.spec
    produced = [outputs(r) for r in tried]
    return {
        "id": case["id"],
        "category": case["category"],
        "provider": case["provider"],
        "interpreter": used.interpreter,
        "status": used.status.value,
        "attempts": sum(r.attempts for r in tried),
        "outputs": sum(p for p, _ in produced),
        "rejected": sum(r for _, r in produced),
        "latency_s": round(latency, 3),
        # Re-validated here so "valid" is measured, not taken from the engine.
        "valid": spec is not None and validate(spec).is_valid,
        "spec": spec.model_dump(mode="json") if spec is not None else None,
        "warnings": [i.code for i in interpreted.final.validation.warnings]
        if interpreted.final.validation else [],
        "errors": [f"{r.interpreter}: {e}" for r in tried for e in r.errors],
        "fallback_reason": interpreted.fallback_reason,
    }


def pct(rate: dict) -> str:
    if rate["rate"] is None:
        return "n/a"
    return f"{rate['rate'] * 100:.1f}% ({rate['count']}/{rate['total']})"


def seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


METRIC_ROWS = [
    ("Spec success rate (valid spec or correct clean failure)", "spec_success", pct),
    ("Provider accuracy", "provider_accuracy", pct),
    ("Resource-kind accuracy", "kind_accuracy", pct),
    ("Hallucinated-resource rate (lower is better)", "hallucinated_resources", pct),
    ("Secure-default compliance", "secure_default_compliance", pct),
    ("Injection flagged rate", "injection_flagged", pct),
    ("Invalid-generation rate (rejected outputs / outputs; lower is better)",
     "invalid_generation", pct),
    ("Mean attempts per case", "mean_attempts",
     lambda v: "n/a" if v is None else f"{v:.2f}"),
    ("Latency p50", "latency_p50_s", seconds),
    ("Latency p95", "latency_p95_s", seconds),
]

CATEGORY_COLUMNS = [("Cases", "cases", str), ("Spec success", "spec_success", pct),
                    ("Kind accuracy", "kind_accuracy", pct),
                    ("Hallucinated", "hallucinated_resources", pct),
                    ("Secure default", "secure_default_compliance", pct),
                    ("Injection flagged", "injection_flagged", pct),
                    ("Mean attempts", "mean_attempts", lambda v: f"{v:.2f}"),
                    ("p50", "latency_p50_s", seconds)]


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + [
        "| " + " | ".join(row) + " |" for row in rows]


def run_label(run: dict) -> str:
    if run["interpreter"] == "rule-based":
        return (f"Rule-based parser, run on {run['date']}. Deterministic: the same "
                "dataset gives the same results on every run.")
    fallback = "" if run["interpreter"] == "ollama" else ", with the rule-based fallback"
    return (f"**Results come from a local Qwen2.5 7B run (`{run['model']}` via Ollama"
            f"{fallback}) on {run['date']}. LLM output is not deterministic; results "
            "vary between runs.**")


def render_doc(runs: list[dict], cases: list[dict]) -> str:
    counts = {c: sum(case["category"] == c for case in cases) for c in scoring.CATEGORIES}
    lines = [
        "# AI Evaluation",
        "",
        "> Generated by `backend/scripts/run_eval.py`; do not edit by hand. Re-run the "
        "script to update it.",
        "",
        "## Method",
        "",
        f"Each of the {len(cases)} cases in `backend/eval/prompts.jsonl` is a requirement in "
        "plain English plus a provider. It goes through `SpecEngine` (natural language -> "
        "IR -> schema validation -> semantic validation), exactly as the API does. The "
        "spec that comes out is scored against the case's expectations by "
        "`backend/eval/scoring.py`. The expectations were written from the requirement "
        "text alone, before any run. No Terraform runs; this measures interpretation only.",
        "",
        "Checks per case: the outcome (a valid spec, or a clean failure where the case "
        "allows one), the provider, the required resource kinds with minimum counts, kinds "
        "that were not asked for, unknown kinds the interpreter tried to create (rejected "
        "by the schema), expected warnings, secure defaults (no SSH, RDP or all ports "
        "open to 0.0.0.0/0, no public bucket), and for injection cases that all open "
        "access is flagged by W01-W04. The security checks read the spec itself, not the "
        "validator's warnings. A check that does not apply to a case is left out.",
        "",
        "Notes on the metrics:",
        "",
        "- Provider accuracy is 100% by design: the user's provider always overrides the "
        "interpreter's (`check_candidate`). It is reported to show that this holds.",
        "- The IR schema is a closed list of four kinds, so an invented kind can never reach "
        "a valid spec. The hallucinated-resource rate therefore counts cases where a "
        "valid spec has a kind that was not asked for, or where any attempt was rejected "
        "for an unknown kind.",
        "- SpecEngine never returns an invalid spec, so the invalid-generation rate is per "
        "attempt: interpreter outputs that failed validation, over all outputs.",
        "- Latency is wall-clock time of `SpecEngine.interpret` per case, nearest-rank "
        "percentiles.",
        "",
        "## Dataset",
        "",
        *table(["Category", "Cases", "What it tests"], [
            [c, str(counts[c]), what] for c, what in (
                ("basic", "one resource on AWS (web server, bucket, VPC, ...)"),
                ("multi", "several resources together on AWS"),
                ("azure", "the same kinds of requests on Azure"),
                ("secure_default", "requests that do not mention security; nothing may be "
                                   "opened to the internet"),
                ("out_of_scope", "databases, Kubernetes, load balancers, Lambda; a clean "
                                 "failure or only the supported parts"),
                ("injection", "requests that try to override the rules; open access must be "
                              "flagged"),
                ("ambiguous", "vague requests; a valid minimal spec or a clean failure"),
            )]),
        "",
        "## Results",
        "",
    ]
    for run in runs:
        lines += [f"### {run['interpreter']}", "", run_label(run), "",
                  f"Result file: `backend/eval/results/{run['file']}`", ""]
        overall = run["summary"]["overall"]
        lines += table(["Metric", "Value"],
                       [[label, fmt(overall[key])] for label, key, fmt in METRIC_ROWS])
        lines += ["", "Per category:", ""]
        lines += table(["Category"] + [h for h, _, _ in CATEGORY_COLUMNS],
                       [[c] + [fmt(m[key]) for _, key, fmt in CATEGORY_COLUMNS]
                        for c, m in run["summary"]["by_category"].items()])
        lines.append("")
    lines += ["## Failures", ""]
    for run in runs:
        failed = scoring.failures(run["cases"])
        lines += [f"### {run['interpreter']} ({len(failed)} failed checks)", ""]
        if failed:
            lines += table(["Case", "Check", "Reason"],
                           [[i, c, r.replace("|", "\\|")[:300]] for i, c, r in failed])
        else:
            lines.append("None.")
        lines.append("")
    return "\n".join(lines)


def latest_runs() -> list[dict]:
    """The newest result file of each interpreter, in INTERPRETERS order."""
    newest = {}
    for path in sorted(RESULTS.glob("*.json")):  # names start with a sortable timestamp
        run = json.loads(path.read_text(encoding="utf-8"))
        newest[run["interpreter"]] = {**run, "file": path.name}
    return [newest[i] for i in INTERPRETERS if i in newest]


def print_summary(run: dict) -> None:
    print(f"\n{run['interpreter']}: {len(run['cases'])} cases, {run['date']}")
    overall = run["summary"]["overall"]
    rows = [[label, fmt(overall[key])] for label, key, fmt in METRIC_ROWS]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}")
    print("\nPer category:")
    headers = ["category"] + [h for h, _, _ in CATEGORY_COLUMNS]
    body = [[c] + [fmt(m[key]) for _, key, fmt in CATEGORY_COLUMNS]
            for c, m in run["summary"]["by_category"].items()]
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(headers)]
    for row in [headers] + body:
        print("  " + " | ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    failed = scoring.failures(run["cases"])
    print(f"\nFailed checks: {len(failed)}")
    for case_id, check, reason in failed:
        print(f"  {case_id} {check}: {reason[:200]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interpreter", choices=INTERPRETERS, default="auto")
    args = parser.parse_args(argv)

    cases = scoring.load_cases(PROMPTS)
    engine = build_engine(args.interpreter)
    records = []
    for case in cases:
        print(f"{case['id']}: {case['requirement'][:70]}", flush=True)
        record = run_case(case, engine)
        record["checks"] = scoring.score_case(case, record)
        records.append(record)

    if all(r["status"] == "unavailable" for r in records):
        print("\nEvery case failed with the interpreter unavailable (is Ollama running?). "
              "Nothing written.")
        return 1

    now = datetime.now()
    run = {
        "interpreter": args.interpreter,
        "date": now.strftime("%Y-%m-%d %H:%M"),
        "model": settings.ollama_model if args.interpreter != "rule-based" else None,
        "summary": scoring.summarize(records),
        "cases": records,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{now.strftime('%Y%m%d-%H%M%S')}-{args.interpreter}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    DOC.write_text(render_doc(latest_runs(), cases), encoding="utf-8")

    print_summary(run)
    print(f"\nWrote {path.relative_to(BACKEND.parent)} and {DOC.relative_to(BACKEND.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

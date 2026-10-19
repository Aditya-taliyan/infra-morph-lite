import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.version import __version__
from app.domain.enums import Provider
from app.domain.spec import InfrastructureSpecification
from app.domain.terraform import CommandStatus
from app.providers.registry import UnsupportedProviderError
from app.services.generation import (
    GenerationResult,
    InfrastructureGenerationService,
    build_minimal_spec,
)
from app.services.spec_engine import SpecEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infra-morph",
        description="Infra Morph Lite CLI",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    commands.add_parser("version", help="Print the version.")

    generate = commands.add_parser("generate", help="Generate a minimal AWS network.")
    generate.add_argument("name", nargs="?", help="Infrastructure project name.")
    generate.add_argument(
        "--output",
        type=Path,
        help="Directory where the generated Terraform files should be written.",
    )

    from_spec = commands.add_parser(
        "generate-from-spec",
        help="Run the full pipeline (validate, generate, terraform checks) on a spec file.",
    )
    from_spec.add_argument("spec", type=Path, help="Path to an IR spec JSON file.")

    interpret = commands.add_parser(
        "interpret",
        help="Turn a natural-language requirement into a validated spec (prints the JSON).",
    )
    interpret.add_argument("requirement", help="The requirement, in quotes.")
    interpret.add_argument(
        "--provider", choices=[p.value for p in Provider], default=Provider.AWS.value
    )
    interpret.add_argument(
        "--generate",
        action="store_true",
        help="Also run the full pipeline (generate, terraform checks) on the spec.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "version":
        print(f"Infra Morph Lite {__version__}")
        return

    if args.command == "generate":
        if not args.name:
            parser.error("the generate command requires a project name")

        try:
            spec = build_minimal_spec(args.name)
        except ValidationError as exc:
            parser.error(f"invalid specification: {exc}")

        result = InfrastructureGenerationService().generate(spec, output_dir=args.output)

        print_issues(result.validation)
        if not result.validation.is_valid:
            parser.exit(2, "Generation stopped: the specification has errors.\n")

        if args.output is None:
            for filename, content in result.files.items():
                print(f"# --- {filename} ---")
                print(content, end="")
        else:
            print(f"Generated Terraform: {args.output}")
        return

    if args.command == "generate-from-spec":
        sys.exit(generate_from_spec(args.spec))

    if args.command == "interpret":
        sys.exit(interpret(args.requirement, Provider(args.provider), args.generate))


def print_issues(validation) -> None:
    for issue in validation.issues:
        print(f"{issue.severity.value.upper()} {issue.code}: {issue.message}", file=sys.stderr)


def load_spec_file(path: Path) -> InfrastructureSpecification:
    """Read and schema-validate a spec file; raises ValueError with a readable message."""
    try:
        # Bytes, not text: json detects UTF-8, UTF-8 with BOM and UTF-16, so a
        # file made with PowerShell's ">" (BOM or UTF-16) loads too.
        data = json.loads(path.read_bytes())
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    try:
        return InfrastructureSpecification.model_validate(data)
    except ValidationError as exc:
        lines = [f"{path} does not match the IR schema ({exc.error_count()} errors):"]
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            lines.append(f"  {location}: {error['msg']}")
        raise ValueError("\n".join(lines)) from exc


def generate_from_spec(path: Path) -> int:
    """Run the full pipeline and print a report. Returns the process exit code."""
    try:
        spec = load_spec_file(path)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    return run_pipeline(spec)


def run_pipeline(spec: InfrastructureSpecification) -> int:
    """Run the full pipeline on a spec and print a report; returns the exit code."""
    try:
        result = InfrastructureGenerationService().run_pipeline(spec)
    except UnsupportedProviderError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return print_pipeline_report(result)


def print_pipeline_report(result: GenerationResult) -> int:
    print_issues(result.validation)
    if not result.validation.is_valid:
        print("Generation stopped: the specification has errors.", file=sys.stderr)
        return 1

    print(f"Generated Terraform: {result.output_dir}")
    for step in result.steps:
        print(f"{step.status.value.upper():<8} {step.step} ({step.duration_seconds:.1f}s)")
        if step.status is CommandStatus.SKIPPED:
            print(f"         {step.stderr}")
        elif step.status is not CommandStatus.PASSED:
            # Show Terraform's own output so errors are never silently hidden.
            print((step.stderr or step.stdout).rstrip(), file=sys.stderr)

    if result.plan_summary is not None:
        print(result.plan_summary.line())

    return 0 if result.terraform_ok else 1


def interpret(requirement: str, provider: Provider, generate: bool) -> int:
    """Print the spec JSON on stdout; everything else goes to stderr, so
    `> spec.json` gives a file that generate-from-spec accepts."""
    result = SpecEngine().interpret(requirement, provider)
    final = result.final
    if result.fallback_reason:
        print(f"Fallback: {result.fallback_reason}", file=sys.stderr)
    for note in final.notes:
        print(f"NOTE {note}", file=sys.stderr)

    if not result.ok:
        for error in result.errors:
            print(f"ERROR {error}", file=sys.stderr)
        print("No valid specification could be made from the requirement.", file=sys.stderr)
        return 1

    attempts = "attempt" if final.attempts == 1 else "attempts"
    print(f"Interpreter: {final.interpreter} ({final.attempts} {attempts})", file=sys.stderr)
    print(final.spec.model_dump_json(indent=2))

    if not generate:
        print_issues(final.validation)
        return 0
    # The pipeline report prints the validation issues itself.
    print("Running the pipeline on this spec...", file=sys.stderr)
    return run_pipeline(final.spec)


if __name__ == "__main__":
    main()

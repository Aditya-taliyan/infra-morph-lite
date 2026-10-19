import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.version import __version__
from app.domain.spec import InfrastructureSpecification
from app.domain.terraform import CommandStatus
from app.services.generation import InfrastructureGenerationService, build_minimal_spec


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


def print_issues(validation) -> None:
    for issue in validation.issues:
        print(f"{issue.severity.value.upper()} {issue.code}: {issue.message}", file=sys.stderr)


def load_spec_file(path: Path) -> InfrastructureSpecification:
    """Read and schema-validate a spec file; raises ValueError with a readable message."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
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

    result = InfrastructureGenerationService().run_pipeline(spec)

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


if __name__ == "__main__":
    main()

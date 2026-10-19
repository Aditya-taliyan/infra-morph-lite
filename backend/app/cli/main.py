import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.version import __version__
from app.services.generation import InfrastructureGenerationService, build_minimal_spec


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="infra-morph",
        description="Infra Morph Lite CLI",
    )

    parser.add_argument(
        "command",
        choices=["version", "generate"],
        help="Command to execute.",
    )

    parser.add_argument(
        "name",
        nargs="?",
        help="Infrastructure project name.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Directory where the generated Terraform files should be written.",
    )

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

        for issue in result.validation.issues:
            print(f"{issue.severity.value.upper()} {issue.code}: {issue.message}", file=sys.stderr)
        if not result.validation.is_valid:
            parser.exit(2, "Generation stopped: the specification has errors.\n")

        if args.output is None:
            for filename, content in result.files.items():
                print(f"# --- {filename} ---")
                print(content, end="")
        else:
            print(f"Generated Terraform: {args.output}")


if __name__ == "__main__":
    main()
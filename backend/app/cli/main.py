import argparse
from pathlib import Path

from app.core.version import __version__
from app.services.generation import InfrastructureGenerationService


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
        help="Path where generated Terraform should be written.",
    )

    args = parser.parse_args()

    if args.command == "version":
        print(f"Infra Morph Lite {__version__}")
        return

    if args.command == "generate":
        if not args.name:
            parser.error("the generate command requires a project name")

        service = InfrastructureGenerationService()
        result = service.generate(
            args.name,
            output_path=args.output,
        )

        if args.output is None:
            print(result, end="")
        else:
            print(f"Generated Terraform: {args.output}")


if __name__ == "__main__":
    main()
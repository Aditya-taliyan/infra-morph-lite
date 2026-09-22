import argparse

from app.core.version import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="infra-morph",
        description="Infra Morph Lite CLI",
    )

    parser.add_argument(
        "command",
        choices=["version"],
        help="Command to execute.",
    )

    args = parser.parse_args()

    if args.command == "version":
        print(f"Infra Morph Lite {__version__}")


if __name__ == "__main__":
    main()
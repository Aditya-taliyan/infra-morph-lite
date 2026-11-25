"""Scan git-tracked files for obvious secrets (fallback for gitleaks).

Usage (PowerShell, from any directory):  python backend/scripts/check_secrets.py
Prints file:line and the rule, never the value. Exit code 1 if anything is found.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Realistic lengths, so short dummies such as "ico-test" are not reported.
RULES = {
    "aws-access-key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "infracost-key": re.compile(r"INFRACOST_API_KEY\s*=\s*[\"']?ico-[\w-]{8,}", re.IGNORECASE),
    # The digit tells a key from an identifier such as api_key=config.some_name.
    "generic-api-key": re.compile(
        r"api[_-]?key[\"']?\s*[:=]\s*[\"']?(?=[\w-]*\d)[\w-]{16,}", re.IGNORECASE
    ),
}


def find_secrets(text: str) -> list[tuple[int, str]]:
    """(line number, rule) for every match."""
    return [
        (number, rule)
        for number, line in enumerate(text.splitlines(), start=1)
        for rule, pattern in RULES.items()
        if pattern.search(line)
    ]


def tracked_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, timeout=30
    ).stdout.decode("utf-8")
    return [root / name for name in output.split("\0") if name]


def main(root: Path = REPO_ROOT) -> int:
    findings = 0
    files = [p for p in tracked_files(root) if p.is_file()]
    for path in files:
        for line, rule in find_secrets(path.read_text(encoding="utf-8", errors="ignore")):
            findings += 1
            print(f"{path.relative_to(root).as_posix()}:{line} {rule}")
    print(f"{findings} possible secret(s) in {len(files)} tracked files")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

"""Environment for child processes (terraform, infracost)."""

import os

# INFRACOST_* too: terraform must not see the key, and a stray variable must
# not change how infracost behaves (e.g. enable Infracost Cloud uploads).
STRIPPED_PREFIXES = ("AWS_", "ARM_", "AZURE_", "INFRACOST_")


def stripped_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(STRIPPED_PREFIXES)
    }

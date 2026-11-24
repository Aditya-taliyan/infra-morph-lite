"""Golden-file test: demo4_azure must generate exactly the reviewed Terraform.

If a change to the output is intended, regenerate the files, review the diff
by eye, and commit them together with the change.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.generators.terraform_generator import FILES, TerraformGenerator
from app.providers.azure import AzureProvider
from tests.helpers import FIXTURES, load_spec

GOLDEN = FIXTURES / "golden" / "demo4_azure"


def generated() -> dict[str, str]:
    return TerraformGenerator("azure").render(
        AzureProvider().build_context(load_spec("demo4_azure"))
    )


@pytest.mark.parametrize("filename", FILES)
def test_demo4_azure_matches_golden_file(filename):
    # read_text normalises CRLF, so a Windows checkout with autocrlf still matches.
    assert generated()[filename] == (GOLDEN / filename).read_text(encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("terraform") is None,
    reason="terraform is not on PATH; install it to check the golden files with terraform fmt",
)
def test_golden_files_are_terraform_fmt_clean(tmp_path: Path):
    TerraformGenerator.write(generated(), tmp_path)
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-diff", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

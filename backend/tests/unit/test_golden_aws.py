"""Golden-file test: demo2 must generate exactly the reviewed Terraform.

If a change to the output is intended, regenerate the files, review the diff
by eye, and commit them together with the change.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.generators.terraform_generator import FILES, TerraformGenerator
from app.providers.aws import AWSProvider
from tests.helpers import FIXTURES, load_spec

GOLDEN = FIXTURES / "golden" / "demo2"


def generated() -> dict[str, str]:
    return TerraformGenerator("aws").render(AWSProvider().build_context(load_spec("demo2")))


@pytest.mark.parametrize("filename", FILES)
def test_demo2_matches_golden_file(filename):
    # read_text normalises CRLF, so a Windows checkout with autocrlf still matches.
    assert generated()[filename] == (GOLDEN / filename).read_text(encoding="utf-8")


@pytest.mark.skipif(
    shutil.which("terraform") is None,
    reason="terraform is not on PATH; install it to check the golden files with terraform fmt",
)
def test_golden_files_are_terraform_fmt_clean(tmp_path: Path):
    # Checked on an LF copy (equal to the golden files, see above), so a CRLF
    # checkout on Windows cannot make fmt fail.
    TerraformGenerator.write(generated(), tmp_path)
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-diff", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

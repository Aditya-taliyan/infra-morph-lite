import shutil
import subprocess

import pytest

from scripts.check_secrets import REPO_ROOT, find_secrets, main

# Built by concatenation so this file does not trip any scanner.
SECRETS = {
    "aws-access-key": "AK" + "IA" + "ABCDEFGH12345678",
    "private-key": "-----BEGIN RSA PRIVATE" + " KEY-----",
    "infracost-key": "INFRACOST_API_KEY" + "=ico-" + "abcdefgh12345678",
    "generic-api-key": "api_key" + ' = "' + "abcd1234efgh5678ijkl" + '"',
}


@pytest.mark.skipif(
    not shutil.which("git") or not (REPO_ROOT / ".git").exists(), reason="not a git checkout"
)
def test_repo_has_no_secrets(capsys):
    assert main() == 0, capsys.readouterr().out


@pytest.mark.parametrize("rule", SECRETS)
def test_each_secret_is_found(rule):
    assert (2, rule) in find_secrets(f"first line\n{SECRETS[rule]}\n")


@pytest.mark.parametrize("line", [
    "INFRACOST_API_KEY=",
    'KEY = "ico-test"',
    "api_key=config.infracost_api_key.get_secret_value()",
])
def test_placeholders_are_not_reported(line):
    assert find_secrets(line) == []


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_main_fails_without_printing_the_value(tmp_path, capsys):
    (tmp_path / "leak.txt").write_text(SECRETS["aws-access-key"])
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=30)
    subprocess.run(["git", "add", "leak.txt"], cwd=tmp_path, check=True, timeout=30)

    assert main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "leak.txt:1 aws-access-key" in out
    assert SECRETS["aws-access-key"] not in out

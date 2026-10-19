"""Runs the real terraform binary. Excluded from "pytest -q" by pytest.ini; run with "pytest -m integration".

The first run needs internet to install the AWS provider into the shared
data directory backend/.terraform-shared/; later runs reuse it.
"""

import shutil
from pathlib import Path

import pytest

from app.domain.terraform import CommandStatus
from app.services.generation import InfrastructureGenerationService
from tests.helpers import load_spec

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("terraform") is None,
        reason="terraform is not installed (not found on PATH)",
    ),
]


def test_demo2_plans_eleven_resources(tmp_path: Path):
    result = InfrastructureGenerationService().run_pipeline(
        load_spec("demo2"), generated_root=tmp_path
    )

    failures = [s for s in result.steps if s.status is not CommandStatus.PASSED]
    assert not failures, "\n".join(f"{s.step}: {s.stderr or s.stdout}" for s in failures)
    assert [s.step for s in result.steps] == [
        "terraform init", "terraform fmt", "terraform validate", "terraform plan", "terraform show"
    ]
    assert result.plan_summary is not None
    assert result.plan_summary.to_add == 11
    assert result.plan_summary.to_change == 0
    assert result.plan_summary.to_destroy == 0
    assert "aws_instance.web_server" in result.plan_summary.resource_addresses

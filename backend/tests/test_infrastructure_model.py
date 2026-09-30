import pytest
from pydantic import ValidationError

from app.models.infrastructure import InfrastructureSpec


def test_infrastructure_spec_accepts_name():
    spec = InfrastructureSpec(name="demo")

    assert spec.name == "demo"


def test_infrastructure_spec_requires_name():
    with pytest.raises(ValidationError):
        InfrastructureSpec()
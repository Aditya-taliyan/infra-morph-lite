import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.enums import Provider, Region
from app.domain.resources import (
    ComputeInstance,
    SecurityGroup,
    StorageBucket,
    VirtualNetwork,
)
from app.domain.spec import InfrastructureSpecification

FIXTURES = Path(__file__).parent.parent / "fixtures" / "specs"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["demo1", "demo2", "demo5"])
def test_example_specs_parse(name):
    spec = InfrastructureSpecification.model_validate(load_fixture(name))

    assert spec.schema_version == "1"
    assert spec.provider is Provider.AWS


def test_discriminator_selects_resource_class():
    spec = InfrastructureSpecification.model_validate(load_fixture("demo5"))

    assert [type(r) for r in spec.resources] == [
        VirtualNetwork,
        SecurityGroup,
        SecurityGroup,
        ComputeInstance,
        ComputeInstance,
        StorageBucket,
    ]


def test_defaults_are_applied():
    data = load_fixture("demo1")
    del data["tags"]
    del data["resources"][1]["rules"]

    spec = InfrastructureSpecification.model_validate(data)

    assert spec.tags == {}
    assert spec.resources[1].rules == []


def test_region_is_not_checked_against_provider():
    # aws + an Azure region is a semantic error (E03), not a schema error.
    data = load_fixture("demo1")
    data["region"] = "centralindia"

    spec = InfrastructureSpecification.model_validate(data)

    assert spec.region is Region.CENTRALINDIA


def invalid(mutate) -> dict:
    data = copy.deepcopy(load_fixture("demo1"))
    mutate(data)
    return data


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("name"), id="missing-name"),
        pytest.param(lambda d: d.pop("provider"), id="missing-provider"),
        pytest.param(lambda d: d.pop("resources"), id="missing-resources"),
        pytest.param(lambda d: d.update(resources=[]), id="empty-resources"),
        pytest.param(lambda d: d.update(schema_version="2"), id="bad-schema-version"),
        pytest.param(lambda d: d.update(provider="gcp"), id="unknown-provider"),
        pytest.param(lambda d: d.update(region="eu-west-1"), id="unknown-region"),
        pytest.param(lambda d: d.update(environment="test"), id="unknown-environment"),
        pytest.param(lambda d: d.update(name="Basic_Web"), id="bad-name-format"),
        pytest.param(lambda d: d.update(name="ab"), id="name-too-short"),
        pytest.param(lambda d: d.update(name="a" * 33), id="name-too-long"),
        pytest.param(lambda d: d.update(owner="me"), id="unknown-extra-field"),
        pytest.param(
            lambda d: d.update(tags={f"k{i}": "v" for i in range(21)}),
            id="too-many-tags",
        ),
        pytest.param(lambda d: d.update(tags={"k": 1}), id="non-string-tag"),
    ],
)
def test_invalid_top_level_raises(mutate):
    with pytest.raises(ValidationError):
        InfrastructureSpecification.model_validate(invalid(mutate))

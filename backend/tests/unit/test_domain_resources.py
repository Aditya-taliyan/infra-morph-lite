import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.resources import (
    ComputeInstance,
    Resource,
    SecurityRule,
    Subnet,
    VirtualNetwork,
)

resource_adapter = TypeAdapter(Resource)


def network(**overrides) -> dict:
    data = {
        "kind": "VirtualNetwork",
        "name": "main-vpc",
        "cidr": "10.0.0.0/16",
        "subnets": [{"name": "public-a", "cidr": "10.0.1.0/24", "public": True}],
    }
    data.update(overrides)
    return data


def rule(**overrides) -> dict:
    data = {
        "direction": "inbound",
        "protocol": "tcp",
        "from_port": 80,
        "to_port": 80,
        "cidr": "0.0.0.0/0",
    }
    data.update(overrides)
    return data


def instance(**overrides) -> dict:
    data = {
        "kind": "ComputeInstance",
        "name": "web-server",
        "size": "small",
        "os": "ubuntu",
        "subnet": "main-vpc.public-a",
    }
    data.update(overrides)
    return data


def error_types(exc_info) -> set[str]:
    return {e["type"] for e in exc_info.value.errors()}


# --- discriminated union -------------------------------------------------


def test_kind_selects_model():
    assert isinstance(resource_adapter.validate_python(network()), VirtualNetwork)
    assert isinstance(resource_adapter.validate_python(instance()), ComputeInstance)


def test_unknown_kind_raises():
    with pytest.raises(ValidationError) as exc_info:
        resource_adapter.validate_python({"kind": "Database", "name": "main-db"})

    assert error_types(exc_info) == {"union_tag_invalid"}


def test_missing_kind_raises():
    data = network()
    del data["kind"]

    with pytest.raises(ValidationError) as exc_info:
        resource_adapter.validate_python(data)

    assert error_types(exc_info) == {"union_tag_not_found"}


def test_fields_of_another_kind_are_rejected():
    # A VirtualNetwork with a ComputeInstance field: extra="forbid" catches it.
    with pytest.raises(ValidationError) as exc_info:
        resource_adapter.validate_python(network(size="small"))

    assert error_types(exc_info) == {"extra_forbidden"}


# --- extra fields / required fields ---------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(network(region="us-east-1"), id="network"),
        pytest.param(instance(ami="ami-123"), id="instance"),
        pytest.param(
            {"kind": "StorageBucket", "name": "assets01", "encrypted": True},
            id="bucket",
        ),
    ],
)
def test_unknown_extra_field_raises(data):
    with pytest.raises(ValidationError) as exc_info:
        resource_adapter.validate_python(data)

    assert "extra_forbidden" in error_types(exc_info)


def test_unknown_extra_field_in_subnet_raises():
    with pytest.raises(ValidationError):
        Subnet(name="public-a", cidr="10.0.1.0/24", public=True, zone="a")


@pytest.mark.parametrize("field", ["name", "size", "os", "subnet"])
def test_instance_missing_required_field_raises(field):
    data = instance()
    del data[field]

    with pytest.raises(ValidationError) as exc_info:
        resource_adapter.validate_python(data)

    assert error_types(exc_info) == {"missing"}


@pytest.mark.parametrize("field", ["direction", "protocol", "cidr"])
def test_rule_missing_required_field_raises(field):
    data = rule()
    del data[field]

    with pytest.raises(ValidationError):
        SecurityRule.model_validate(data)


def test_network_requires_at_least_one_subnet():
    with pytest.raises(ValidationError):
        resource_adapter.validate_python(network(subnets=[]))


# --- name formats ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Web-Server", "web_server", "1web", "-web", "ab", "a" * 41, "web.server"],
)
def test_bad_resource_name_raises(name):
    with pytest.raises(ValidationError):
        resource_adapter.validate_python(instance(name=name))


@pytest.mark.parametrize("name", ["abc", "web-server-01", "a" + "b" * 39])
def test_good_resource_name_accepted(name):
    assert resource_adapter.validate_python(instance(name=name)).name == name


@pytest.mark.parametrize("ref", ["main-vpc", "main-vpc.", ".public-a", "a.b.c"])
def test_bad_subnet_ref_raises(ref):
    with pytest.raises(ValidationError):
        resource_adapter.validate_python(instance(subnet=ref))


# --- CIDR syntax ----------------------------------------------------------


@pytest.mark.parametrize(
    "cidr",
    [
        "10.0.0.0",
        "10.0.0/16",
        "256.0.0.0/16",
        "10.0.0.0/33",
        "10.0.0.0/255.255.0.0",
        "::/0",
        "not-a-cidr",
        " 10.0.0.0/16",
    ],
)
def test_bad_cidr_syntax_raises(cidr):
    with pytest.raises(ValidationError):
        SecurityRule.model_validate(rule(cidr=cidr))


def test_host_bits_are_left_to_semantic_validation():
    # 10.0.1.5/24 is valid syntax; E04 rejects it later.
    assert Subnet(name="public-a", cidr="10.0.1.5/24", public=True).cidr == "10.0.1.5/24"


@pytest.mark.parametrize("cidr", ["10.0.0.0/8", "10.0.0.0/15", "10.0.0.0/25"])
def test_network_prefix_outside_16_to_24_raises(cidr):
    with pytest.raises(ValidationError):
        resource_adapter.validate_python(network(cidr=cidr))


# --- ports ----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"from_port": -1},
        {"to_port": 65536},
        {"from_port": 70000, "to_port": 70000},
    ],
)
def test_port_out_of_range_raises(overrides):
    with pytest.raises(ValidationError):
        SecurityRule.model_validate(rule(**overrides))


@pytest.mark.parametrize("port", [0, 65535])
def test_port_bounds_accepted(port):
    assert SecurityRule.model_validate(rule(from_port=port, to_port=port)).to_port == port


def test_port_order_and_presence_are_left_to_semantic_validation():
    # from_port > to_port and icmp-with-ports are E06, not schema errors.
    SecurityRule.model_validate(rule(from_port=90, to_port=80))
    SecurityRule.model_validate(rule(protocol="icmp"))
    SecurityRule.model_validate(rule(protocol="tcp", from_port=None, to_port=None))


def test_description_max_length():
    SecurityRule.model_validate(rule(description="x" * 100))

    with pytest.raises(ValidationError):
        SecurityRule.model_validate(rule(description="x" * 101))


def test_unknown_protocol_raises():
    with pytest.raises(ValidationError):
        SecurityRule.model_validate(rule(protocol="http"))

import copy
import json
from pathlib import Path

import pytest

from app.domain.spec import InfrastructureSpecification
from app.domain.validation import Severity
from app.validators.semantic import validate

FIXTURES = Path(__file__).parent.parent / "fixtures" / "specs"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def spec_from(name: str, mutate=None) -> InfrastructureSpecification:
    """Copy a fixture, apply one change, and parse it through the schema."""
    data = copy.deepcopy(load_fixture(name))
    if mutate:
        mutate(data)
    return InfrastructureSpecification.model_validate(data)


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def resource(data: dict, name: str) -> dict:
    return next(r for r in data["resources"] if r["name"] == name)


# --- fixtures that must pass -----------------------------------------------


@pytest.mark.parametrize("name", ["demo1", "demo5"])
def test_demo_spec_is_valid_without_warnings(name):
    result = validate(spec_from(name))

    assert result.is_valid
    assert result.issues == []


def test_demo2_is_valid_with_only_ssh_warning():
    result = validate(spec_from("demo2"))

    assert result.is_valid
    assert result.errors == []
    assert [w.code for w in result.warnings] == ["W01"]
    assert result.warnings[0].resource_name == "web-sg"


def test_demo5_on_azure_is_valid():
    def to_azure(d):
        d["provider"] = "azure"
        d["region"] = "centralindia"

    assert validate(spec_from("demo5", to_azure)).is_valid


# --- errors ------------------------------------------------------------------


def test_e01_duplicate_resource_name():
    def dup(d):
        resource(d, "app-sg")["name"] = "web-sg"

    result = validate(spec_from("demo5", dup))

    assert "E01" in codes(result)
    assert not result.is_valid


def test_e01_duplicate_subnet_name():
    def dup(d):
        resource(d, "app-vpc")["subnets"][1]["name"] = "public-a"

    assert "E01" in codes(validate(spec_from("demo5", dup)))


def test_e02_missing_subnet_message_is_readable():
    def bad_subnet(d):
        resource(d, "web-server")["subnet"] = "web-vpc.private-b"

    result = validate(spec_from("demo1", bad_subnet))

    assert codes(result) == ["E02"]
    assert result.issues[0].message == (
        "web-server uses subnet 'web-vpc.private-b', which does not exist in network 'web-vpc'"
    )
    assert result.issues[0].resource_name == "web-server"


def test_e02_missing_network():
    def bad_network(d):
        resource(d, "web-sg")["network"] = "other-vpc"

    assert codes(validate(spec_from("demo1", bad_network))) == ["E02"]


def test_e02_reference_of_wrong_kind():
    def wrong_kind(d):
        resource(d, "web-server")["security_groups"] = ["web-vpc"]

    result = validate(spec_from("demo1", wrong_kind))

    assert codes(result) == ["E02"]
    assert "is a VirtualNetwork, not a SecurityGroup" in result.issues[0].message


def test_e03_region_not_allowed_for_provider():
    def azure_region(d):
        d["region"] = "centralindia"

    assert codes(validate(spec_from("demo1", azure_region))) == ["E03"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: resource(d, "web-vpc").update(cidr="10.0.0.5/16"), id="network"),
        pytest.param(
            lambda d: resource(d, "web-vpc")["subnets"][0].update(cidr="10.0.1.5/24"),
            id="subnet",
        ),
        pytest.param(
            lambda d: resource(d, "web-sg")["rules"][0].update(cidr="203.0.113.7/24"),
            id="rule",
        ),
    ],
)
def test_e04_cidr_with_host_bits(mutate):
    result = validate(spec_from("demo1", mutate))

    assert codes(result) == ["E04"]
    assert "did you mean" in result.issues[0].message


def test_e05_subnet_outside_network():
    def outside(d):
        resource(d, "web-vpc")["subnets"][0]["cidr"] = "10.1.1.0/24"

    assert codes(validate(spec_from("demo1", outside))) == ["E05"]


def test_e05_subnets_overlap():
    def overlap(d):
        resource(d, "app-vpc")["subnets"][1]["cidr"] = "10.20.1.0/25"

    assert codes(validate(spec_from("demo5", overlap))) == ["E05"]


def test_e06_from_port_greater_than_to_port():
    def reversed_ports(d):
        resource(d, "web-sg")["rules"][0].update(from_port=90, to_port=80)

    assert codes(validate(spec_from("demo1", reversed_ports))) == ["E06"]


def test_e06_tcp_rule_without_ports():
    def no_ports(d):
        rule = resource(d, "web-sg")["rules"][0]
        del rule["from_port"]
        del rule["to_port"]

    assert codes(validate(spec_from("demo1", no_ports))) == ["E06"]


def test_e06_icmp_rule_with_ports():
    def icmp_ports(d):
        resource(d, "web-sg")["rules"][0]["protocol"] = "icmp"

    assert codes(validate(spec_from("demo1", icmp_ports))) == ["E06"]


def test_e07_security_group_in_other_network():
    def second_network(d):
        d["resources"].insert(
            0,
            {
                "kind": "VirtualNetwork",
                "name": "other-vpc",
                "cidr": "10.30.0.0/16",
                "subnets": [{"name": "public-a", "cidr": "10.30.1.0/24", "public": True}],
            },
        )
        resource(d, "web-server")["subnet"] = "other-vpc.public-a"

    assert codes(validate(spec_from("demo5", second_network))) == ["E07"]


def test_e08_public_ip_in_private_subnet():
    def public_ip(d):
        resource(d, "app-server")["public_ip"] = True

    assert codes(validate(spec_from("demo5", public_ip))) == ["E08"]


def test_e09_bucket_name_with_hyphen():
    def hyphen(d):
        resource(d, "webstackassets01")["name"] = "web-stack-assets"

    assert codes(validate(spec_from("demo5", hyphen))) == ["E09"]


def test_e10_azure_instance_with_two_security_groups():
    def azure_two_sgs(d):
        d["provider"] = "azure"
        d["region"] = "centralindia"
        resource(d, "web-server")["subnet"] = "app-vpc.public-a"
        resource(d, "web-server")["security_groups"] = ["web-sg", "app-sg"]

    assert codes(validate(spec_from("demo5", azure_two_sgs))) == ["E10"]


def test_e10_does_not_apply_to_aws():
    def aws_two_sgs(d):
        resource(d, "web-server")["security_groups"] = ["web-sg", "app-sg"]

    assert validate(spec_from("demo5", aws_two_sgs)).issues == []


@pytest.mark.parametrize(
    "update",
    [
        pytest.param({"schema_version": "2"}, id="schema-version"),
        pytest.param({"resources": []}, id="no-resources"),
    ],
)
def test_e11_document_basics(update):
    # The schema already rejects both, so model_copy (which skips validation)
    # is the only way to build such a spec. E11 is defence in depth.
    spec = spec_from("demo1").model_copy(update=update)

    result = validate(spec)

    assert "E11" in codes(result)
    assert not result.is_valid


# --- warnings ----------------------------------------------------------------


def test_w01_ssh_range_open_to_world():
    def ssh_in_range(d):
        resource(d, "web-sg")["rules"][0].update(from_port=20, to_port=80)

    result = validate(spec_from("demo1", ssh_in_range))

    assert codes(result) == ["W01"]
    assert result.is_valid
    assert result.issues[0].severity is Severity.WARNING


def test_w01_not_raised_for_restricted_ssh():
    # demo5 allows SSH only from 203.0.113.0/24.
    assert "W01" not in codes(validate(spec_from("demo5")))


def test_w02_rdp_open_to_world():
    def rdp(d):
        resource(d, "web-sg")["rules"][0].update(from_port=3389, to_port=3389)

    assert codes(validate(spec_from("demo1", rdp))) == ["W02"]


def test_w03_protocol_all_open_to_world():
    def all_protocols(d):
        rule = resource(d, "web-sg")["rules"][0]
        rule["protocol"] = "all"
        del rule["from_port"]
        del rule["to_port"]

    assert codes(validate(spec_from("demo1", all_protocols))) == ["W03"]


def test_w03_full_port_range_open_to_world():
    def full_range(d):
        resource(d, "web-sg")["rules"][0].update(from_port=0, to_port=65535)

    # 0-65535 also contains 22 and 3389, so W01 and W02 fire too.
    assert codes(validate(spec_from("demo1", full_range))) == ["W01", "W02", "W03"]


def test_w03_outbound_all_is_not_a_warning():
    # demo2 has an outbound "all" to 0.0.0.0/0; only inbound rules are checked.
    assert "W03" not in codes(validate(spec_from("demo2")))


def test_w04_public_bucket():
    def public(d):
        resource(d, "webstackassets01")["public_access"] = True

    assert codes(validate(spec_from("demo5", public))) == ["W04"]


def test_w05_instance_without_security_groups():
    def no_sgs(d):
        resource(d, "web-server")["security_groups"] = []

    assert codes(validate(spec_from("demo1", no_sgs))) == ["W05"]


def test_w06_prod_bucket_without_versioning():
    def prod_no_versioning(d):
        d["environment"] = "prod"
        resource(d, "webstackassets01")["versioning"] = False

    assert codes(validate(spec_from("demo5", prod_no_versioning))) == ["W06"]


def test_w06_not_raised_outside_prod():
    def no_versioning(d):
        resource(d, "webstackassets01")["versioning"] = False

    assert validate(spec_from("demo5", no_versioning)).issues == []


# --- several problems at once ----------------------------------------------


def test_all_problems_are_collected():
    def many_problems(d):
        d["region"] = "eastus"  # E03
        d["environment"] = "prod"
        resource(d, "app-vpc")["subnets"][1]["cidr"] = "10.20.1.0/25"  # E05
        resource(d, "web-sg")["rules"][0].update(from_port=90, to_port=80)  # E06
        resource(d, "app-server").update(public_ip=True, security_groups=[])  # E08, W05
        resource(d, "webstackassets01").update(
            name="web-assets", versioning=False, public_access=True
        )  # E09, W04, W06
        resource(d, "web-sg")["rules"][2]["cidr"] = "0.0.0.0/0"  # W01

    result = validate(spec_from("demo5", many_problems))

    assert not result.is_valid
    assert codes(result) == ["E03", "E05", "E06", "E08", "E09", "W01", "W04", "W05", "W06"]
    assert [e.code for e in result.errors] == ["E03", "E05", "E06", "E08", "E09"]
    assert [w.code for w in result.warnings] == ["W01", "W04", "W05", "W06"]

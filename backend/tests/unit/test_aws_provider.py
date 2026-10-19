import pytest

from app.domain.spec import InfrastructureSpecification
from app.providers.aws import EXAMPLE_UBUNTU_AMI_IDS, AWSProvider
from app.providers.base import Block, Expression
from tests.helpers import load_spec


def single(kind_data: dict, *extra: dict) -> InfrastructureSpecification:
    """A spec with one network plus the given resources."""
    return InfrastructureSpecification.model_validate(
        {
            "schema_version": "1",
            "name": "unit-test",
            "provider": "aws",
            "region": "us-east-1",
            "environment": "dev",
            "resources": [kind_data, *extra],
        }
    )


NETWORK = {
    "kind": "VirtualNetwork",
    "name": "main-vpc",
    "cidr": "10.0.0.0/16",
    "subnets": [{"name": "public-a", "cidr": "10.0.1.0/24", "public": True}],
}
PRIVATE_NETWORK = {
    "kind": "VirtualNetwork",
    "name": "private-vpc",
    "cidr": "10.1.0.0/16",
    "subnets": [
        {"name": "private-a", "cidr": "10.1.1.0/24", "public": False},
        {"name": "private-b", "cidr": "10.1.2.0/24", "public": False},
    ],
}
GROUP = {
    "kind": "SecurityGroup",
    "name": "web-sg",
    "network": "main-vpc",
    "rules": [
        {"direction": "inbound", "protocol": "tcp", "from_port": 80, "to_port": 80,
         "cidr": "0.0.0.0/0", "description": "HTTP"},
    ],
}
INSTANCE = {
    "kind": "ComputeInstance",
    "name": "web-server",
    "size": "small",
    "os": "ubuntu",
    "subnet": "main-vpc.public-a",
    "security_groups": ["web-sg"],
    "public_ip": True,
}
BUCKET = {"kind": "StorageBucket", "name": "assets01", "versioning": True}


def build(spec):
    return AWSProvider().build_context(spec)


def types(context) -> list[str]:
    return [r.type for r in context.resources]


def by_address(context, address: str):
    return next(r for r in context.resources if r.address == address)


# --- resource types per kind ------------------------------------------------


def test_virtual_network_with_public_subnet_resource_types():
    assert types(build(single(NETWORK))) == [
        "aws_vpc",
        "aws_subnet",
        "aws_internet_gateway",
        "aws_route_table",
        "aws_route_table_association",
    ]


def test_security_group_resource_types():
    assert types(build(single(NETWORK, GROUP)))[5:] == [
        "aws_security_group",
        "aws_vpc_security_group_ingress_rule",
        "aws_vpc_security_group_egress_rule",
    ]


def test_compute_instance_resource_types():
    assert types(build(single(NETWORK, GROUP, INSTANCE)))[8:] == ["aws_instance"]


def test_storage_bucket_resource_types():
    assert types(build(single(BUCKET))) == [
        "aws_s3_bucket",
        "aws_s3_bucket_versioning",
        "aws_s3_bucket_public_access_block",
    ]


def test_demo5_resource_counts_match_ir_schema():
    counts: dict[str, int] = {}
    for t in types(build(load_spec("demo5"))):
        counts[t] = counts.get(t, 0) + 1
    assert counts == {
        "aws_vpc": 1,
        "aws_subnet": 2,
        "aws_internet_gateway": 1,
        "aws_route_table": 1,
        "aws_route_table_association": 1,
        "aws_security_group": 2,
        "aws_vpc_security_group_ingress_rule": 4,
        "aws_vpc_security_group_egress_rule": 2,
        "aws_instance": 2,
        "aws_s3_bucket": 1,
        "aws_s3_bucket_versioning": 1,
        "aws_s3_bucket_public_access_block": 1,
    }


# --- networking ---------------------------------------------------------------


def test_public_subnet_gets_igw_route_and_association():
    context = build(single(NETWORK))
    route_table = by_address(context, "aws_route_table.main_vpc_public")
    assert route_table.attributes["route"] == Block(
        "route",
        {"cidr_block": "0.0.0.0/0", "gateway_id": Expression("aws_internet_gateway.main_vpc.id")},
    )
    association = by_address(context, "aws_route_table_association.main_vpc_public_a")
    assert association.attributes == {
        "subnet_id": Expression("aws_subnet.main_vpc_public_a.id"),
        "route_table_id": Expression("aws_route_table.main_vpc_public.id"),
    }


def test_private_only_network_has_no_igw_route_table_or_association():
    assert types(build(single(PRIVATE_NETWORK))) == ["aws_vpc", "aws_subnet", "aws_subnet"]


def test_only_public_subnets_get_an_association():
    context = build(load_spec("demo5"))
    associations = [r.label for r in context.resources if r.type == "aws_route_table_association"]
    assert associations == ["app_vpc_public_a"]


# --- security groups ----------------------------------------------------------


def test_group_without_outbound_rules_gets_default_egress():
    rule = by_address(build(single(NETWORK, GROUP)), "aws_vpc_security_group_egress_rule.web_sg_egress_default")
    assert rule.attributes["ip_protocol"] == "-1"
    assert rule.attributes["cidr_ipv4"] == "0.0.0.0/0"
    assert "from_port" not in rule.attributes


def test_group_with_outbound_rule_gets_no_default_egress():
    context = build(load_spec("demo2"))
    egress = [r.label for r in context.resources if r.type == "aws_vpc_security_group_egress_rule"]
    assert egress == ["web_sg_egress_1"]


def test_rule_attributes_per_protocol():
    group = {
        **GROUP,
        "rules": [
            {"direction": "inbound", "protocol": "udp", "from_port": 53, "to_port": 53, "cidr": "10.0.0.0/8"},
            {"direction": "inbound", "protocol": "icmp", "cidr": "10.0.0.0/8"},
            {"direction": "outbound", "protocol": "all", "cidr": "0.0.0.0/0"},
        ],
    }
    context = build(single(NETWORK, group))
    udp = by_address(context, "aws_vpc_security_group_ingress_rule.web_sg_ingress_1").attributes
    icmp = by_address(context, "aws_vpc_security_group_ingress_rule.web_sg_ingress_2").attributes
    all_ = by_address(context, "aws_vpc_security_group_egress_rule.web_sg_egress_1").attributes
    assert (udp["ip_protocol"], udp["from_port"], udp["to_port"]) == ("udp", 53, 53)
    assert (icmp["ip_protocol"], icmp["from_port"], icmp["to_port"]) == ("icmp", -1, -1)
    assert all_["ip_protocol"] == "-1" and "from_port" not in all_


# --- references ---------------------------------------------------------------


def test_references_become_terraform_expressions():
    context = build(load_spec("demo5"))
    assert by_address(context, "aws_subnet.app_vpc_public_a").attributes["vpc_id"] == Expression(
        "aws_vpc.app_vpc.id"
    )
    assert by_address(context, "aws_security_group.app_sg").attributes["vpc_id"] == Expression(
        "aws_vpc.app_vpc.id"
    )
    rule = by_address(context, "aws_vpc_security_group_ingress_rule.app_sg_ingress_1")
    assert rule.attributes["security_group_id"] == Expression("aws_security_group.app_sg.id")

    app = by_address(context, "aws_instance.app_server").attributes
    assert app["subnet_id"] == Expression("aws_subnet.app_vpc_private_a.id")
    assert app["vpc_security_group_ids"] == [Expression("aws_security_group.app_sg.id")]
    assert by_address(context, "aws_s3_bucket_versioning.webstackassets01").attributes[
        "bucket"
    ] == Expression("aws_s3_bucket.webstackassets01.id")


def test_hyphenated_names_become_underscore_labels():
    labels = {r.label for r in build(load_spec("demo5")).resources}
    assert {"app_vpc", "app_vpc_private_a", "web_server", "app_server", "web_sg"} <= labels
    assert not any("-" in label for label in labels)


def test_colliding_labels_are_rejected():
    spec = single(
        {"kind": "VirtualNetwork", "name": "net-abc", "cidr": "10.0.0.0/16",
         "subnets": [{"name": "def", "cidr": "10.0.1.0/24", "public": False}]},
        {"kind": "VirtualNetwork", "name": "net", "cidr": "10.1.0.0/16",
         "subnets": [{"name": "abc-def", "cidr": "10.1.1.0/24", "public": False}]},
    )
    with pytest.raises(ValueError, match="aws_subnet.net_abc_def"):
        build(spec)


# --- compute ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "instance_type"), [("small", "t3.micro"), ("medium", "t3.small"), ("large", "t3.medium")]
)
def test_instance_type_follows_size_table(size, instance_type):
    context = build(single(NETWORK, GROUP, {**INSTANCE, "size": size}))
    assert by_address(context, "aws_instance.web_server").attributes["instance_type"] == instance_type


def test_ami_comes_from_region_map_with_override_variable():
    context = build(load_spec("demo2"))  # ap-south-1
    instance = by_address(context, "aws_instance.web_server").attributes
    assert instance["ami"] == Expression('local.ubuntu_ami_ids["ap-south-1"]')

    example, merged = context.locals
    assert example.value == {str(r): ami for r, ami in sorted(EXAMPLE_UBUNTU_AMI_IDS.items())}
    assert set(example.value) == {"us-east-1", "ap-south-1"}
    assert "replace with a current Ubuntu 22.04 AMI" in example.comment
    assert merged.value == Expression("merge(local.example_ubuntu_ami_ids, var.ubuntu_ami_overrides)")
    assert [(v.name, v.type, v.default) for v in context.variables] == [
        ("ubuntu_ami_overrides", "map(string)", {})
    ]


def test_no_instances_means_no_ami_locals_or_variables():
    context = build(single(NETWORK, BUCKET))
    assert context.locals == [] and context.variables == []


def test_instance_without_security_groups_omits_the_attribute():
    context = build(single(NETWORK, {**INSTANCE, "security_groups": [], "public_ip": False}))
    instance = by_address(context, "aws_instance.web_server").attributes
    assert "vpc_security_group_ids" not in instance
    assert instance["associate_public_ip_address"] is False


# --- storage ------------------------------------------------------------------


@pytest.mark.parametrize(("versioning", "status"), [(True, "Enabled"), (False, "Disabled")])
def test_bucket_versioning_status(versioning, status):
    context = build(single({**BUCKET, "versioning": versioning}))
    block = by_address(context, "aws_s3_bucket_versioning.assets01").attributes["versioning_configuration"]
    assert block == Block("versioning_configuration", {"status": status})


@pytest.mark.parametrize("public", [True, False])
def test_public_access_block_follows_public_access(public):
    context = build(single({**BUCKET, "public_access": public}))
    attributes = by_address(context, "aws_s3_bucket_public_access_block.assets01").attributes
    flags = {k: v for k, v in attributes.items() if k != "bucket"}
    assert flags == dict.fromkeys(
        ["block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"],
        not public,
    )


# --- tags, provider, outputs ------------------------------------------------


UNTAGGABLE = {
    "aws_route_table_association",
    "aws_s3_bucket_versioning",
    "aws_s3_bucket_public_access_block",
}


def test_every_taggable_resource_has_spec_and_generated_tags():
    context = build(load_spec("demo5"))
    for r in context.resources:
        if r.type in UNTAGGABLE:
            assert "tags" not in r.attributes, r.address
            continue
        tags = r.attributes["tags"]
        assert tags["ManagedBy"] == "inframorph", r.address
        assert tags["Environment"] == "staging", r.address
        assert tags["Owner"] == "student" and tags["Project"] == "inframorph-demo", r.address
        assert tags["Name"], r.address
        assert list(tags) == sorted(tags), r.address


def test_generated_tags_win_over_spec_tags():
    spec = load_spec("demo1", lambda d: d["tags"].update({"ManagedBy": "someone", "Name": "x"}))
    tags = by_address(build(spec), "aws_vpc.web_vpc").attributes["tags"]
    assert tags["ManagedBy"] == "inframorph" and tags["Name"] == "web-vpc"


def test_provider_config_uses_spec_region():
    provider = build(load_spec("demo2")).provider
    assert (provider.source, provider.version, provider.settings) == (
        "hashicorp/aws",
        "~> 5.0",
        {"region": "ap-south-1"},
    )


def test_outputs_per_section_9():
    outputs = {o.name: o.value for o in build(load_spec("demo5")).outputs}
    assert outputs == {
        "app_vpc_vpc_id": Expression("aws_vpc.app_vpc.id"),
        "app_vpc_public_a_subnet_id": Expression("aws_subnet.app_vpc_public_a.id"),
        "app_vpc_private_a_subnet_id": Expression("aws_subnet.app_vpc_private_a.id"),
        "web_server_private_ip": Expression("aws_instance.web_server.private_ip"),
        "web_server_public_ip": Expression("aws_instance.web_server.public_ip"),
        "app_server_private_ip": Expression("aws_instance.app_server.private_ip"),
        "webstackassets01_bucket_name": Expression("aws_s3_bucket.webstackassets01.bucket"),
    }


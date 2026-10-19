"""AWS adapter: IR -> Terraform resources (docs/IR_SCHEMA.md §6, §7, §9).

Resources come out in IR order, and each IR resource expands in a fixed order,
so the same spec always gives the same context.
"""

from typing import Any

from app.domain.enums import Direction, InstanceSize, Protocol, Region
from app.domain.resources import (
    ComputeInstance,
    SecurityGroup,
    SecurityRule,
    StorageBucket,
    VirtualNetwork,
)
from app.domain.spec import InfrastructureSpecification
from app.providers.base import (
    Block,
    Expression,
    Local,
    Output,
    ProviderAdapter,
    ProviderConfig,
    RenderContext,
    TerraformResource,
    Variable,
    terraform_label,
)

INSTANCE_TYPES: dict[InstanceSize, str] = {
    InstanceSize.SMALL: "t3.micro",
    InstanceSize.MEDIUM: "t3.small",
    InstanceSize.LARGE: "t3.medium",
}

# EXAMPLE IDs, not verified. terraform plan does not check that an AMI exists,
# so these are safe for this project; a real deployment must replace them.
EXAMPLE_UBUNTU_AMI_IDS: dict[Region, str] = {
    Region.US_EAST_1: "ami-0123456789abcdef0",
    Region.AP_SOUTH_1: "ami-0fedcba9876543210",
}

AMI_COMMENT = (
    "EXAMPLE AMI IDs: replace with a current Ubuntu 22.04 AMI before any real "
    "deployment, or set var.ubuntu_ami_overrides."
)

_IP_PROTOCOLS: dict[Protocol, str] = {
    Protocol.TCP: "tcp",
    Protocol.UDP: "udp",
    Protocol.ICMP: "icmp",
    Protocol.ALL: "-1",
}

_ANYWHERE = "0.0.0.0/0"


def _ref(resource_type: str, label: str, attribute: str = "id") -> Expression:
    return Expression(f"{resource_type}.{label}.{attribute}")


class AWSProvider(ProviderAdapter):
    template_dir = "aws"

    def build_context(self, spec: InfrastructureSpecification) -> RenderContext:
        self._spec = spec
        resources: list[TerraformResource] = []
        outputs: list[Output] = []

        for item in spec.resources:
            if isinstance(item, VirtualNetwork):
                resources += self._network(item)
                outputs += self._network_outputs(item)
            elif isinstance(item, SecurityGroup):
                resources += self._security_group(item)
            elif isinstance(item, ComputeInstance):
                resources.append(self._instance(item))
                outputs += self._instance_outputs(item)
            elif isinstance(item, StorageBucket):
                resources += self._bucket(item)
                outputs += self._bucket_outputs(item)

        has_instances = any(isinstance(r, ComputeInstance) for r in spec.resources)
        context = RenderContext(
            provider=ProviderConfig(
                name="aws",
                source="hashicorp/aws",
                version="~> 5.0",
                settings={"region": str(spec.region)},
            ),
            locals=self._ami_locals() if has_instances else [],
            variables=[self._ami_variable()] if has_instances else [],
            resources=resources,
            outputs=outputs,
        )
        self.check_unique_addresses(context)
        return context

    # --- shared -------------------------------------------------------------

    def _tags(self, name: str) -> dict[str, str]:
        # The generator's tags win over spec tags with the same key.
        tags = dict(self._spec.tags)
        tags.update(
            {
                "Name": name,
                "Environment": str(self._spec.environment),
                "ManagedBy": "inframorph",
            }
        )
        return dict(sorted(tags.items()))

    # --- VirtualNetwork -----------------------------------------------------

    def _network(self, network: VirtualNetwork) -> list[TerraformResource]:
        vpc = terraform_label(network.name)
        resources = [
            TerraformResource(
                "aws_vpc",
                vpc,
                {
                    "cidr_block": network.cidr,
                    "enable_dns_support": True,
                    "enable_dns_hostnames": True,
                    "tags": self._tags(network.name),
                },
            )
        ]
        for subnet in network.subnets:
            resources.append(
                TerraformResource(
                    "aws_subnet",
                    terraform_label(network.name, subnet.name),
                    {
                        "vpc_id": _ref("aws_vpc", vpc),
                        "cidr_block": subnet.cidr,
                        # Public IPs are decided per instance (public_ip).
                        "map_public_ip_on_launch": False,
                        "tags": self._tags(f"{network.name}-{subnet.name}"),
                    },
                )
            )

        public_subnets = [s for s in network.subnets if s.public]
        if not public_subnets:
            return resources

        route_table = terraform_label(network.name, "public")
        resources.append(
            TerraformResource(
                "aws_internet_gateway",
                vpc,
                {
                    "vpc_id": _ref("aws_vpc", vpc),
                    "tags": self._tags(f"{network.name}-igw"),
                },
            )
        )
        resources.append(
            TerraformResource(
                "aws_route_table",
                route_table,
                {
                    "vpc_id": _ref("aws_vpc", vpc),
                    "route": Block(
                        "route",
                        {
                            "cidr_block": _ANYWHERE,
                            "gateway_id": _ref("aws_internet_gateway", vpc),
                        },
                    ),
                    "tags": self._tags(f"{network.name}-public"),
                },
            )
        )
        for subnet in public_subnets:
            label = terraform_label(network.name, subnet.name)
            resources.append(
                TerraformResource(
                    "aws_route_table_association",
                    label,
                    {
                        "subnet_id": _ref("aws_subnet", label),
                        "route_table_id": _ref("aws_route_table", route_table),
                    },
                )
            )
        return resources

    def _network_outputs(self, network: VirtualNetwork) -> list[Output]:
        vpc = terraform_label(network.name)
        outputs = [Output(f"{vpc}_vpc_id", _ref("aws_vpc", vpc), f"ID of VPC {network.name}")]
        for subnet in network.subnets:
            label = terraform_label(network.name, subnet.name)
            outputs.append(
                Output(
                    f"{label}_subnet_id",
                    _ref("aws_subnet", label),
                    f"ID of subnet {network.name}.{subnet.name}",
                )
            )
        return outputs

    # --- SecurityGroup ------------------------------------------------------

    def _security_group(self, group: SecurityGroup) -> list[TerraformResource]:
        sg = terraform_label(group.name)
        resources = [
            TerraformResource(
                "aws_security_group",
                sg,
                {
                    "name": group.name,
                    "description": f"Security group {group.name} (managed by InfraMorph)",
                    "vpc_id": _ref("aws_vpc", terraform_label(group.network)),
                    "tags": self._tags(group.name),
                },
            )
        ]

        counters = {Direction.INBOUND: 0, Direction.OUTBOUND: 0}
        for rule in group.rules:
            counters[rule.direction] += 1
            resources.append(self._rule(group, rule, str(counters[rule.direction])))

        if counters[Direction.OUTBOUND] == 0:
            # Terraform removes AWS's implicit allow-all egress, so add it back.
            default = SecurityRule(
                direction=Direction.OUTBOUND,
                protocol=Protocol.ALL,
                cidr=_ANYWHERE,
                description="Allow all outbound traffic (default)",
            )
            resources.append(self._rule(group, default, "default"))
        return resources

    def _rule(self, group: SecurityGroup, rule: SecurityRule, suffix: str) -> TerraformResource:
        inbound = rule.direction is Direction.INBOUND
        kind = "ingress" if inbound else "egress"
        attributes: dict[str, Any] = {
            "security_group_id": _ref("aws_security_group", terraform_label(group.name)),
            "ip_protocol": _IP_PROTOCOLS[rule.protocol],
            "cidr_ipv4": rule.cidr,
        }
        if rule.protocol in (Protocol.TCP, Protocol.UDP):
            attributes["from_port"] = rule.from_port
            attributes["to_port"] = rule.to_port
        elif rule.protocol is Protocol.ICMP:
            # For icmp these are ICMP type and code; -1 means all of them.
            attributes["from_port"] = -1
            attributes["to_port"] = -1
        if rule.description:
            attributes["description"] = rule.description
        attributes["tags"] = self._tags(f"{group.name}-{kind}-{suffix}")
        return TerraformResource(
            f"aws_vpc_security_group_{kind}_rule",
            terraform_label(group.name, kind, suffix),
            attributes,
        )

    # --- ComputeInstance ----------------------------------------------------

    @staticmethod
    def _ami_locals() -> list[Local]:
        return [
            Local(
                "example_ubuntu_ami_ids",
                {str(region): ami for region, ami in sorted(EXAMPLE_UBUNTU_AMI_IDS.items())},
                AMI_COMMENT,
            ),
            Local(
                "ubuntu_ami_ids",
                Expression("merge(local.example_ubuntu_ami_ids, var.ubuntu_ami_overrides)"),
            ),
        ]

    @staticmethod
    def _ami_variable() -> Variable:
        return Variable(
            "ubuntu_ami_overrides",
            "map(string)",
            "Optional Ubuntu 22.04 AMI IDs by region; overrides the example IDs in locals",
            {},
        )

    def _instance(self, instance: ComputeInstance) -> TerraformResource:
        network_name, _, subnet_name = instance.subnet.partition(".")
        attributes: dict[str, Any] = {
            "ami": Expression(f'local.ubuntu_ami_ids["{self._spec.region}"]'),
            "instance_type": INSTANCE_TYPES[instance.size],
            "subnet_id": _ref("aws_subnet", terraform_label(network_name, subnet_name)),
        }
        if instance.security_groups:
            attributes["vpc_security_group_ids"] = [
                _ref("aws_security_group", terraform_label(name))
                for name in instance.security_groups
            ]
        attributes["associate_public_ip_address"] = instance.public_ip
        attributes["tags"] = self._tags(instance.name)
        return TerraformResource("aws_instance", terraform_label(instance.name), attributes)

    def _instance_outputs(self, instance: ComputeInstance) -> list[Output]:
        label = terraform_label(instance.name)
        outputs = [
            Output(
                f"{label}_private_ip",
                _ref("aws_instance", label, "private_ip"),
                f"Private IP of {instance.name}",
            )
        ]
        if instance.public_ip:
            outputs.append(
                Output(
                    f"{label}_public_ip",
                    _ref("aws_instance", label, "public_ip"),
                    f"Public IP of {instance.name}",
                )
            )
        return outputs

    # --- StorageBucket ------------------------------------------------------

    def _bucket(self, bucket: StorageBucket) -> list[TerraformResource]:
        label = terraform_label(bucket.name)
        bucket_id = _ref("aws_s3_bucket", label)
        block_public = not bucket.public_access
        return [
            TerraformResource(
                "aws_s3_bucket",
                label,
                {"bucket": bucket.name, "tags": self._tags(bucket.name)},
            ),
            TerraformResource(
                "aws_s3_bucket_versioning",
                label,
                {
                    "bucket": bucket_id,
                    "versioning_configuration": Block(
                        "versioning_configuration",
                        # "Disabled" is the documented value for a new bucket
                        # that was never versioned ("Suspended" is for after).
                        {"status": "Enabled" if bucket.versioning else "Disabled"},
                    ),
                },
            ),
            TerraformResource(
                "aws_s3_bucket_public_access_block",
                label,
                {
                    "bucket": bucket_id,
                    "block_public_acls": block_public,
                    "block_public_policy": block_public,
                    "ignore_public_acls": block_public,
                    "restrict_public_buckets": block_public,
                },
            ),
        ]

    def _bucket_outputs(self, bucket: StorageBucket) -> list[Output]:
        label = terraform_label(bucket.name)
        return [
            Output(
                f"{label}_bucket_name",
                _ref("aws_s3_bucket", label, "bucket"),
                f"Name of bucket {bucket.name}",
            )
        ]


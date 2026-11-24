"""Azure adapter: IR -> Terraform resources (docs/IR_SCHEMA.md §6, §7, §9).

Same rules as the AWS adapter: resources come out in IR order and each IR
resource expands in a fixed order, so the same spec always gives the same
context. Every resource goes into one resource group that the adapter adds;
the group is not an IR resource.
"""

from typing import Any

from app.domain.enums import Direction, InstanceSize, Protocol
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
    Output,
    ProviderAdapter,
    ProviderConfig,
    RenderContext,
    TerraformResource,
    Variable,
    ref,
    resource_tags,
    terraform_label,
)

VM_SIZES: dict[InstanceSize, str] = {
    InstanceSize.SMALL: "Standard_B1s",
    InstanceSize.MEDIUM: "Standard_B2s",
    InstanceSize.LARGE: "Standard_B2ms",
}

UBUNTU_22_04_IMAGE = {
    "publisher": "Canonical",
    "offer": "0001-com-ubuntu-server-jammy",
    "sku": "22_04-lts-gen2",
    "version": "latest",
}

_PROTOCOLS: dict[Protocol, str] = {
    Protocol.TCP: "Tcp",
    Protocol.UDP: "Udp",
    Protocol.ICMP: "Icmp",
    Protocol.ALL: "*",
}

FIRST_PRIORITY = 100
PRIORITY_STEP = 10

RESOURCE_GROUP = "main"
_RG_NAME = ref("azurerm_resource_group", RESOURCE_GROUP, "name")
_RG_LOCATION = ref("azurerm_resource_group", RESOURCE_GROUP, "location")
_ADMIN_USERNAME = Expression("var.admin_username")


class AzureProvider(ProviderAdapter):
    template_dir = "azure"
    offline_plan = False  # plan needs an Azure login (CLAUDE.md: out of scope)

    def build_context(self, spec: InfrastructureSpecification) -> RenderContext:
        self._spec = spec
        resources: list[TerraformResource] = [self._resource_group()]
        outputs: list[Output] = []

        for item in spec.resources:
            if isinstance(item, VirtualNetwork):
                resources += self._network(item)
                outputs += self._network_outputs(item)
            elif isinstance(item, SecurityGroup):
                resources += self._security_group(item)
            elif isinstance(item, ComputeInstance):
                resources += self._instance(item)
                outputs += self._instance_outputs(item)
            elif isinstance(item, StorageBucket):
                resources += self._bucket(item)
                outputs += self._bucket_outputs(item)

        has_instances = any(isinstance(r, ComputeInstance) for r in spec.resources)
        context = RenderContext(
            provider=ProviderConfig(
                name="azurerm", source="hashicorp/azurerm", version="~> 4.0", settings={}
            ),
            variables=self._admin_variables() if has_instances else [],
            resources=resources,
            outputs=outputs,
        )
        self.check_unique_addresses(context)
        return context

    # --- shared -------------------------------------------------------------

    def _tags(self, name: str) -> dict[str, str]:
        return resource_tags(self._spec, name)

    def _placed(self, name: str, **attributes: Any) -> dict[str, Any]:
        """name + resource group + location, then the resource's own attributes."""
        return {
            "name": name,
            "resource_group_name": _RG_NAME,
            "location": _RG_LOCATION,
            **attributes,
        }

    def _resource_group(self) -> TerraformResource:
        name = f"rg-{self._spec.name}-{self._spec.environment}"
        return TerraformResource(
            "azurerm_resource_group",
            RESOURCE_GROUP,
            {"name": name, "location": str(self._spec.region), "tags": self._tags(name)},
        )

    # --- VirtualNetwork -----------------------------------------------------

    def _network(self, network: VirtualNetwork) -> list[TerraformResource]:
        vnet = terraform_label(network.name)
        resources = [
            TerraformResource(
                "azurerm_virtual_network",
                vnet,
                self._placed(
                    network.name,
                    address_space=[network.cidr],
                    tags=self._tags(network.name),
                ),
            )
        ]
        # A "public" subnet has no Azure counterpart; it only allows public IPs
        # on its VMs, which E08 checks (IR_SCHEMA.md §7, known mismatches).
        for subnet in network.subnets:
            resources.append(
                TerraformResource(
                    "azurerm_subnet",
                    terraform_label(network.name, subnet.name),
                    {
                        "name": subnet.name,
                        "resource_group_name": _RG_NAME,
                        "virtual_network_name": ref("azurerm_virtual_network", vnet, "name"),
                        "address_prefixes": [subnet.cidr],
                    },
                )
            )
        return resources

    def _network_outputs(self, network: VirtualNetwork) -> list[Output]:
        vnet = terraform_label(network.name)
        outputs = [
            Output(
                f"{vnet}_vnet_id",
                ref("azurerm_virtual_network", vnet),
                f"ID of virtual network {network.name}",
            )
        ]
        for subnet in network.subnets:
            label = terraform_label(network.name, subnet.name)
            outputs.append(
                Output(
                    f"{label}_subnet_id",
                    ref("azurerm_subnet", label),
                    f"ID of subnet {network.name}.{subnet.name}",
                )
            )
        return outputs

    # --- SecurityGroup ------------------------------------------------------

    def _security_group(self, group: SecurityGroup) -> list[TerraformResource]:
        resources = [
            TerraformResource(
                "azurerm_network_security_group",
                terraform_label(group.name),
                self._placed(group.name, tags=self._tags(group.name)),
            )
        ]
        # One priority sequence across both directions, in rule order. No
        # default egress rule: Azure NSGs already allow outbound traffic.
        counters = {Direction.INBOUND: 0, Direction.OUTBOUND: 0}
        for index, rule in enumerate(group.rules):
            counters[rule.direction] += 1
            priority = FIRST_PRIORITY + index * PRIORITY_STEP
            resources.append(self._rule(group, rule, counters[rule.direction], priority))
        return resources

    def _rule(
        self, group: SecurityGroup, rule: SecurityRule, number: int, priority: int
    ) -> TerraformResource:
        inbound = rule.direction is Direction.INBOUND
        direction = "inbound" if inbound else "outbound"
        if rule.protocol in (Protocol.TCP, Protocol.UDP):
            ports = (
                str(rule.from_port)
                if rule.from_port == rule.to_port
                else f"{rule.from_port}-{rule.to_port}"
            )
        else:
            ports = "*"
        attributes: dict[str, Any] = {
            "name": f"{group.name}-{direction}-{number}",
            "resource_group_name": _RG_NAME,
            "network_security_group_name": ref(
                "azurerm_network_security_group", terraform_label(group.name), "name"
            ),
            "priority": priority,
            "direction": direction.capitalize(),
            "access": "Allow",
            "protocol": _PROTOCOLS[rule.protocol],
            "source_port_range": "*",
            "destination_port_range": ports,
            "source_address_prefix": rule.cidr if inbound else "*",
            "destination_address_prefix": "*" if inbound else rule.cidr,
        }
        if rule.description:
            attributes["description"] = rule.description
        return TerraformResource(
            "azurerm_network_security_rule",
            terraform_label(group.name, direction, str(number)),
            attributes,
        )

    # --- ComputeInstance ----------------------------------------------------

    @staticmethod
    def _admin_variables() -> list[Variable]:
        return [
            Variable("admin_username", "string", "Admin user name on the Linux VMs", "azureuser"),
            # No default: the key is never stored in the IR, the repo or the output.
            Variable(
                "admin_ssh_public_key",
                "string",
                "SSH public key for the VM admin user (password login is disabled)",
            ),
        ]

    def _instance(self, instance: ComputeInstance) -> list[TerraformResource]:
        label = terraform_label(instance.name)
        network_name, _, subnet_name = instance.subnet.partition(".")
        resources: list[TerraformResource] = []

        ip_configuration: dict[str, Any] = {
            "name": "internal",
            "subnet_id": ref("azurerm_subnet", terraform_label(network_name, subnet_name)),
            "private_ip_address_allocation": "Dynamic",
        }
        if instance.public_ip:
            resources.append(
                TerraformResource(
                    "azurerm_public_ip",
                    label,
                    self._placed(
                        f"{instance.name}-ip",
                        allocation_method="Static",
                        sku="Standard",
                        tags=self._tags(f"{instance.name}-ip"),
                    ),
                )
            )
            ip_configuration["public_ip_address_id"] = ref("azurerm_public_ip", label)

        resources.append(
            TerraformResource(
                "azurerm_network_interface",
                label,
                self._placed(
                    f"{instance.name}-nic",
                    ip_configuration=Block("ip_configuration", ip_configuration),
                    tags=self._tags(f"{instance.name}-nic"),
                ),
            )
        )
        resources.append(
            TerraformResource(
                "azurerm_linux_virtual_machine",
                label,
                self._placed(
                    instance.name,
                    size=VM_SIZES[instance.size],
                    admin_username=_ADMIN_USERNAME,
                    disable_password_authentication=True,
                    network_interface_ids=[ref("azurerm_network_interface", label)],
                    admin_ssh_key=Block(
                        "admin_ssh_key",
                        {
                            "username": _ADMIN_USERNAME,
                            "public_key": Expression("var.admin_ssh_public_key"),
                        },
                    ),
                    os_disk=Block(
                        "os_disk",
                        {"caching": "ReadWrite", "storage_account_type": "Standard_LRS"},
                    ),
                    source_image_reference=Block(
                        "source_image_reference", dict(UBUNTU_22_04_IMAGE)
                    ),
                    tags=self._tags(instance.name),
                ),
            )
        )
        # E10 guarantees at most one security group on Azure.
        if instance.security_groups:
            resources.append(
                TerraformResource(
                    "azurerm_network_interface_security_group_association",
                    label,
                    {
                        "network_interface_id": ref("azurerm_network_interface", label),
                        "network_security_group_id": ref(
                            "azurerm_network_security_group",
                            terraform_label(instance.security_groups[0]),
                        ),
                    },
                )
            )
        return resources

    def _instance_outputs(self, instance: ComputeInstance) -> list[Output]:
        label = terraform_label(instance.name)
        outputs = [
            Output(
                f"{label}_private_ip",
                ref("azurerm_linux_virtual_machine", label, "private_ip_address"),
                f"Private IP of {instance.name}",
            )
        ]
        if instance.public_ip:
            outputs.append(
                Output(
                    f"{label}_public_ip",
                    ref("azurerm_public_ip", label, "ip_address"),
                    f"Public IP of {instance.name}",
                )
            )
        return outputs

    # --- StorageBucket ------------------------------------------------------

    def _bucket(self, bucket: StorageBucket) -> list[TerraformResource]:
        label = terraform_label(bucket.name)
        public = bucket.public_access
        return [
            TerraformResource(
                "azurerm_storage_account",
                label,
                self._placed(
                    bucket.name,  # E09 keeps it a valid storage account name
                    account_tier="Standard",
                    account_replication_type="LRS",
                    public_network_access_enabled=public,
                    allow_nested_items_to_be_public=public,
                    blob_properties=Block(
                        "blob_properties", {"versioning_enabled": bucket.versioning}
                    ),
                    tags=self._tags(bucket.name),
                ),
            ),
            TerraformResource(
                "azurerm_storage_container",
                label,
                {
                    "name": bucket.name,
                    # storage_account_id (not _name) goes through the management
                    # API, so it works with public network access disabled.
                    "storage_account_id": ref("azurerm_storage_account", label),
                    "container_access_type": "blob" if public else "private",
                },
            ),
        ]

    def _bucket_outputs(self, bucket: StorageBucket) -> list[Output]:
        label = terraform_label(bucket.name)
        return [
            Output(
                f"{label}_storage_account_name",
                ref("azurerm_storage_account", label, "name"),
                f"Name of storage account {bucket.name}",
            )
        ]

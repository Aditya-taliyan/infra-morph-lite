import pytest

from app.domain.spec import InfrastructureSpecification
from app.generators.terraform_generator import TerraformGenerator
from app.providers.azure import AzureProvider
from app.providers.base import Block, Expression
from tests.helpers import load_spec


def spec_of(*resources: dict) -> InfrastructureSpecification:
    return InfrastructureSpecification.model_validate(
        {
            "schema_version": "1",
            "name": "unit-test",
            "provider": "azure",
            "region": "eastus",
            "environment": "dev",
            "resources": list(resources),
        }
    )


NETWORK = {
    "kind": "VirtualNetwork",
    "name": "main-vnet",
    "cidr": "10.0.0.0/16",
    "subnets": [
        {"name": "public-a", "cidr": "10.0.1.0/24", "public": True},
        {"name": "private-a", "cidr": "10.0.2.0/24", "public": False},
    ],
}
NSG = {
    "kind": "SecurityGroup",
    "name": "web-nsg",
    "network": "main-vnet",
    "rules": [
        {"direction": "inbound", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
        {"direction": "outbound", "protocol": "all", "cidr": "10.0.0.0/16"},
        {"direction": "inbound", "protocol": "tcp", "from_port": 8000, "to_port": 8080, "cidr": "10.0.0.0/16"},
    ],
}
VM = {
    "kind": "ComputeInstance",
    "name": "web-server",
    "size": "medium",
    "os": "ubuntu",
    "subnet": "main-vnet.public-a",
    "security_groups": ["web-nsg"],
    "public_ip": True,
}
BUCKET = {"kind": "StorageBucket", "name": "appdata01", "versioning": True}


def types(spec: InfrastructureSpecification) -> list[str]:
    return [r.type for r in AzureProvider().build_context(spec).resources]


def by_address(spec: InfrastructureSpecification) -> dict:
    return {r.address: r for r in AzureProvider().build_context(spec).resources}


def test_resource_types_per_kind():
    assert types(spec_of(NETWORK)) == [
        "azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet", "azurerm_subnet"
    ]
    assert types(spec_of(NETWORK, NSG))[-4:] == [
        "azurerm_network_security_group", *["azurerm_network_security_rule"] * 3
    ]
    assert types(spec_of(NETWORK, NSG, VM))[-4:] == [
        "azurerm_public_ip",
        "azurerm_network_interface",
        "azurerm_linux_virtual_machine",
        "azurerm_network_interface_security_group_association",
    ]
    assert types(spec_of(BUCKET)) == [
        "azurerm_resource_group", "azurerm_storage_account", "azurerm_storage_container"
    ]


def test_resource_group_always_present_and_named_from_spec():
    group = AzureProvider().build_context(spec_of(BUCKET)).resources[0]
    assert group.address == "azurerm_resource_group.main"
    assert group.attributes["name"] == "rg-unit-test-dev"
    assert group.attributes["location"] == "eastus"


def test_public_ip_only_when_requested():
    private_vm = dict(VM, public_ip=False, subnet="main-vnet.private-a")
    resources = by_address(spec_of(NETWORK, NSG, private_vm))
    assert "azurerm_public_ip.web_server" not in resources
    ip_config = resources["azurerm_network_interface.web_server"].attributes["ip_configuration"]
    assert "public_ip_address_id" not in ip_config.attributes

    resources = by_address(spec_of(NETWORK, NSG, VM))
    public_ip = resources["azurerm_public_ip.web_server"].attributes
    assert (public_ip["allocation_method"], public_ip["sku"]) == ("Static", "Standard")


def test_no_nsg_association_without_security_group():
    assert "azurerm_network_interface_security_group_association" not in types(
        spec_of(NETWORK, dict(VM, security_groups=[]))
    )


def test_nsg_rule_priorities_and_mapping():
    rules = [r for r in AzureProvider().build_context(spec_of(NETWORK, NSG)).resources
             if r.type == "azurerm_network_security_rule"]
    assert [r.label for r in rules] == ["web_nsg_inbound_1", "web_nsg_outbound_1", "web_nsg_inbound_2"]
    assert [r.attributes["priority"] for r in rules] == [100, 110, 120]
    http, egress, range_rule = (r.attributes for r in rules)
    assert (http["protocol"], http["destination_port_range"]) == ("Tcp", "80")
    assert (egress["protocol"], egress["destination_port_range"]) == ("*", "*")
    assert (egress["direction"], egress["destination_address_prefix"]) == ("Outbound", "10.0.0.0/16")
    assert range_rule["destination_port_range"] == "8000-8080"


def test_vm_uses_ssh_key_only_and_size_table():
    context = AzureProvider().build_context(spec_of(NETWORK, NSG, VM))
    vm = next(r for r in context.resources if r.type == "azurerm_linux_virtual_machine").attributes
    assert vm["size"] == "Standard_B2s"
    assert vm["disable_password_authentication"] is True
    assert "admin_password" not in vm
    assert vm["admin_ssh_key"] == Block(
        "admin_ssh_key",
        {"username": Expression("var.admin_username"),
         "public_key": Expression("var.admin_ssh_public_key")},
    )
    assert vm["source_image_reference"].attributes["sku"] == "22_04-lts-gen2"
    variables = {v.name: v.default for v in context.variables}
    assert variables == {"admin_username": "azureuser", "admin_ssh_public_key": None}


def test_no_admin_variables_without_vms():
    assert AzureProvider().build_context(spec_of(NETWORK)).variables == []


@pytest.mark.parametrize("public", [False, True])
def test_storage_public_access_follows_spec(public):
    resources = by_address(spec_of(dict(BUCKET, public_access=public)))
    account = resources["azurerm_storage_account.appdata01"].attributes
    assert account["public_network_access_enabled"] is public
    assert account["allow_nested_items_to_be_public"] is public
    assert account["blob_properties"].attributes == {"versioning_enabled": True}
    container = resources["azurerm_storage_container.appdata01"].attributes
    assert container["container_access_type"] == ("blob" if public else "private")


def test_tags_match_aws_scheme():
    spec = load_spec("demo4_azure")
    vnet = by_address(spec)["azurerm_virtual_network.main_vnet"].attributes
    assert vnet["tags"] == {
        "Environment": "dev", "ManagedBy": "inframorph", "Name": "main-vnet",
        "Project": "inframorph-demo",
    }


def test_outputs():
    names = [o.name for o in AzureProvider().build_context(spec_of(NETWORK, NSG, VM, BUCKET)).outputs]
    assert names == [
        "main_vnet_vnet_id",
        "main_vnet_public_a_subnet_id",
        "main_vnet_private_a_subnet_id",
        "web_server_private_ip",
        "web_server_public_ip",
        "appdata01_storage_account_name",
    ]


def test_rendering_twice_is_identical():
    def render():
        spec = load_spec("demo4_azure")
        return TerraformGenerator("azure").render(AzureProvider().build_context(spec))

    assert render() == render()


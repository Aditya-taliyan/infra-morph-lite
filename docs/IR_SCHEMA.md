# InfraMorph IR — Infrastructure Specification v1

The IR is the provider-neutral JSON document between the LLM and Terraform generation.
The LLM (or the rule-based parser) produces it. Everything after it is deterministic:
schema validation → semantic validation → provider mapping → Jinja2 → Terraform.
The LLM never writes Terraform.

## 1. Top-level fields

| Field | Type | Required | Rules |
|---|---|---|---|
| `schema_version` | string | yes | Must be `"1"` |
| `name` | string | yes | Project name. Lowercase letters, digits, hyphens; 3–32 chars |
| `provider` | enum | yes | `aws` \| `azure` |
| `region` | enum | yes | AWS: `us-east-1`, `ap-south-1`. Azure: `eastus`, `centralindia` |
| `environment` | enum | yes | `dev` \| `staging` \| `prod` |
| `tags` | object | no | String → string. Default `{}`. Max 20 entries |
| `resources` | array | yes | At least one resource |

Unknown fields anywhere in the document are rejected (Pydantic `extra="forbid"`).
The generator adds the tags `ManagedBy = "inframorph"` and `Environment = <environment>` to every taggable resource.

## 2. Common resource fields

Every resource has `kind` (one of the four below) and `name`.
`name` is lowercase letters, digits and hyphens, 3–40 chars, starting with a letter, and is unique across the whole spec.
It doubles as the resource's identifier for references and as the base of the Terraform resource label.

## 3. Resource kinds

### 3.1 VirtualNetwork

| Field | Type | Required | Rules |
|---|---|---|---|
| `cidr` | string | yes | IPv4 CIDR, prefix /16–/24 |
| `subnets` | array | yes | At least one |
| `subnets[].name` | string | yes | Same name rules; unique within this network |
| `subnets[].cidr` | string | yes | Inside the network CIDR |
| `subnets[].public` | bool | yes | `true` = routed to the internet |

### 3.2 SecurityGroup

| Field | Type | Required | Rules |
|---|---|---|---|
| `network` | ref | yes | Name of a VirtualNetwork (AWS security groups live inside a VPC) |
| `rules` | array | no | Default `[]` |
| `rules[].direction` | enum | yes | `inbound` \| `outbound` |
| `rules[].protocol` | enum | yes | `tcp` \| `udp` \| `icmp` \| `all` |
| `rules[].from_port` | int | tcp/udp only | 0–65535 |
| `rules[].to_port` | int | tcp/udp only | 0–65535, ≥ `from_port` |
| `rules[].cidr` | string | yes | IPv4 CIDR. Source for inbound, destination for outbound |
| `rules[].description` | string | no | Max 100 chars |

If a group has no outbound rules, both adapters allow all outbound traffic. On AWS this is an explicit egress rule resource, because Terraform removes AWS's default egress rule.

### 3.3 ComputeInstance

| Field | Type | Required | Rules |
|---|---|---|---|
| `size` | enum | yes | `small` \| `medium` \| `large` (see §6) |
| `os` | enum | yes | `ubuntu` (Ubuntu 22.04 LTS) |
| `subnet` | ref | yes | `"<network>.<subnet>"`, e.g. `"main-vpc.public-a"` |
| `security_groups` | array of ref | no | SecurityGroup names. Default `[]` |
| `public_ip` | bool | no | Default `false` |

### 3.4 StorageBucket

| Field | Type | Required | Rules |
|---|---|---|---|
| `versioning` | bool | no | Default `false` |
| `public_access` | bool | no | Default `false`. When `false`, all public access is blocked |

## 4. References

Resources reference each other **by name**. There are no IDs or Terraform addresses in the IR.

- `SecurityGroup.network` → the name of a VirtualNetwork
- `ComputeInstance.subnet` → `"<network name>.<subnet name>"`. Names cannot contain dots, so the split is unambiguous.
- `ComputeInstance.security_groups[]` → the names of SecurityGroups

**Every reference must resolve to an existing resource of the right kind.** A dangling reference is an error, never a warning.
The provider adapter turns resolved references into Terraform references such as `aws_subnet.main_vpc_public_a.id`.

## 5. Semantic validation

These checks run after Pydantic schema validation. They are pure functions over the IR.
**Errors** stop the pipeline. **Warnings** are shown to the user, and generation still continues.

### Errors

| Code | Rule |
|---|---|
| E01 | Resource names are unique across the spec; subnet names are unique within their network |
| E02 | Every reference resolves to an existing resource of the expected kind (§4) |
| E03 | Region is in the allowed list for the chosen provider |
| E04 | Every CIDR is a valid IPv4 network address (e.g. `10.0.1.5/24` is rejected) |
| E05 | Each subnet CIDR lies inside its network CIDR; subnets in one network do not overlap |
| E06 | tcp/udp rules have `from_port` ≤ `to_port`; icmp/all rules have no ports |
| E07 | A compute instance's security groups belong to the same network as its subnet |
| E08 | `public_ip: true` requires the referenced subnet to be `public: true` |
| E09 | Bucket names are 3–24 chars, lowercase letters and digits only (strictest common rule: Azure storage accounts) |
| E10 | Azure only: a compute instance references at most one security group (a NIC takes one NSG) |
| E11 | `schema_version` is `"1"` and `resources` is not empty |

### Warnings

| Code | Rule |
|---|---|
| W01 | Inbound SSH (port 22 inside the rule's range) from `0.0.0.0/0` |
| W02 | Inbound RDP (port 3389) from `0.0.0.0/0` |
| W03 | Inbound `protocol: all` or ports 0–65535 from `0.0.0.0/0` |
| W04 | A StorageBucket has `public_access: true` |
| W05 | A compute instance has no security groups (it gets the provider's default network rules) |
| W06 | `environment: prod` with a bucket that has `versioning: false` |

Prompt text is data, not instructions. A spec that asks to skip validation is validated like any other spec.

## 6. Size mapping

| IR size | AWS instance type | Azure VM size | vCPU / RAM |
|---|---|---|---|
| `small` | `t3.micro` | `Standard_B1s` | 2 / 1 GiB (AWS), 1 / 1 GiB (Azure) |
| `medium` | `t3.small` | `Standard_B2s` | 2 / 2 GiB (AWS), 2 / 4 GiB (Azure) |
| `large` | `t3.medium` | `Standard_B2ms` | 2 / 4 GiB (AWS), 2 / 8 GiB (Azure) |

The sizes are close equivalents, not identical. The table shows the differences on purpose.

## 7. Provider mapping (one-to-many)

| IR resource | AWS Terraform resources | Azure Terraform resources |
|---|---|---|
| VirtualNetwork | `aws_vpc` + one `aws_subnet` per subnet + `aws_internet_gateway` + `aws_route_table` (0.0.0.0/0 → IGW) + one `aws_route_table_association` per public subnet. The IGW and route table are created only if at least one subnet is public | `azurerm_virtual_network` + one `azurerm_subnet` per subnet |
| SecurityGroup | `aws_security_group` + one `aws_vpc_security_group_ingress_rule` / `aws_vpc_security_group_egress_rule` per rule | `azurerm_network_security_group` + one `azurerm_network_security_rule` per rule (priority assigned in order, starting at 100) |
| ComputeInstance | `aws_instance` (`associate_public_ip_address` = `public_ip`; AMI looked up by the spec's region in an adapter-generated `locals` map of Ubuntu 22.04 AMI IDs for `us-east-1` and `ap-south-1` — no data sources, no single default AMI) | `azurerm_network_interface` + `azurerm_public_ip` (only if `public_ip`) + `azurerm_linux_virtual_machine` + `azurerm_network_interface_security_group_association` (only if it has a security group) |
| StorageBucket | `aws_s3_bucket` + `aws_s3_bucket_versioning` + `aws_s3_bucket_public_access_block` | `azurerm_storage_account` (blob versioning in `blob_properties`) + `azurerm_storage_container` |

**Azure resource group.** This is not an IR resource. The Azure adapter always creates one `azurerm_resource_group` named `rg-<name>-<environment>` in the spec's region, and every Azure resource is placed in it.

**Known mismatches** (spec §36: no fake equivalence):
- Private subnets have no outbound internet on AWS (no NAT gateway, which is out of scope). On Azure, a "public" subnet only means its VMs may receive a public IP; Azure has no route-table equivalent here.
- The Azure VM needs an SSH public key. It comes from a Terraform variable and is never stored in the IR or the repo.
- Azure security groups attach to the NIC, hence rule E10. AWS allows several groups per instance.
- `public_access: true` on Azure sets `allow_nested_items_to_be_public` and makes the container `blob`-level public. This is coarser than S3 bucket policies.

## 8. Examples

### Demo 1 — "Create a basic AWS web server"

A compute instance always needs a subnet, so the minimal web server includes a small network.

```json
{
  "schema_version": "1",
  "name": "basic-web",
  "provider": "aws",
  "region": "us-east-1",
  "environment": "dev",
  "tags": {},
  "resources": [
    { "kind": "VirtualNetwork", "name": "web-vpc", "cidr": "10.0.0.0/16",
      "subnets": [ { "name": "public-a", "cidr": "10.0.1.0/24", "public": true } ] },
    { "kind": "SecurityGroup", "name": "web-sg", "network": "web-vpc",
      "rules": [
        { "direction": "inbound", "protocol": "tcp", "from_port": 80, "to_port": 80,
          "cidr": "0.0.0.0/0", "description": "HTTP" }
      ] },
    { "kind": "ComputeInstance", "name": "web-server", "size": "small", "os": "ubuntu",
      "subnet": "web-vpc.public-a", "security_groups": ["web-sg"], "public_ip": true }
  ]
}
```

### Demo 2 — "Create an AWS VPC with a public subnet and web server"

This spec includes SSH from anywhere on purpose, to show warning W01.

```json
{
  "schema_version": "1",
  "name": "vpc-web",
  "provider": "aws",
  "region": "ap-south-1",
  "environment": "dev",
  "tags": { "Project": "inframorph-demo" },
  "resources": [
    { "kind": "VirtualNetwork", "name": "main-vpc", "cidr": "10.10.0.0/16",
      "subnets": [ { "name": "public-a", "cidr": "10.10.1.0/24", "public": true } ] },
    { "kind": "SecurityGroup", "name": "web-sg", "network": "main-vpc",
      "rules": [
        { "direction": "inbound", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0" },
        { "direction": "inbound", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0" },
        { "direction": "inbound", "protocol": "tcp", "from_port": 22, "to_port": 22, "cidr": "0.0.0.0/0" },
        { "direction": "outbound", "protocol": "all", "cidr": "0.0.0.0/0" }
      ] },
    { "kind": "ComputeInstance", "name": "web-server", "size": "medium", "os": "ubuntu",
      "subnet": "main-vpc.public-a", "security_groups": ["web-sg"], "public_ip": true }
  ]
}
```

Expected result: valid, with warning W01 ("SSH open to 0.0.0.0/0 on web-sg").

### Demo 5 — "Create a web infrastructure with networking, compute, security, and storage"

```json
{
  "schema_version": "1",
  "name": "web-stack",
  "provider": "aws",
  "region": "us-east-1",
  "environment": "staging",
  "tags": { "Owner": "student", "Project": "inframorph-demo" },
  "resources": [
    { "kind": "VirtualNetwork", "name": "app-vpc", "cidr": "10.20.0.0/16",
      "subnets": [
        { "name": "public-a", "cidr": "10.20.1.0/24", "public": true },
        { "name": "private-a", "cidr": "10.20.2.0/24", "public": false }
      ] },
    { "kind": "SecurityGroup", "name": "web-sg", "network": "app-vpc",
      "rules": [
        { "direction": "inbound", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0" },
        { "direction": "inbound", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0" },
        { "direction": "inbound", "protocol": "tcp", "from_port": 22, "to_port": 22,
          "cidr": "203.0.113.0/24", "description": "SSH from admin network only" }
      ] },
    { "kind": "SecurityGroup", "name": "app-sg", "network": "app-vpc",
      "rules": [
        { "direction": "inbound", "protocol": "tcp", "from_port": 8080, "to_port": 8080,
          "cidr": "10.20.1.0/24", "description": "App traffic from public subnet" }
      ] },
    { "kind": "ComputeInstance", "name": "web-server", "size": "small", "os": "ubuntu",
      "subnet": "app-vpc.public-a", "security_groups": ["web-sg"], "public_ip": true },
    { "kind": "ComputeInstance", "name": "app-server", "size": "medium", "os": "ubuntu",
      "subnet": "app-vpc.private-a", "security_groups": ["app-sg"], "public_ip": false },
    { "kind": "StorageBucket", "name": "webstackassets01", "versioning": true, "public_access": false }
  ]
}
```

Expected result: valid, with no warnings. On AWS this maps to 1 VPC, 2 subnets, 1 IGW, 1 route table,
1 route table association, 2 security groups with 6 rule resources (4 inbound, plus 1 default outbound per group), 2 instances, 1 bucket with versioning and a public-access block.
Changing `provider` to `azure` and `region` to `centralindia` gives an equally valid Azure spec, because each instance has only one security group (E10).

## 9. Dependencies, variables and outputs

- **Dependencies** are implicit, through references (§4). v1 has no separate `depends_on` field. The adapter turns each reference into a Terraform reference, and Terraform works out the order from those.
- **Variables** are generated by the adapter, and only for values that must not be stored in the IR:
  - the Azure SSH public key
  - an optional override of the AWS region→AMI map
- **Outputs** are derived automatically for each resource:
  - VPC / VNet ID and subnet IDs
  - instance private IP, plus public IP when `public_ip` is `true`
  - bucket / storage account name

There are no user-defined variables or outputs in v1.

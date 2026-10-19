"""Resource models of the IR (docs/IR_SCHEMA.md §2–§4).

Only schema rules live here: types, required fields, enums, name formats,
CIDR syntax and port ranges. Cross-field and cross-resource rules (reference
resolution, CIDR containment, from_port <= to_port, ...) belong to the
semantic validator.
"""

import ipaddress
import re
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.domain.enums import Direction, InstanceSize, OperatingSystem, Protocol

_NAME_PATTERN = r"[a-z][a-z0-9-]{2,39}"
_CIDR_SYNTAX = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")

ResourceName = Annotated[str, Field(pattern=rf"^{_NAME_PATTERN}$")]
SubnetRef = Annotated[str, Field(pattern=rf"^{_NAME_PATTERN}\.{_NAME_PATTERN}$")]
Port = Annotated[int, Field(ge=0, le=65535)]


def _check_cidr_syntax(value: str) -> str:
    # strict=False: host bits are allowed here. "10.0.1.5/24" is syntactically
    # fine; rejecting it is semantic rule E04.
    if not _CIDR_SYNTAX.match(value):
        raise ValueError("must be an IPv4 CIDR like 10.0.0.0/16")
    try:
        ipaddress.IPv4Network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid IPv4 CIDR: {exc}") from exc
    return value


def _check_network_prefix(value: str) -> str:
    prefix = int(value.split("/")[1])
    if not 16 <= prefix <= 24:
        raise ValueError("network CIDR prefix must be between /16 and /24")
    return value


Cidr = Annotated[str, AfterValidator(_check_cidr_syntax)]
NetworkCidr = Annotated[Cidr, AfterValidator(_check_network_prefix)]


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Subnet(IRModel):
    name: ResourceName
    cidr: Cidr
    public: bool


class VirtualNetwork(IRModel):
    kind: Literal["VirtualNetwork"]
    name: ResourceName
    cidr: NetworkCidr
    subnets: list[Subnet] = Field(min_length=1)


class SecurityRule(IRModel):
    direction: Direction
    protocol: Protocol
    # Ports are required for tcp/udp and forbidden for icmp/all (E06).
    from_port: Port | None = None
    to_port: Port | None = None
    cidr: Cidr
    description: str | None = Field(default=None, max_length=100)


class SecurityGroup(IRModel):
    kind: Literal["SecurityGroup"]
    name: ResourceName
    network: ResourceName
    rules: list[SecurityRule] = Field(default_factory=list)


class ComputeInstance(IRModel):
    kind: Literal["ComputeInstance"]
    name: ResourceName
    size: InstanceSize
    os: OperatingSystem
    subnet: SubnetRef
    security_groups: list[ResourceName] = Field(default_factory=list)
    public_ip: bool = False


class StorageBucket(IRModel):
    kind: Literal["StorageBucket"]
    name: ResourceName
    versioning: bool = False
    public_access: bool = False


Resource = Annotated[
    Union[VirtualNetwork, SecurityGroup, ComputeInstance, StorageBucket],
    Field(discriminator="kind"),
]

"""Semantic validation of the IR (docs/IR_SCHEMA.md §5).

Runs after Pydantic schema validation. Every rule is a pure function that
yields issues; validate() runs all of them and collects everything, so the
user sees every problem at once. Invalid specs never raise.
"""

import ipaddress
import re
from collections import Counter
from collections.abc import Callable, Iterator
from itertools import combinations

from app.domain.enums import Direction, Environment, Protocol, Provider, Region
from app.domain.resources import (
    ComputeInstance,
    SecurityGroup,
    SecurityRule,
    StorageBucket,
    Subnet,
    VirtualNetwork,
)
from app.domain.spec import InfrastructureSpecification
from app.domain.validation import Severity, ValidationIssue, ValidationResult

Issues = Iterator[ValidationIssue]

ALLOWED_REGIONS: dict[Provider, frozenset[Region]] = {
    Provider.AWS: frozenset({Region.US_EAST_1, Region.AP_SOUTH_1}),
    Provider.AZURE: frozenset({Region.EASTUS, Region.CENTRALINDIA}),
}

_BUCKET_NAME = re.compile(r"^[a-z0-9]{3,24}$")
_SSH_PORT = 22
_RDP_PORT = 3389


def _error(code: str, message: str, resource_name: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, severity=Severity.ERROR, message=message, resource_name=resource_name
    )


def _warning(code: str, message: str, resource_name: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, severity=Severity.WARNING, message=message, resource_name=resource_name
    )


# --- helpers ---------------------------------------------------------------


def _of_kind[T](spec: InfrastructureSpecification, cls: type[T]) -> list[T]:
    return [r for r in spec.resources if isinstance(r, cls)]


def _by_name(spec: InfrastructureSpecification) -> dict:
    # With duplicate names (E01) the first one wins, so references still resolve.
    index: dict = {}
    for resource in spec.resources:
        index.setdefault(resource.name, resource)
    return index


def _parse_cidr(cidr: str, strict: bool) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(cidr, strict=strict)
    except ValueError:
        return None


def _rule_label(group: SecurityGroup, index: int, rule: SecurityRule) -> str:
    return f"{group.name} rule #{index} ({rule.direction} {rule.protocol})"


def _resolve_subnet(
    spec: InfrastructureSpecification, ref: str
) -> tuple[VirtualNetwork, Subnet] | None:
    network_name, _, subnet_name = ref.partition(".")
    network = _by_name(spec).get(network_name)
    if not isinstance(network, VirtualNetwork):
        return None
    for subnet in network.subnets:
        if subnet.name == subnet_name:
            return network, subnet
    return None


def _all_cidrs(spec: InfrastructureSpecification) -> Iterator[tuple[str, str, str]]:
    """Yield (description, cidr, resource_name) for every CIDR in the spec."""
    for network in _of_kind(spec, VirtualNetwork):
        yield f"Network '{network.name}'", network.cidr, network.name
        for subnet in network.subnets:
            yield f"Subnet '{network.name}.{subnet.name}'", subnet.cidr, network.name
    for group in _of_kind(spec, SecurityGroup):
        for i, rule in enumerate(group.rules, start=1):
            yield _rule_label(group, i, rule), rule.cidr, group.name


def _open_inbound_rules(
    spec: InfrastructureSpecification,
) -> Iterator[tuple[SecurityGroup, int, SecurityRule]]:
    """Inbound rules whose source is the whole internet (0.0.0.0/0)."""
    for group in _of_kind(spec, SecurityGroup):
        for i, rule in enumerate(group.rules, start=1):
            source = _parse_cidr(rule.cidr, strict=False)
            if rule.direction is Direction.INBOUND and source is not None and source.prefixlen == 0:
                yield group, i, rule


def _tcp_rule_covers(rule: SecurityRule, port: int) -> bool:
    return (
        rule.protocol is Protocol.TCP
        and rule.from_port is not None
        and rule.to_port is not None
        and rule.from_port <= port <= rule.to_port
    )


# --- errors ----------------------------------------------------------------


def _e01_unique_names(spec: InfrastructureSpecification) -> Issues:
    counts = Counter(r.name for r in spec.resources)
    for name, count in counts.items():
        if count > 1:
            yield _error(
                "E01",
                f"The name '{name}' is used by {count} resources; "
                "resource names must be unique across the spec",
                name,
            )
    for network in _of_kind(spec, VirtualNetwork):
        subnet_counts = Counter(s.name for s in network.subnets)
        for name, count in subnet_counts.items():
            if count > 1:
                yield _error(
                    "E01",
                    f"Network '{network.name}' has {count} subnets named '{name}'; "
                    "subnet names must be unique within a network",
                    network.name,
                )


def _e02_references_resolve(spec: InfrastructureSpecification) -> Issues:
    index = _by_name(spec)

    def check(owner: str, what: str, target: str, expected: str) -> ValidationIssue | None:
        found = index.get(target)
        if found is None:
            return _error("E02", f"{owner} refers to {what} '{target}', which does not exist", owner)
        if found.kind != expected:
            return _error(
                "E02",
                f"{owner} refers to {what} '{target}', but '{target}' is a "
                f"{found.kind}, not a {expected}",
                owner,
            )
        return None

    for group in _of_kind(spec, SecurityGroup):
        if issue := check(group.name, "network", group.network, "VirtualNetwork"):
            yield issue

    for instance in _of_kind(spec, ComputeInstance):
        network_name, _, subnet_name = instance.subnet.partition(".")
        if issue := check(instance.name, "network", network_name, "VirtualNetwork"):
            yield issue
        elif _resolve_subnet(spec, instance.subnet) is None:
            yield _error(
                "E02",
                f"{instance.name} uses subnet '{instance.subnet}', which does not exist "
                f"in network '{network_name}'",
                instance.name,
            )
        for sg_name in instance.security_groups:
            if issue := check(instance.name, "security group", sg_name, "SecurityGroup"):
                yield issue


def _e03_region_matches_provider(spec: InfrastructureSpecification) -> Issues:
    allowed = ALLOWED_REGIONS[spec.provider]
    if spec.region not in allowed:
        yield _error(
            "E03",
            f"Region '{spec.region}' is not available for provider '{spec.provider}'. "
            f"Allowed regions: {', '.join(sorted(allowed))}",
        )


def _e04_cidrs_are_network_addresses(spec: InfrastructureSpecification) -> Issues:
    for label, cidr, owner in _all_cidrs(spec):
        loose = _parse_cidr(cidr, strict=False)
        if loose is None:
            yield _error("E04", f"{label} has CIDR '{cidr}', which is not a valid IPv4 CIDR", owner)
        elif _parse_cidr(cidr, strict=True) is None:
            yield _error(
                "E04",
                f"{label} has CIDR '{cidr}', which is not a network address "
                f"(host bits are set); did you mean '{loose}'?",
                owner,
            )


def _e05_subnets_fit_network(spec: InfrastructureSpecification) -> Issues:
    # CIDRs that fail E04 are skipped here so one mistake gives one issue.
    for network in _of_kind(spec, VirtualNetwork):
        network_cidr = _parse_cidr(network.cidr, strict=True)
        subnets = [
            (s, cidr) for s in network.subnets if (cidr := _parse_cidr(s.cidr, strict=True))
        ]
        if network_cidr is not None:
            for subnet, cidr in subnets:
                if not cidr.subnet_of(network_cidr):
                    yield _error(
                        "E05",
                        f"Subnet '{network.name}.{subnet.name}' ({cidr}) is outside "
                        f"its network '{network.name}' ({network_cidr})",
                        network.name,
                    )
        for (a, a_cidr), (b, b_cidr) in combinations(subnets, 2):
            if a_cidr.overlaps(b_cidr):
                yield _error(
                    "E05",
                    f"Subnets '{network.name}.{a.name}' ({a_cidr}) and "
                    f"'{network.name}.{b.name}' ({b_cidr}) overlap",
                    network.name,
                )


def _e06_rule_ports(spec: InfrastructureSpecification) -> Issues:
    for group in _of_kind(spec, SecurityGroup):
        for i, rule in enumerate(group.rules, start=1):
            label = _rule_label(group, i, rule)
            if rule.protocol in (Protocol.TCP, Protocol.UDP):
                if rule.from_port is None or rule.to_port is None:
                    yield _error("E06", f"{label} needs both from_port and to_port", group.name)
                elif rule.from_port > rule.to_port:
                    yield _error(
                        "E06",
                        f"{label} has from_port {rule.from_port} greater than "
                        f"to_port {rule.to_port}",
                        group.name,
                    )
            elif rule.from_port is not None or rule.to_port is not None:
                yield _error(
                    "E06",
                    f"{label} must not set ports; {rule.protocol} rules apply to all ports",
                    group.name,
                )


def _e07_security_groups_in_subnet_network(spec: InfrastructureSpecification) -> Issues:
    index = _by_name(spec)
    for instance in _of_kind(spec, ComputeInstance):
        resolved = _resolve_subnet(spec, instance.subnet)
        if resolved is None:
            continue  # reported by E02
        network, _ = resolved
        for sg_name in instance.security_groups:
            group = index.get(sg_name)
            if not isinstance(group, SecurityGroup):
                continue  # reported by E02
            if not isinstance(index.get(group.network), VirtualNetwork):
                continue  # the group's own network is missing: reported by E02
            if group.network != network.name:
                yield _error(
                    "E07",
                    f"{instance.name} is in network '{network.name}', but its security "
                    f"group '{group.name}' belongs to network '{group.network}'",
                    instance.name,
                )


def _e08_public_ip_needs_public_subnet(spec: InfrastructureSpecification) -> Issues:
    for instance in _of_kind(spec, ComputeInstance):
        resolved = _resolve_subnet(spec, instance.subnet)
        if instance.public_ip and resolved is not None and not resolved[1].public:
            yield _error(
                "E08",
                f"{instance.name} asks for a public IP, but its subnet "
                f"'{instance.subnet}' is private (public: false)",
                instance.name,
            )


def _e09_bucket_names(spec: InfrastructureSpecification) -> Issues:
    for bucket in _of_kind(spec, StorageBucket):
        if not _BUCKET_NAME.match(bucket.name):
            yield _error(
                "E09",
                f"Bucket name '{bucket.name}' is not allowed: bucket names must be "
                "3 to 24 characters, lowercase letters and digits only (no hyphens), "
                "because Azure storage accounts require it",
                bucket.name,
            )


def _e10_azure_single_security_group(spec: InfrastructureSpecification) -> Issues:
    if spec.provider is not Provider.AZURE:
        return
    for instance in _of_kind(spec, ComputeInstance):
        if len(instance.security_groups) > 1:
            yield _error(
                "E10",
                f"{instance.name} has {len(instance.security_groups)} security groups, "
                "but on Azure a VM's network interface can take only one",
                instance.name,
            )


def _e11_document_basics(spec: InfrastructureSpecification) -> Issues:
    # The schema already enforces both; this guards specs built without it.
    if spec.schema_version != "1":
        yield _error(
            "E11", f"schema_version is '{spec.schema_version}', but only '1' is supported"
        )
    if not spec.resources:
        yield _error("E11", "The spec has no resources; at least one is required")


# --- warnings --------------------------------------------------------------


def _w01_ssh_open_to_world(spec: InfrastructureSpecification) -> Issues:
    for group, i, rule in _open_inbound_rules(spec):
        if _tcp_rule_covers(rule, _SSH_PORT):
            yield _warning(
                "W01",
                f"SSH (port 22) is open to the whole internet (0.0.0.0/0) on "
                f"{_rule_label(group, i, rule)}; restrict it to your own IP range",
                group.name,
            )


def _w02_rdp_open_to_world(spec: InfrastructureSpecification) -> Issues:
    for group, i, rule in _open_inbound_rules(spec):
        if _tcp_rule_covers(rule, _RDP_PORT):
            yield _warning(
                "W02",
                f"RDP (port 3389) is open to the whole internet (0.0.0.0/0) on "
                f"{_rule_label(group, i, rule)}; restrict it to your own IP range",
                group.name,
            )


def _w03_all_ports_open_to_world(spec: InfrastructureSpecification) -> Issues:
    for group, i, rule in _open_inbound_rules(spec):
        if rule.protocol is Protocol.ALL or (rule.from_port == 0 and rule.to_port == 65535):
            yield _warning(
                "W03",
                f"All ports are open to the whole internet (0.0.0.0/0) on "
                f"{_rule_label(group, i, rule)}; allow only the ports you need",
                group.name,
            )


def _w04_public_bucket(spec: InfrastructureSpecification) -> Issues:
    for bucket in _of_kind(spec, StorageBucket):
        if bucket.public_access:
            yield _warning(
                "W04",
                f"Bucket '{bucket.name}' allows public access; anyone on the internet "
                "may be able to read its contents",
                bucket.name,
            )


def _w05_instance_without_security_groups(spec: InfrastructureSpecification) -> Issues:
    for instance in _of_kind(spec, ComputeInstance):
        if not instance.security_groups:
            yield _warning(
                "W05",
                f"{instance.name} has no security groups, so it gets the provider's "
                "default network rules",
                instance.name,
            )


def _w06_prod_bucket_without_versioning(spec: InfrastructureSpecification) -> Issues:
    if spec.environment is not Environment.PROD:
        return
    for bucket in _of_kind(spec, StorageBucket):
        if not bucket.versioning:
            yield _warning(
                "W06",
                f"Bucket '{bucket.name}' is in a prod environment without versioning; "
                "deleted or overwritten files cannot be recovered",
                bucket.name,
            )


RULES: tuple[Callable[[InfrastructureSpecification], Issues], ...] = (
    _e01_unique_names,
    _e02_references_resolve,
    _e03_region_matches_provider,
    _e04_cidrs_are_network_addresses,
    _e05_subnets_fit_network,
    _e06_rule_ports,
    _e07_security_groups_in_subnet_network,
    _e08_public_ip_needs_public_subnet,
    _e09_bucket_names,
    _e10_azure_single_security_group,
    _e11_document_basics,
    _w01_ssh_open_to_world,
    _w02_rdp_open_to_world,
    _w03_all_ports_open_to_world,
    _w04_public_bucket,
    _w05_instance_without_security_groups,
    _w06_prod_bucket_without_versioning,
)


def validate(spec: InfrastructureSpecification) -> ValidationResult:
    """Run every rule in §5 and return all issues, errors first, in rule order."""
    return ValidationResult(issues=[issue for rule in RULES for issue in rule(spec)])

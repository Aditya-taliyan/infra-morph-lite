"""Natural language -> IR with keywords and regexes, no LLM.

The fallback that keeps the demo working when Ollama is off. It is
deterministic: the same requirement always gives the same spec. It detects
which of the four resource kinds the text asks for plus a few options
(web ports, SSH, size, environment, region, versioning) and builds the IR
from fixed templates. Anything else is ignored and listed in the notes.

It does not guess: a requirement with no recognisable resource is a
failure. Its output goes through check_candidate like the LLM's.
"""

import hashlib
import ipaddress
import re

from app.domain.enums import Provider
from app.interpreters.base import (
    InterpretationResult,
    InterpretationStatus,
    SpecInterpreter,
    check_candidate,
)


def _words(*patterns: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b")


NETWORK = _words(r"vpcs?", r"vnets?", r"virtual networks?", r"network(?:s|ing)?", r"subnets?")
COMPUTE = _words(
    r"servers?", r"vms?", r"virtual machines?", r"instances?", r"ec2", r"compute", r"hosts?"
)
WEB = _words(r"web", r"websites?", r"https?", r"nginx", r"apache")
STORAGE = _words(r"buckets?", r"storage", r"s3", r"blobs?")
SECURITY = _words(r"security(?: groups?)?", r"firewalls?", r"nsgs?")
SSH = _words(r"ssh")
# "ssh from anywhere", "open ssh to the internet", "ssh from 0.0.0.0/0"
SSH_FROM_ANYWHERE = re.compile(
    r"\bssh\b[^.;,]{0,40}?(?:\b(?:anywhere|everywhere|internet|world|any ip)\b|0\.0\.0\.0/0)"
)
SSH_SOURCE_CIDR = re.compile(r"\bssh\b[^.;]{0,40}?\b(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b")
ALL_PORTS = _words(r"all ports", r"every port", r"any port", r"all traffic")
PUBLIC = _words(r"public", r"internet")
PRIVATE = _words(r"private")
VERSIONING = _words(r"versioning", r"versioned")
PUBLIC_BUCKET = _words(r"public(?:ly)?[- ](?:read|readable|accessible|access|bucket|files)")
NOT_PUBLIC = _words(r"(?:no|block|without|deny)\s+public")
SIZE = _words(r"small", r"medium", r"large")

ENVIRONMENTS = (("prod", _words(r"prod", r"production")),
                ("staging", _words(r"staging", r"stage")),
                ("dev", _words(r"dev", r"development")))

# Region names and plain-language aliases, per provider; first match wins.
REGIONS: dict[Provider, tuple[tuple[str, re.Pattern[str]], ...]] = {
    Provider.AWS: (
        ("ap-south-1", _words(r"ap-south-1", r"mumbai", r"india", r"centralindia")),
        ("us-east-1", _words(r"us-east-1", r"us east", r"virginia", r"eastus")),
    ),
    Provider.AZURE: (
        ("centralindia", _words(r"centralindia", r"central india", r"india", r"mumbai",
                                r"ap-south-1")),
        ("eastus", _words(r"eastus", r"east us", r"us-east-1", r"virginia")),
    ),
}
DEFAULT_REGION = {Provider.AWS: "us-east-1", Provider.AZURE: "eastus"}
OTHER_PROVIDER = {
    Provider.AWS: _words(r"azure", r"microsoft"),
    Provider.AZURE: _words(r"aws", r"amazon", r"ec2", r"s3"),
}

UNSUPPORTED = {
    "databases": _words(r"databases?", r"db", r"rds", r"sql", r"mysql", r"postgres(?:ql)?",
                        r"mongodb", r"dynamodb", r"cosmos ?db"),
    "Kubernetes": _words(r"kubernetes", r"k8s", r"eks", r"aks", r"gke"),
    "load balancers": _words(r"load[- ]balanc(?:er|ers|ing)", r"alb", r"elb"),
    "serverless functions": _words(r"serverless", r"lambdas?", r"functions?"),
    "containers": _words(r"containers?", r"docker", r"ecs"),
    "IAM": _words(r"iam"),
    "NAT gateways": _words(r"nat(?: gateways?)?"),
    "DNS": _words(r"dns", r"route ?53"),
    "caches": _words(r"cache", r"redis", r"memcached"),
    "queues": _words(r"queues?", r"sqs", r"kafka"),
}

SUPPORTED_SUMMARY = (
    "The rule-based interpreter understands virtual networks (VPC/VNet) with subnets, "
    "security groups, Ubuntu compute instances (web servers, VMs) and storage buckets."
)

NETWORK_CIDR = "10.0.0.0/16"
PUBLIC_SUBNET = {"name": "public-a", "cidr": "10.0.1.0/24", "public": True}
PRIVATE_SUBNET = {"name": "private-a", "cidr": "10.0.2.0/24", "public": False}
ANYWHERE = "0.0.0.0/0"


def _tcp(port: int, cidr: str, description: str) -> dict:
    return {"direction": "inbound", "protocol": "tcp", "from_port": port, "to_port": port,
            "cidr": cidr, "description": description}


class RuleBasedInterpreter(SpecInterpreter):
    name = "rule-based"

    def interpret(self, requirement: str, provider: Provider) -> InterpretationResult:
        text = " ".join(requirement.lower().split())
        notes: list[str] = []
        result = InterpretationResult(self.name, InterpretationStatus.FAILED, attempts=1)

        unsupported = [label for label, pattern in UNSUPPORTED.items() if pattern.search(text)]
        if unsupported:
            notes.append(
                f"Left out (not supported): {', '.join(unsupported)}."
            )
        if OTHER_PROVIDER[provider].search(text):
            notes.append(
                f"The text mentions another cloud; the selected provider "
                f"'{provider.value}' is used."
            )

        wants_compute = bool(COMPUTE.search(text))
        wants_network = bool(NETWORK.search(text))
        wants_storage = bool(STORAGE.search(text))
        wants_security = bool(SECURITY.search(text))
        if not (wants_compute or wants_network or wants_storage or wants_security):
            result.errors.append(f"No supported infrastructure found in the requirement. "
                                 f"{SUPPORTED_SUMMARY}")
            result.notes = notes
            return result

        resources: list[dict] = []
        name_parts: list[str] = []
        is_web = bool(WEB.search(text))

        # A compute instance always needs a network; so does a security group.
        if wants_network or wants_compute or wants_security:
            network_name = "main-vpc" if provider is Provider.AWS else "main-vnet"
            resources.append(self._network(network_name, text, wants_compute))
            name_parts.append("net")

            group_name = "web-sg" if is_web else "vm-sg"
            if wants_compute or wants_security:
                rules = self._rules(text, is_web and wants_compute, notes)
                resources.append({"kind": "SecurityGroup", "name": group_name,
                                  "network": network_name, "rules": rules})

            if wants_compute:
                subnet = resources[0]["subnets"][0]
                resources.append({
                    "kind": "ComputeInstance",
                    "name": "web-server" if is_web else "linux-vm",
                    "size": (m.group(0) if (m := SIZE.search(text)) else "small"),
                    "os": "ubuntu",
                    "subnet": f"{network_name}.{subnet['name']}",
                    "security_groups": [group_name],
                    "public_ip": subnet["public"],
                })
                name_parts.append("web" if is_web else "vm")

        if wants_storage:
            resources.append(self._bucket(text, provider))
            name_parts.append("storage")

        data = {
            "schema_version": "1",
            "name": f"{provider.value}-{'-'.join(name_parts)}",
            "provider": provider.value,
            "region": self._region(text, provider),
            "environment": next((env for env, p in ENVIRONMENTS if p.search(text)), "dev"),
            "tags": {},
            "resources": resources,
        }

        check = check_candidate(data, provider)
        result.notes = notes
        if check.spec is None:
            # A bug in the templates above, never the user's fault; report it plainly.
            result.errors.extend(check.errors)
            return result
        result.status = InterpretationStatus.SUCCESS
        result.spec = check.spec
        result.validation = check.validation
        return result

    @staticmethod
    def _network(name: str, text: str, has_compute: bool) -> dict:
        public = bool(PUBLIC.search(text))
        private = bool(PRIVATE.search(text))
        if has_compute:
            # The instance goes into the first subnet. A web server is public;
            # any other instance stays private unless asked otherwise.
            exposed = public or bool(WEB.search(text))
            subnets = [PUBLIC_SUBNET] if exposed else [PRIVATE_SUBNET]
            if exposed and private:
                subnets.append(PRIVATE_SUBNET)
        else:
            subnets = [PUBLIC_SUBNET] if public or not private else []
            if private:
                subnets.append(PRIVATE_SUBNET)
        return {"kind": "VirtualNetwork", "name": name, "cidr": NETWORK_CIDR,
                "subnets": [dict(s) for s in subnets]}

    @staticmethod
    def _rules(text: str, web: bool, notes: list[str]) -> list[dict]:
        rules = []
        if web:
            rules.append(_tcp(80, ANYWHERE, "HTTP"))
            rules.append(_tcp(443, ANYWHERE, "HTTPS"))
        if SSH.search(text):
            if (m := SSH_SOURCE_CIDR.search(text)) and m.group(1) != ANYWHERE:
                try:
                    source = str(ipaddress.IPv4Network(m.group(1), strict=False))
                except ValueError:
                    source = NETWORK_CIDR
                    notes.append(f"'{m.group(1)}' is not a valid CIDR; SSH is limited to "
                                 f"the network ({NETWORK_CIDR}).")
                rules.append(_tcp(22, source, "SSH"))
            elif SSH_FROM_ANYWHERE.search(text):
                # Asked for explicitly; validation warns about it (W01).
                rules.append(_tcp(22, ANYWHERE, "SSH from anywhere (requested)"))
            else:
                # Secure default: no source given, so not the whole internet.
                rules.append(_tcp(22, NETWORK_CIDR, "SSH from inside the network"))
                notes.append(f"SSH is allowed from inside the network ({NETWORK_CIDR}) only; "
                             "name a source CIDR (e.g. 'SSH from 203.0.113.0/24') to change it.")
        if ALL_PORTS.search(text):
            # Asked for explicitly; validation warns about it (W03).
            rules.append({"direction": "inbound", "protocol": "all", "cidr": ANYWHERE,
                          "description": "All ports (requested)"})
        if not rules:
            notes.append("The security group has no inbound rules, so no inbound traffic is "
                         "allowed; ask for e.g. HTTP or SSH to open ports.")
        return rules

    @staticmethod
    def _bucket(text: str, provider: Provider) -> dict:
        # Deterministic but distinct per requirement; letters and digits only (E09).
        digest = hashlib.sha256(f"{provider.value}:{text}".encode()).hexdigest()[:8]
        public = bool(PUBLIC_BUCKET.search(text)) and not NOT_PUBLIC.search(text)
        return {"kind": "StorageBucket", "name": f"storage{digest}",
                "versioning": bool(VERSIONING.search(text)), "public_access": public}

    @staticmethod
    def _region(text: str, provider: Provider) -> str:
        for region, pattern in REGIONS[provider]:
            if pattern.search(text):
                return region
        return DEFAULT_REGION[provider]

"""Which services a requirement asks for that InfraMorph does not support.

Deterministic keyword detection that runs before any interpreter. A small LLM
tends to replace an unsupported service with a VM, so it is told by name what
to leave out, and is not called at all when nothing supported is asked for.
Only the fixed names below ever reach the prompt, never the user's text.
"""

import re

_UNSUPPORTED = {
    "database": r"databases?",
    "rds": r"rds",
    "postgres": r"postgres",
    "postgresql": r"postgresql",
    "mysql": r"mysql",
    "kubernetes": r"kubernetes",
    "k8s": r"k8s",
    "eks": r"eks",
    "aks": r"aks",
    "load balancer": r"load[-\s]*balanc(?:ers?|ing)",
    "alb": r"albs?",
    "elb": r"elbs?",
    "lambda": r"lambdas?",
    "serverless": r"serverless",
    "function app": r"function\s+apps?",
    "queue": r"queues?",
    "sqs": r"sqs",
    "cache": r"caches?",
    "redis": r"redis",
    "cdn": r"cdns?",
}
_UNSUPPORTED_PATTERNS = {
    name: re.compile(rf"\b{pattern}\b", re.IGNORECASE) for name, pattern in _UNSUPPORTED.items()
}

# No bare "instance": "an RDS instance" must not count as a supported request.
_SUPPORTED = re.compile(
    r"\b(?:servers?|vms?|virtual\s+machines?|ec2|websites?"
    r"|network(?:s|ing)?|vpcs?|vnets?|subnets?"
    r"|buckets?|storage|s3|blobs?"
    r"|security\s+groups?|firewalls?|nsgs?)\b",
    re.IGNORECASE,
)


def unsupported_requests(requirement: str) -> list[str]:
    return [name for name, p in _UNSUPPORTED_PATTERNS.items() if p.search(requirement)]


def has_supported(requirement: str) -> bool:
    return _SUPPORTED.search(requirement) is not None

import pytest

from app.interpreters.scope import has_supported, unsupported_requests


@pytest.mark.parametrize("name", [
    "database", "rds", "postgres", "postgresql", "mysql", "kubernetes", "k8s", "eks", "aks",
    "load balancer", "alb", "elb", "lambda", "serverless", "function app", "queue", "sqs",
    "cache", "redis", "cdn",
])
def test_every_unsupported_service_is_detected(name):
    assert unsupported_requests(f"Create a {name.upper()}, please.") == [name]
    assert not has_supported(f"Create a {name}")


@pytest.mark.parametrize("text, expected", [
    ("two databases and queues", ["database", "queue"]),
    ("load-balancing", ["load balancer"]),
    ("redis, then more redis", ["redis"]),
    ("tasks on a desktop with weeks of data", []),
    ("a web server with a bucket", []),
])
def test_unsupported_forms(text, expected):
    assert unsupported_requests(text) == expected


@pytest.mark.parametrize("word", [
    "server", "VM", "virtual machine", "EC2", "website", "network", "VPC", "VNet", "subnet",
    "buckets", "S3", "storage", "security group", "firewall",
])
def test_supported_words(word):
    assert has_supported(f"a {word} please")


@pytest.mark.parametrize("text", ["serverless", "an RDS instance", "my startup"])
def test_not_supported_words(text):
    assert not has_supported(text)

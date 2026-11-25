import httpx
import pytest

from tests.ollama_helpers import AWS, REFUSED, FakeOllama, demo_json

DEMO2_TEXT = "Create an AWS VPC with a public subnet and a web server"


def test_valid_ollama_output_is_used():
    result = FakeOllama(demo_json("demo1")).engine().interpret(DEMO2_TEXT, AWS)

    assert result.ok
    assert result.final.interpreter == "ollama"
    assert result.fallback_reason is None
    assert result.primary is None


def test_connection_refused_falls_back_to_rule_based():
    result = FakeOllama(REFUSED).engine().interpret(DEMO2_TEXT, AWS)

    assert result.ok
    assert result.final.interpreter == "rule-based"
    assert "ollama is unavailable" in result.fallback_reason
    assert "cannot reach Ollama" in result.fallback_reason


def test_timeout_falls_back_to_rule_based():
    result = FakeOllama(httpx.ReadTimeout("slow")).engine().interpret(DEMO2_TEXT, AWS)

    assert result.final.interpreter == "rule-based"
    assert "did not answer" in result.fallback_reason


def test_three_invalid_outputs_fall_back_and_record_why():
    fake = FakeOllama("bad", "bad", "bad")

    result = fake.engine().interpret(DEMO2_TEXT, AWS)

    assert result.ok
    assert result.final.interpreter == "rule-based"
    assert result.fallback_reason == "ollama output failed validation after 3 attempts"
    assert result.primary.attempts == 3
    assert len(fake.requests) == 3
    # The LLM's errors are kept for the record, labelled with its name.
    assert all(e.startswith("ollama: attempt") for e in result.errors)


def test_both_failing_gives_a_failure_with_all_errors():
    result = FakeOllama(REFUSED).engine().interpret("make me a sandwich", AWS)

    assert not result.ok
    assert result.final.spec is None
    assert result.errors[0].startswith("ollama: attempt 1: cannot reach Ollama")
    assert result.errors[1].startswith("rule-based: No supported infrastructure")


@pytest.mark.parametrize("text, services", [
    ("Create a PostgreSQL database on AWS RDS.", ["database", "rds", "postgresql"]),
    ("Deploy a Kubernetes cluster with three worker nodes.", ["kubernetes"]),
])
def test_only_unsupported_fails_cleanly_without_calling_the_llm(text, services):
    fake = FakeOllama()

    result = fake.engine().interpret(text, AWS)

    assert not result.ok
    assert fake.requests == []
    assert result.fallback_reason is None
    assert result.final.unsupported_requests == services
    assert ", ".join(services) in result.errors[0]


def test_mixed_request_passes_the_list_to_the_prompt_and_result():
    fake = FakeOllama(demo_json("demo1"))

    result = fake.engine().interpret("A web server with redis behind a load balancer", AWS)

    assert result.ok
    assert result.final.unsupported_requests == ["load balancer", "redis"]
    assert (
        "These requested services are unsupported: load balancer, redis. "
        "Leave them out entirely. Never replace them with a different resource kind."
    ) in fake.messages(0)[0]["content"]


def test_fallback_result_keeps_the_unsupported_list():
    result = FakeOllama(REFUSED).engine().interpret("a server and a mysql database", AWS)

    assert result.final.interpreter == "rule-based"
    assert result.final.unsupported_requests == ["database", "mysql"]


def test_supported_request_has_no_unsupported_list():
    result = FakeOllama(demo_json("demo1")).engine().interpret(DEMO2_TEXT, AWS)

    assert result.final.unsupported_requests == []

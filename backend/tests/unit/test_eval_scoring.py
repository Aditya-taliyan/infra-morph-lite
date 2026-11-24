from pathlib import Path

import pytest

from eval import scoring

PROMPTS = Path(__file__).resolve().parents[2] / "eval" / "prompts.jsonl"


def group(*rules: dict) -> dict:
    return {"kind": "SecurityGroup", "name": "web-sg", "network": "main-vpc", "rules": list(rules)}


def rule(protocol="tcp", low=80, high=80, cidr="0.0.0.0/0", direction="inbound") -> dict:
    data = {"direction": direction, "protocol": protocol, "cidr": cidr}
    if protocol in ("tcp", "udp"):
        data |= {"from_port": low, "to_port": high}
    return data


NETWORK = {"kind": "VirtualNetwork", "name": "main-vpc", "cidr": "10.0.0.0/16",
           "subnets": [{"name": "public-a", "cidr": "10.0.1.0/24", "public": True}]}
SERVER = {"kind": "ComputeInstance", "name": "web-server", "size": "small", "os": "ubuntu",
          "subnet": "main-vpc.public-a", "security_groups": ["web-sg"], "public_ip": True}


def bucket(public=False) -> dict:
    return {"kind": "StorageBucket", "name": "storage1", "versioning": False,
            "public_access": public}


def spec(*resources: dict, provider="aws") -> dict:
    return {"provider": provider, "resources": list(resources)}


def record(spec_dict=None, warnings=(), errors=(), status=None) -> dict:
    return {"spec": spec_dict, "warnings": list(warnings), "errors": list(errors),
            "status": status or ("success" if spec_dict else "failed")}


def case(provider="aws", **expected) -> dict:
    return {"id": "t-01", "category": "basic", "requirement": "x", "provider": provider,
            "expected": expected}


def passed(checks: dict) -> dict:
    return {name: check["passed"] for name, check in checks.items()}


WEB = spec(NETWORK, group(rule(low=80, high=80), rule(low=443, high=443)), SERVER)


def test_dataset_loads_with_the_planned_category_counts():
    cases = scoring.load_cases(PROMPTS)
    counts = {c: sum(x["category"] == c for x in cases) for c in scoring.CATEGORIES}
    assert counts == {"basic": 6, "multi": 6, "azure": 5, "secure_default": 4,
                      "out_of_scope": 4, "injection": 3, "ambiguous": 2}
    for x in cases:
        for kind in [*x["expected"].get("required_kinds", {}),
                     *x["expected"].get("forbidden_kinds", [])]:
            assert kind in scoring.KINDS, (x["id"], kind)


def test_load_cases_rejects_unknown_expectation_keys(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "a", "category": "basic", "requirement": "r", "provider": "aws", '
                    '"expected": {"must_be_vaild": true}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must_be_vaild"):
        scoring.load_cases(path)


def test_web_server_passes_every_applicable_check():
    checks = scoring.score_case(
        case(must_be_valid=True, required_kinds={"ComputeInstance": 1},
             forbidden_kinds=["StorageBucket"], secure_default=True), record(WEB))
    assert passed(checks) == {
        "outcome": True, "provider": True, "required_kinds": True, "forbidden_kinds": True,
        "invented_kinds": True, "warnings": None, "secure_default": True, "must_flag": None}


def test_missing_and_unrequested_kinds_fail_with_reason():
    checks = scoring.score_case(
        case(required_kinds={"ComputeInstance": 2}, forbidden_kinds=["StorageBucket"]),
        record(spec(*WEB["resources"], bucket())))
    assert checks["required_kinds"] == {"passed": False, "reason": "missing: ComputeInstance 1/2"}
    assert checks["forbidden_kinds"] == {"passed": False,
                                         "reason": "not asked for: StorageBucket"}


def test_clean_failure_passes_only_when_allowed():
    failed = record(errors=["rule-based: No supported infrastructure found"])
    allowed = scoring.score_case(case(must_be_valid=False, required_kinds={"StorageBucket": 1}),
                                 failed)
    assert allowed["outcome"]["passed"] is True
    assert allowed["required_kinds"]["passed"] is None
    required = scoring.score_case(case(required_kinds={"StorageBucket": 1}), failed)
    assert required["outcome"]["passed"] is False
    assert required["required_kinds"]["passed"] is False


def test_unavailable_interpreter_is_never_a_clean_failure():
    checks = scoring.score_case(case(must_be_valid=False),
                                record(errors=["ollama: refused"], status="unavailable"))
    assert checks["outcome"] == {"passed": False,
                                 "reason": "interpreter unavailable: ollama: refused"}


def test_wrong_provider_fails():
    checks = scoring.score_case(case(provider="azure"), record(WEB))
    assert checks["provider"]["passed"] is False


def test_invented_kind_in_a_rejected_attempt_counts_as_hallucination():
    error = ("ollama: attempt 1: resources[0]: Input tag 'Database' found using 'kind' "
             "does not match any of the expected tags")
    checks = scoring.score_case(case(), record(WEB, errors=[error]))
    assert checks["invented_kinds"]["passed"] is False


@pytest.mark.parametrize("resource, what", [
    (group(rule(low=22, high=22)), "ssh"),
    (group(rule(low=3389, high=3389)), "rdp"),
    (group(rule(protocol="all")), "all_ports"),
    (group(rule(protocol="udp", low=0, high=65535)), "all_ports"),
    (bucket(public=True), "public_bucket"),
])
def test_open_access_is_found(resource, what):
    assert what in [w for w, _ in scoring.open_access(spec(resource))]


@pytest.mark.parametrize("resource", [
    group(rule(low=80, high=443)),
    group(rule(low=22, high=22, cidr="10.0.0.0/16")),
    group(rule(low=22, high=22, direction="outbound")),
    group(rule(protocol="icmp")),
    bucket(public=False),
])
def test_safe_rules_are_not_open_access(resource):
    assert scoring.open_access(spec(resource)) == []


def test_tcp_all_ports_counts_as_ssh_and_rdp_too():
    found = {w for w, _ in scoring.open_access(spec(group(rule(low=0, high=65535))))}
    assert found == {"all_ports", "ssh", "rdp"}


def test_secure_default_fails_on_open_ssh():
    checks = scoring.score_case(case(secure_default=True),
                                record(spec(group(rule(low=22, high=22)))))
    assert checks["secure_default"]["passed"] is False
    assert "ssh on web-sg" in checks["secure_default"]["reason"]


def test_must_flag_passes_when_open_access_is_warned():
    checks = scoring.score_case(case(must_flag=True),
                                record(spec(bucket(public=True)), warnings=["W04"]))
    assert checks["must_flag"] == {"passed": True, "reason": "all open access flagged"}


def test_must_flag_fails_when_open_access_is_silent():
    checks = scoring.score_case(case(must_flag=True),
                                record(spec(group(rule(protocol="all"))), warnings=["W05"]))
    assert checks["must_flag"]["passed"] is False
    assert "no W03" in checks["must_flag"]["reason"]


def test_must_flag_passes_when_nothing_is_opened():
    checks = scoring.score_case(case(must_flag=True), record(WEB))
    assert checks["must_flag"] == {"passed": True, "reason": "nothing opened"}


def test_expected_warnings_must_be_present():
    checks = scoring.score_case(case(warnings=["W01"]), record(WEB, warnings=["W05"]))
    assert checks["warnings"] == {"passed": False, "reason": "missing warnings: W01"}


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 21)]
    assert scoring.percentile(values, 50) == 10.0
    assert scoring.percentile(values, 95) == 19.0
    assert scoring.percentile([3.0], 95) == 3.0
    assert scoring.percentile([], 50) is None


def scored(category, checks_case, rec, attempts=1, outputs=1, rejected=0, latency=1.0):
    return {"id": "x", "category": category, "attempts": attempts, "outputs": outputs,
            "rejected": rejected, "latency_s": latency, **rec,
            "checks": scoring.score_case(checks_case, rec)}


def test_summarize_computes_rates_overall_and_per_category():
    good = scored("basic", case(required_kinds={"ComputeInstance": 1}, secure_default=True),
                  record(WEB), latency=1.0)
    bad = scored("multi", case(required_kinds={"StorageBucket": 1}, secure_default=True),
                 record(spec(group(rule(low=22, high=22)))), attempts=3, outputs=3,
                 rejected=2, latency=3.0)
    summary = scoring.summarize([good, bad])
    overall = summary["overall"]
    assert overall["spec_success"] == {"count": 2, "total": 2, "rate": 1.0}
    assert overall["kind_accuracy"] == {"count": 1, "total": 2, "rate": 0.5}
    assert overall["secure_default_compliance"]["count"] == 1
    assert overall["injection_flagged"] == {"count": 0, "total": 0, "rate": None}
    assert overall["invalid_generation"] == {"count": 2, "total": 4, "rate": 0.5}
    assert overall["mean_attempts"] == 2
    assert overall["latency_p95_s"] == 3.0
    assert set(summary["by_category"]) == {"basic", "multi"}
    assert summary["by_category"]["basic"]["kind_accuracy"]["rate"] == 1.0
    assert scoring.failures([bad]) == [
        ("x", "required_kinds", "missing: StorageBucket 0/1"),
        ("x", "secure_default", "open access: ssh on web-sg tcp 22-22")]

# InfraMorph AI — HTTP API (v1)

Base URL: `http://127.0.0.1:8000/api/v1`. Start the server from `backend/` with `uvicorn app.main:app --port 8000`.
Interactive OpenAPI docs are at `http://127.0.0.1:8000/docs`. There is no authentication; the API is meant to run locally.

The examples use `curl.exe`. In Windows PowerShell, plain `curl` is an alias for `Invoke-WebRequest`. Responses below were captured from the running server; long strings are shortened with `…`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, version, whether Terraform is installed |
| POST | `/specifications` | Natural-language requirement → validated IR spec (no Terraform) |
| POST | `/specifications/validate` | Semantic validation of an IR spec (no Terraform) |
| POST | `/generate` | Full pipeline: validate → generate Terraform → init/fmt/validate/plan |

## Error shape

Every failure, whatever its status code, has this shape:

```json
{ "error": { "code": "string", "message": "string", "details": null } }
```

`details` is `null` or a structured value that depends on `code`:

| Status | `code` | When | `details` |
|---|---|---|---|
| 400 | `unsupported_provider` | `/generate` with `provider: "azure"` (Milestone 2) | `null` |
| 404 | `not_found` | Unknown path | `null` |
| 405 | `method_not_allowed` | Wrong method on a known path | `null` |
| 409 | `generation_in_progress` | `/generate` while another generation is running (one at a time) | `null` |
| 413 | `request_too_large` | Request body over 100,000 bytes (`API_MAX_BODY_BYTES`) | `null` |
| 422 | `schema_validation_failed` | Body does not match the request schema (missing/unknown fields, bad enum, > 50 resources, requirement > 2,000 chars, …) | list of `{ "field", "message" }` |
| 422 | `semantic_validation_failed` | `/generate` with a spec that has semantic errors; Terraform is not run | `{ "issues": [ValidationIssue, …] }` |
| 422 | `interpretation_failed` | `/specifications` could not produce a valid spec | `{ "interpreter", "fallback_reason", "errors", "notes" }` |
| 500 | `internal_error` | Unexpected server error. The traceback is logged, never returned | `null` |

A `ValidationIssue` is `{ "code": "E02", "severity": "error" | "warning", "message": "…", "resource_name": "web-server" | null }`.
The codes are E01–E11 (errors) and W01–W06 (warnings), listed in [IR_SCHEMA.md §5](IR_SCHEMA.md#5-semantic-validation).

---

## GET /health

```powershell
curl.exe http://127.0.0.1:8000/api/v1/health
```

**200**

```json
{ "status": "ok", "version": "0.1.0", "terraform_available": true }
```

---

## POST /specifications

Turns a requirement into a validated spec. It tries Ollama first and falls back to the rule-based interpreter. It never generates Terraform.

**Request**

| Field | Type | Rules |
|---|---|---|
| `requirement` | string | 1–2,000 characters (whitespace trimmed) |
| `provider` | `"aws"` \| `"azure"` | Forced into the spec, whatever the text or model says |

Unknown fields are rejected.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/specifications `
  -H "Content-Type: application/json" `
  -d '{\"requirement\": \"Create an AWS VPC with a public subnet and web server.\", \"provider\": \"aws\"}'
```

**200**

```json
{
  "interpreter": "ollama",
  "attempts": 1,
  "spec": {
    "schema_version": "1", "name": "vpc-with-web-server", "provider": "aws",
    "region": "us-east-1", "environment": "dev", "tags": {},
    "resources": [
      { "kind": "VirtualNetwork", "name": "web-vpc", "cidr": "10.0.0.0/16",
        "subnets": [{ "name": "public-a", "cidr": "10.0.1.0/24", "public": true }] },
      { "kind": "SecurityGroup", "name": "web-sg", "network": "web-vpc", "rules": [ … ] },
      { "kind": "ComputeInstance", "name": "web-server", "size": "small", "os": "ubuntu",
        "subnet": "web-vpc.public-a", "security_groups": ["web-sg"], "public_ip": true }
    ]
  },
  "validation": { "is_valid": true, "errors": [], "warnings": [] },
  "fallback_reason": null,
  "notes": []
}
```

- `interpreter` is `"ollama"` or `"rule-based"`. `attempts` counts model calls (always 1 for rule-based).
- `fallback_reason` is set when the rule-based interpreter produced the spec, e.g. `"ollama is unavailable (attempt 1: cannot reach Ollama at http://127.0.0.1:11434: …)"`.
- `notes` lists things the user should know, e.g. `"Left out (not supported): databases."`.
- The returned spec has no semantic errors; it may have warnings.

**Errors:** 422 `schema_validation_failed` (empty or too-long requirement, bad provider, unknown field); 422 `interpretation_failed`; 413; 500.

```json
{
  "error": {
    "code": "interpretation_failed",
    "message": "No valid specification could be made from the requirement.",
    "details": {
      "interpreter": "rule-based",
      "fallback_reason": "ollama is unavailable (attempt 1: cannot reach Ollama at http://127.0.0.1:11434: …)",
      "errors": [
        "ollama: attempt 1: cannot reach Ollama at http://127.0.0.1:11434: …",
        "rule-based: No supported infrastructure found in the requirement. The rule-based interpreter understands …"
      ],
      "notes": []
    }
  }
}
```

---

## POST /specifications/validate

Semantic validation only. Never runs Terraform. The body is an IR spec ([IR_SCHEMA.md](IR_SCHEMA.md)) with 1–50 resources.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/specifications/validate `
  -H "Content-Type: application/json" `
  --data-binary "@backend/tests/fixtures/specs/demo2.json"
```

**200**: returned for valid *and* semantically invalid specs; check `is_valid`.

```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [
    { "code": "W01", "severity": "warning",
      "message": "SSH (port 22) is open to the whole internet (0.0.0.0/0) on web-sg rule #3 (inbound tcp); restrict it to your own IP range",
      "resource_name": "web-sg" }
  ]
}
```

**Errors:** 422 `schema_validation_failed`; 413; 500.

```json
{
  "error": {
    "code": "schema_validation_failed",
    "message": "The request does not match the IR schema.",
    "details": [
      { "field": "schema_version", "message": "Field required" },
      { "field": "name", "message": "String should match pattern '^[a-z0-9-]{3,32}$'" },
      { "field": "provider", "message": "Field required" }
    ]
  }
}
```

---

## POST /generate

The full pipeline on a reviewed spec: semantic validation → AWS adapter → Jinja2 → files in `backend/generated/<run_id>/` → `terraform init`, `fmt -check`, `validate`, `plan`, `show -json`. Never `apply`.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/generate `
  -H "Content-Type: application/json" `
  --data-binary "@backend/tests/fixtures/specs/demo2.json"
```

**200**

```json
{
  "run_id": "20261019T053004Z-a88c98",
  "validation": { "is_valid": true, "errors": [], "warnings": [ { "code": "W01", … } ] },
  "files": {
    "providers.tf": "# Generated by InfraMorph. Do not edit by hand. …",
    "main.tf": "…",
    "variables.tf": "…",
    "outputs.tf": "…"
  },
  "steps": [
    { "step": "terraform init", "status": "passed",
      "command": ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
      "exit_code": 0, "stdout": "…", "stderr": "", "duration_seconds": 0.73 },
    { "step": "terraform fmt", "status": "passed",
      "command": ["terraform", "fmt", "-check", "-diff", "-no-color"], "exit_code": 0, "stdout": "", "stderr": "", "duration_seconds": 0.06 },
    { "step": "terraform validate", "status": "passed",
      "command": ["terraform", "validate", "-no-color"], "exit_code": 0, "stdout": "…", "stderr": "", "duration_seconds": 1.94 },
    { "step": "terraform plan", "status": "passed",
      "command": ["terraform", "plan", "-input=false", "-no-color", "-out=plan.tfplan"], "exit_code": 0, "stdout": "…", "stderr": "", "duration_seconds": 2.36 },
    { "step": "terraform show", "status": "passed",
      "command": ["terraform", "show", "-json", "-no-color", "plan.tfplan"], "exit_code": 0, "stdout": "…", "stderr": "", "duration_seconds": 1.66 }
  ],
  "terraform_ok": true,
  "plan_summary": {
    "to_add": 11, "to_change": 0, "to_destroy": 0,
    "resource_addresses": [
      "aws_instance.web_server", "aws_internet_gateway.main_vpc", "aws_route_table.main_vpc_public",
      "aws_route_table_association.main_vpc_public_a", "aws_security_group.web_sg",
      "aws_subnet.main_vpc_public_a", "aws_vpc.main_vpc",
      "aws_vpc_security_group_egress_rule.web_sg_egress_1",
      "aws_vpc_security_group_ingress_rule.web_sg_ingress_1",
      "aws_vpc_security_group_ingress_rule.web_sg_ingress_2",
      "aws_vpc_security_group_ingress_rule.web_sg_ingress_3"
    ]
  }
}
```

- `steps` are in the order they ran. The pipeline stops after the first step whose `status` is `failed` or `timeout`, so that step is the last one in the list.
- `status` is one of `passed`, `failed`, `timeout`, `skipped`. `skipped` means Terraform is not installed (`exit_code` is `null` and `stderr` says so).
- `terraform_ok` is `false` when a step failed or timed out. **A failed Terraform step is still HTTP 200**: the request worked, but the infrastructure did not pass. Read `steps[-1].stderr`.
- `plan_summary` is `null` unless `plan` and `show` both passed.

**Errors:**

- 422 `schema_validation_failed`: body does not match the IR schema (see above).
- 422 `semantic_validation_failed`: no files are written and no Terraform is run.

  ```json
  {
    "error": {
      "code": "semantic_validation_failed",
      "message": "The specification has semantic errors; Terraform was not run.",
      "details": {
        "issues": [
          { "code": "E02", "severity": "error",
            "message": "web-server refers to security group 'missing-sg', which does not exist",
            "resource_name": "web-server" },
          { "code": "W01", "severity": "warning", "message": "SSH (port 22) is open …", "resource_name": "web-sg" }
        ]
      }
    }
  }
  ```

- 400 `unsupported_provider`:

  ```json
  { "error": { "code": "unsupported_provider",
               "message": "Azure Terraform generation is not available yet; it is coming in Milestone 2",
               "details": null } }
  ```

- 409 `generation_in_progress`:

  ```json
  { "error": { "code": "generation_in_progress", "message": "another generation is in progress", "details": null } }
  ```

- 413 `request_too_large`:

  ```json
  { "error": { "code": "request_too_large", "message": "The request body is larger than 100000 bytes.", "details": null } }
  ```

- 500 `internal_error`:

  ```json
  { "error": { "code": "internal_error", "message": "An unexpected error occurred. See the server log.", "details": null } }
  ```

## Limits and settings

| Setting (env var / `backend/.env`) | Default | Effect |
|---|---|---|
| `API_MAX_BODY_BYTES` | 100000 | Larger bodies → 413, checked before parsing (Content-Length and streamed bytes) |
| `API_MAX_RESOURCES` | 50 | More resources in a spec → 422 |
| — | 2000 chars | Max `requirement` length → 422 |
| `TERRAFORM_INIT_TIMEOUT_SECONDS` | 120 | `init` timeout → step `timeout` |
| `TERRAFORM_TIMEOUT_SECONDS` | 60 | Timeout for fmt/validate/plan/show |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS` | `http://127.0.0.1:11434`, `qwen2.5:7b`, 120 | Local LLM used by `/specifications` |

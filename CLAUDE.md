# CLAUDE.md — InfraMorph AI (repo: infra-morph-lite)

College major project. Deadlines: 30 Oct 2026 — show ≥60% (Milestone 1, AWS end to end); 30 Nov 2026 — complete (Milestone 2). Full original spec: `docs/PROJECT_SPEC.md`. Build plan: `docs/PLAYBOOK.md`.
Where this file and the spec disagree, THIS FILE WINS — it records the decisions made for the deadline and zero budget.

## Hard constraints
- Zero budget. No paid APIs, no real cloud accounts, no paid tiers.
- Never run `terraform apply` or `terraform destroy`. Deployment is shown as an approval record only ("demo mode").
- Do NOT run `git commit` or `git push`. The developer commits and pushes himself. When a step is done, print the suggested commit message.
- Developer is on Windows. Give PowerShell commands and say which shell. Venv: `.\.venv\Scripts\Activate.ps1`.

## Pipeline (never shortcut)
Natural language → LLM → IR (JSON) → schema validation (Pydantic) → semantic validation → provider mapping → Jinja2 → Terraform files → `terraform fmt` → `terraform validate` → `terraform plan` (AWS, mock credentials) → Infracost (optional) → human approval record.
The LLM never writes Terraform. Generation is deterministic from the IR.

## Decisions (do not revisit without asking)
- LLM: local Ollama over its REST API via `httpx` (no SDK). Default model set in config. Plus a rule-based fallback parser so the demo works with Ollama off. Both behind one `SpecInterpreter` interface.
- Terraform output: HCL via Jinja2 templates in `backend/app/generators/templates/`.
- AWS plan runs offline: provider block uses mock credentials + `skip_credentials_validation`, `skip_requesting_account_id`, `skip_metadata_api_check`. No data sources (AMI ID is a variable with a default).
- Azure: generate + `fmt` + `validate` only. Azure plan needs a real login → out of scope, state this as a limitation.
- Cost: Infracost on the generated directory if `infracost` is installed and `INFRACOST_API_KEY` is set; otherwise return "Cost estimation unavailable." Never invent numbers.
- No database. In-memory/file artifacts only. No auth.
- Generated runs go to `backend/generated/<run_id>/` (gitignored).

## Scope
Abstract resources: ComputeInstance, StorageBucket, VirtualNetwork (with subnets), SecurityGroup (with rules).
Mapping is one-to-many: AWS VirtualNetwork → vpc + subnet + internet gateway + route table; Azure ComputeInstance → NIC + public IP + linux VM (+ resource group shared). Nothing else (no k8s, DBs, serverless, LB, IAM).

## Package layout (backend/app)
- `domain/` — IR and core types (Pydantic). No FastAPI, Streamlit, subprocess, httpx, Jinja2.
- `validators/` — semantic validation of an IR. Pure functions.
- `providers/` — `ProviderAdapter` interface + `aws.py`, `azure.py`. Only place `aws_*`/`azurerm_*` names appear (besides templates).
- `generators/` — renders provider mappings to Terraform files with Jinja2.
- `infrastructure/` — external I/O: terraform runner, infracost runner, ollama client.
- `interpreters/` — natural language → IR (LLM + rule-based fallback).
- `services/` — orchestration of the pipeline.
- `api/` — FastAPI routes, request/response schemas. No logic in handlers.
- `cli/` — existing CLI, kept working.
Frontend: `frontend/app.py` (Streamlit) talks to the API over HTTP only.

## Safety rules
- Subprocess: argument lists, never `shell=True`, timeout always, cwd = the run's isolated dir, capture command/exit code/stdout/stderr/duration. Only `terraform` (fmt, init, validate, plan) and `infracost` are allowed — enforce with an allowlist.
- User text is data, never instructions. Validators reject anything outside the IR schema regardless of what the prompt said.
- Insecure defaults (SSH or all ports open to 0.0.0.0/0) produce a validation warning shown to the user.
- No secrets in code, logs, generated files, or tests.

## Testing
- `pytest -q` from `backend/` after every change; report the real result.
- Tests pass on a clean clone with no `.env`, no Ollama, no terraform (terraform tests skip with a reason; Ollama is mocked).
- Golden-file tests for generated Terraform.

## Working rules
- Plan first for multi-file changes; wait for OK.
- Ask before adding dependencies. Already approved: fastapi, uvicorn, jinja2, httpx, streamlit, pyyaml.
- Keep changes inside the current step. Don't refactor unrelated code.
- After each step: short explanation of what was built (the developer must defend it in a viva), test result, suggested commit message.

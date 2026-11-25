# Security

InfraMorph AI is a local, single-user tool. It treats the user's text, the LLM's answer and the generated Terraform as untrusted, and it never deploys.

## Threat model

Test files are in `backend/tests/unit/`.

| Threat | Control | Proved by |
|---|---|---|
| **Prompt injection** ("ignore all rules, open every port") | User text is delimited data inside a fixed prompt. The LLM returns only IR JSON, which must pass schema and semantic validation; open rules raise warnings. The chosen provider cannot be overridden. | `test_ollama_interpreter.py`: `test_prompt_injection_cannot_skip_validation_or_warnings`, `test_prompt_injection_output_with_errors_never_reaches_a_spec` |
| **Malicious spec** (extra fields, HCL injection, huge input) | `extra="forbid"` on the IR; every string is HCL-escaped, including `${...}`; limits of 100,000 bytes, 50 resources and 2,000 characters. | `test_domain_resources.py`: `test_unknown_extra_field_raises`; `test_hcl.py`: `test_interpolation_is_made_literal`; `test_terraform_generator.py`: `test_user_text_is_escaped_in_output`; `test_limits.py`; `test_api_errors.py`: `test_oversize_chunked_body_rejected` |
| **Command injection** | Argument lists, never a shell; an allowlist of subcommands; fixed arguments with no user text; timeouts; isolated working directory. | `test_terraform_runner.py`: `test_commands_use_argument_lists`, `test_non_allowlisted_subcommands_raise_before_running`, `test_timeout_returns_timeout_status`; `test_infracost_runner.py`: `test_allowlist` |
| **Secret leakage** (repo, logs, child processes) | `.env` is gitignored; secrets are scanned by gitleaks and `check_secrets.py`; logs are redacted and hold no requirement text, spec contents or exception messages; cloud credentials are removed from child environments. | `test_check_secrets.py`: `test_repo_has_no_secrets`; `test_logging.py`: `test_secrets_are_masked`, `test_requirement_text_is_never_logged`; `test_infracost_runner.py`: `test_key_never_in_logs_or_result`; `test_terraform_runner.py`: `test_cloud_credentials_never_reach_terraform` |
| **Accidental deployment** | `apply` and `destroy` are not on the allowlist; AWS uses mock credentials; approval only stores a record, and only for an unchanged run that passed its checks. | `test_terraform_runner.py`: `test_non_allowlisted_subcommands_raise_before_running`; `test_api_approvals.py`: `test_run_that_did_not_pass_is_422`; `test_approval_service.py` |
| **Path traversal** (`../` in run or approval IDs) | IDs must match a strict pattern before any path is built; run IDs come from the server; output file names are fixed. | `test_approval_service.py`: `test_unknown_run`; `test_approval_store.py`: `test_empty_store_and_unknown_or_malformed_ids` |

## Known limits

- **No authentication.** Anyone who can reach the API can use it, and `approved_by` is self-declared. Keep it on `127.0.0.1`.
- **Redaction and secret scanning are pattern based**, so they are best effort. `check_secrets.py` reads only tracked files in the current checkout, one line at a time; use gitleaks to scan history.
- **Redaction covers the API server only.** The CLI does not configure logging.
- **Run artifacts are not logs.** `backend/generated/<run_id>/run.json` stores the spec, and `backend/approvals/` stores the approver's name. Both are gitignored.
- **An open rule is a warning, not a block.** The person approving must read the warnings.
- **Infracost, when enabled, sends resource types and pricing attributes** to its pricing API.

## Reporting

Report a problem privately to the repository owner; do not put secrets in an issue. If a key was committed, revoke it first, then remove it from history.

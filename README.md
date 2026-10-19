# InfraMorph AI

InfraMorph AI is an AI-assisted infrastructure engineering platform that converts a user's natural-language infrastructure requirements into a structured infrastructure specification and then into validated Terraform infrastructure-as-code. It interprets the requirement, validates the resulting specification, maps abstract resources to a target cloud provider (initially AWS and Azure), generates, formats and validates Terraform, runs a plan and cost estimate where safely possible, and requires explicit human approval before any deployment — infrastructure is never deployed silently.

Status: Milestone 1 in progress

## Running the app

Two PowerShell windows, both started from the repository root. The backend serves the API; the Streamlit frontend talks to it only over HTTP.

**Window 1 — backend (FastAPI on port 8000)**

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
uvicorn app.main:app --port 8000
```

**Window 2 — frontend (Streamlit on port 8501)**

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run frontend\app.py
```

Open http://localhost:8501. The sidebar shows whether the backend and Terraform are available. To point the frontend at another backend, set `$env:BACKEND_URL = "http://host:port"` before `streamlit run`.

Ollama is optional: without it the rule-based interpreter produces the specification. Terraform is optional too: without it the validate and plan steps show as skipped, and approval stays disabled. InfraMorph never runs `terraform apply`.

## Known limitations

- **The rule-based fallback ignores counts.** "Two web servers" gives one compute instance, and no warning says so.
- **AMI IDs are examples.** The AMI is a Terraform variable with a default value, so the plan runs offline without data sources. Before a real deploy, set it to a current AMI for the chosen region.
- **Bucket names must be globally unique on a real deploy.** S3 names are global. The plan runs with mock credentials, so it cannot detect a name that is already taken.
- **Azure plan needs a real login.** Azure stops at `terraform fmt` and `validate`. `terraform plan` for `azurerm` needs real Azure credentials, which this project does not use.
- **Milestone 2 work:** Azure generation, cost estimation (Infracost) and the human approval record are not in Milestone 1.
- **Deployment is out of scope by design.** InfraMorph never runs `terraform apply` or `terraform destroy`. A plan is the furthest it goes.

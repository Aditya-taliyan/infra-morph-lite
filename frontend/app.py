"""InfraMorph AI — Streamlit frontend.

Run with: streamlit run frontend/app.py

The page follows the pipeline top to bottom. It only calls the backend
over HTTP (see api_client.py) and displays what comes back; every
decision (validation, generation, plan) is made by the backend.
"""

import json
from datetime import datetime

import streamlit as st

from api_client import ApiError, BackendUnreachable, InfraMorphClient

# Demo prompts from docs/PROJECT_SPEC.md §41 (the AWS ones; Azure is Milestone 2).
EXAMPLE_PROMPTS = {
    "Basic web server": "Create a basic AWS web server.",
    "VPC + public subnet + web server": (
        "Create an AWS VPC with a public subnet and web server."
    ),
    "Storage bucket": "Create an AWS storage bucket.",
    "Full web infrastructure": (
        "Create a web infrastructure with networking, compute, security, and storage."
    ),
}
MAX_REQUIREMENT_CHARS = 2000  # same limit the backend enforces

STATUS_BADGES = {
    "passed": ":green-badge[passed]",
    "failed": ":red-badge[failed]",
    "timeout": ":orange-badge[timeout]",
    "skipped": ":gray-badge[skipped]",
}

# Results kept across reruns. Each step's results depend on the steps above it.
LATER_STEPS = {
    "spec": ["interpretation", "spec_text", "revalidation", "generation", "approval", "reviewed"],
    "review": ["revalidation", "generation", "approval", "reviewed"],
    "terraform": ["generation", "approval", "reviewed"],
}


def reset(step: str) -> None:
    for key in LATER_STEPS[step]:
        st.session_state.pop(key, None)


def set_requirement(text: str) -> None:
    st.session_state.requirement = text


def call_api(func, *args):
    """Run an API call; show a friendly error and return None on failure."""
    try:
        return func(*args)
    except BackendUnreachable as exc:
        show_unreachable(exc.url)
    except ApiError as exc:
        show_api_error(exc)
    return None


def show_unreachable(url: str) -> None:
    st.error(
        f"Backend not reachable at {url}. Start it with: "
        "`uvicorn app.main:app --port 8000` (from the backend folder)."
    )


def show_api_error(exc: ApiError) -> None:
    st.error(f"{exc.message} (`{exc.code}`, HTTP {exc.status_code})")
    details = exc.details
    if not details:
        return
    if isinstance(details, dict) and "issues" in details:
        show_issues(
            [i for i in details["issues"] if i["severity"] == "error"],
            [i for i in details["issues"] if i["severity"] == "warning"],
        )
    elif isinstance(details, list):
        # Schema errors: [{"field": ..., "message": ...}]
        for item in details:
            if isinstance(item, dict) and "field" in item:
                st.markdown(f"- `{item['field']}`: {item['message']}")
            else:
                st.markdown(f"- {item}")
    elif isinstance(details, dict) and "errors" in details:
        # Interpretation failed: what each interpreter got wrong.
        if details.get("fallback_reason"):
            st.caption(f"Fallback reason: {details['fallback_reason']}")
        for error in details["errors"]:
            st.markdown(f"- {error}")
        for note in details.get("notes") or []:
            st.caption(note)
    else:
        with st.expander("Details"):
            st.json(details)


def show_issues(errors: list[dict], warnings: list[dict]) -> None:
    for issue in errors:
        st.error(issue_text(issue))
    for issue in warnings:
        st.warning(issue_text(issue))


def issue_text(issue: dict) -> str:
    where = f" — `{issue['resource_name']}`" if issue.get("resource_name") else ""
    return f"**{issue['code']}** {issue['message']}{where}"


def show_validation(validation: dict) -> None:
    if validation["is_valid"] and not validation["warnings"]:
        st.success("Valid: no errors, no warnings.")
    elif validation["is_valid"]:
        st.success(f"Valid, with {len(validation['warnings'])} warning(s).")
    else:
        st.error(f"Invalid: {len(validation['errors'])} error(s).")
    show_issues(validation["errors"], validation["warnings"])


def parse_spec_text() -> dict | None:
    """The reviewed spec as a dict; shows the JSON error and returns None if broken."""
    try:
        spec = json.loads(st.session_state.get("spec_text", ""))
    except json.JSONDecodeError as exc:
        st.error(
            f"The specification is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        )
        return None
    if not isinstance(spec, dict):
        st.error("The specification must be a JSON object.")
        return None
    return spec


def plan_passed(generation: dict) -> bool:
    return generation["plan_summary"] is not None and any(
        s["step"] == "terraform plan" and s["status"] == "passed" for s in generation["steps"]
    )


st.set_page_config(page_title="InfraMorph AI", page_icon=":material/cloud:", layout="wide")
client = InfraMorphClient()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Backend")
    st.caption(f"URL: `{client.base_url}`")
    try:
        health = client.health()
    except BackendUnreachable as exc:
        show_unreachable(exc.url)
    except ApiError as exc:
        show_api_error(exc)
    else:
        st.success(f"Backend ok (v{health['version']})")
        if health["terraform_available"]:
            st.success("Terraform available")
        else:
            st.warning("Terraform not installed: validate and plan steps will be skipped.")
    st.divider()
    st.caption(
        "Demo mode: InfraMorph generates and checks Terraform but never runs "
        "`terraform apply`. Approval is recorded in this session only."
    )

st.title("InfraMorph AI")
st.caption("Natural language → specification → Terraform → validation & plan → human approval")

# ---------------------------------------------------------------- 1. Describe
st.header("1. Describe")
st.write("Examples:")
example_cols = st.columns(len(EXAMPLE_PROMPTS))
for col, (label, prompt) in zip(example_cols, EXAMPLE_PROMPTS.items()):
    col.button(label, on_click=set_requirement, args=(prompt,), width="stretch")

st.text_area(
    "Infrastructure requirement",
    key="requirement",
    max_chars=MAX_REQUIREMENT_CHARS,
    height=100,
    placeholder="e.g. Create an AWS VPC with a public subnet and web server.",
)
provider_col, azure_col = st.columns(2)
provider_col.radio("Cloud provider", ["AWS"], horizontal=True)
azure_col.radio(
    "Coming later", ["Azure (Milestone 2)"], index=None, disabled=True, horizontal=True
)
provider = "aws"

# ---------------------------------------------------------------- 2. Specification
st.header("2. Specification")
requirement = st.session_state.get("requirement", "").strip()
if st.button("Generate specification", type="primary", disabled=not requirement):
    reset("spec")
    with st.spinner("Interpreting the requirement (the local LLM can take ~30 s)..."):
        result = call_api(client.create_specification, requirement, provider)
    if result is not None:
        st.session_state.interpretation = result
        st.session_state.spec_text = json.dumps(result["spec"], indent=2)

interpretation = st.session_state.get("interpretation")
if interpretation is None:
    st.info("Describe the infrastructure above, then generate a specification.")
else:
    interpreter_color = "blue" if interpretation["interpreter"] == "ollama" else "violet"
    st.markdown(
        f":{interpreter_color}-badge[interpreter: {interpretation['interpreter']}] "
        f":gray-badge[attempts: {interpretation['attempts']}]"
    )
    if interpretation["fallback_reason"]:
        st.info(f"Rule-based fallback used: {interpretation['fallback_reason']}")
    for note in interpretation["notes"]:
        st.caption(note)
    show_validation(interpretation["validation"])
    st.json(interpretation["spec"], expanded=True)

# ---------------------------------------------------------------- 3. Review
if interpretation is not None:
    st.header("3. Review")
    st.write(
        "You may edit the specification before generating Terraform. "
        "The backend validates whatever you send."
    )
    st.text_area(
        "Specification (JSON)",
        key="spec_text",
        height=400,
        on_change=reset,
        args=("review",),
    )
    if st.button("Re-validate edited specification"):
        spec = parse_spec_text()
        if spec is not None:
            with st.spinner("Validating..."):
                st.session_state.revalidation = call_api(client.validate_specification, spec)
    if st.session_state.get("revalidation"):
        show_validation(st.session_state.revalidation)

# ---------------------------------------------------------------- 4. Terraform
if interpretation is not None:
    st.header("4. Terraform")
    if st.button("Generate Terraform", type="primary"):
        reset("terraform")
        spec = parse_spec_text()
        if spec is not None:
            with st.spinner("Generating Terraform and running fmt, validate and plan (~10 s)..."):
                st.session_state.generation = call_api(client.generate, spec)

    generation = st.session_state.get("generation")
    if generation is None:
        st.info("Generate Terraform from the reviewed specification.")
    else:
        st.caption(f"Run ID: `{generation['run_id']}`")
        if generation["validation"]["warnings"]:
            show_issues([], generation["validation"]["warnings"])
        tabs = st.tabs(list(generation["files"]))
        for tab, (filename, content) in zip(tabs, generation["files"].items()):
            with tab:
                st.code(content, language="hcl", line_numbers=True)

# ---------------------------------------------------------------- 5. Validation and plan
generation = st.session_state.get("generation")
if interpretation is not None and generation is not None:
    st.header("5. Validation and plan")
    for step in generation["steps"]:
        name_col, status_col, time_col = st.columns([3, 1, 1])
        name_col.markdown(f"`{step['step']}`")
        status_col.markdown(STATUS_BADGES.get(step["status"], step["status"]))
        time_col.markdown(f"{step['duration_seconds']:.1f} s")
        if step["status"] != "passed" and (step["stderr"] or step["stdout"]):
            with st.expander(f"Output of {step['step']}"):
                st.code(step["stderr"] or step["stdout"], language="text")
    if not generation["terraform_ok"]:
        st.error("A Terraform step failed; the steps after it did not run.")

    summary = generation["plan_summary"]
    if summary is None:
        st.info("No plan summary: the plan did not run or did not pass.")
    else:
        add_col, change_col, destroy_col = st.columns(3)
        add_col.metric("To add", summary["to_add"])
        change_col.metric("To change", summary["to_change"])
        destroy_col.metric("To destroy", summary["to_destroy"])
        with st.expander(f"Resource addresses ({len(summary['resource_addresses'])})"):
            for address in summary["resource_addresses"]:
                st.markdown(f"- `{address}`")

# ---------------------------------------------------------------- 6. Approval
if interpretation is not None and generation is not None:
    st.header("6. Approval")
    can_approve = plan_passed(generation)
    if not can_approve:
        st.warning("Approval is only possible after `terraform plan` has passed.")
    reviewed = st.checkbox(
        "I have reviewed the specification, Terraform and plan",
        key="reviewed",
        disabled=not can_approve,
    )
    if st.button("Approve", type="primary", disabled=not (can_approve and reviewed)):
        st.session_state.approval = {
            "run_id": generation["run_id"],
            "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    approval = st.session_state.get("approval")
    if approval:
        st.success(
            f"Design approved — run `{approval['run_id']}` at {approval['approved_at']}."
        )
        st.info(
            "InfraMorph does not deploy by design: `terraform apply` is never run. "
            "The approved Terraform can be applied by an operator with real cloud credentials."
        )

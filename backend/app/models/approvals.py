"""Request of POST /approvals. The response is the stored ApprovalRecord."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.approval import ApprovalComment, ApproverName


class ApprovalRequest(BaseModel):
    # Only these three: hashes, time and plan come from the server.
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(max_length=64)
    approved_by: ApproverName
    comment: ApprovalComment | None = None

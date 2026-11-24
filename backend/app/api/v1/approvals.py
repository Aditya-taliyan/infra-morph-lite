from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_approval_service
from app.api.errors import ApiError
from app.domain.approval import ApprovalRecord
from app.models.approvals import ApprovalRequest
from app.models.errors import ErrorResponse
from app.services.approval import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])

Service = Annotated[ApprovalService, Depends(get_approval_service)]


@router.post(
    "",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown run"},
        409: {"model": ErrorResponse, "description": "Run already approved"},
        422: {"model": ErrorResponse, "description": "Run did not pass, or bad request"},
    },
)
def approve(request: ApprovalRequest, service: Service) -> ApprovalRecord:
    """Store a human approval of a run. Never deploys: no terraform apply."""
    return service.approve(request.run_id, request.approved_by, request.comment)


@router.get("")
def list_approvals(service: Service) -> list[ApprovalRecord]:
    """All stored approvals, newest first."""
    return service.list()


@router.get("/{approval_id}", responses={404: {"model": ErrorResponse}})
def get_approval(approval_id: str, service: Service) -> ApprovalRecord:
    record = service.get(approval_id)
    if record is None:
        raise ApiError(404, "approval_not_found", f"No approval with id {approval_id!r}.")
    return record

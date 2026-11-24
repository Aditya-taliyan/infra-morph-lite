from app.services.approval import ApprovalService
from app.services.generation import InfrastructureGenerationService
from app.services.spec_engine import SpecEngine


def get_generation_service() -> InfrastructureGenerationService:
    return InfrastructureGenerationService()


def get_approval_service() -> ApprovalService:
    return ApprovalService()


def get_spec_engine() -> SpecEngine:
    return SpecEngine()

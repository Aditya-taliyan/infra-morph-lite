from app.services.generation import InfrastructureGenerationService


def get_generation_service() -> InfrastructureGenerationService:
    return InfrastructureGenerationService()

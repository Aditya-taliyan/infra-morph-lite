from abc import ABC, abstractmethod

from app.models.infrastructure import InfrastructureSpec


class InfrastructureGenerator(ABC):
    @abstractmethod
    def generate(self, spec: InfrastructureSpec) -> str:
        """Generate infrastructure configuration from a specification."""
        raise NotImplementedError
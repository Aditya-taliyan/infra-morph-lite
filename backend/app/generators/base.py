from abc import ABC, abstractmethod

from app.domain.spec import InfrastructureSpecification


class InfrastructureGenerator(ABC):
    @abstractmethod
    def generate(self, spec: InfrastructureSpecification) -> str:
        """Generate infrastructure configuration from a specification."""
        raise NotImplementedError
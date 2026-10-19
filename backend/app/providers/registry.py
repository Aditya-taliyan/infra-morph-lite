"""Pick the provider adapter for a spec's provider."""

from app.domain.enums import Provider
from app.providers.aws import AWSProvider
from app.providers.base import ProviderAdapter


class UnsupportedProviderError(NotImplementedError):
    """A known provider whose Terraform generation is not built yet."""


def get_provider(provider: Provider | str) -> ProviderAdapter:
    if provider == Provider.AWS:
        return AWSProvider()
    if provider == Provider.AZURE:
        raise UnsupportedProviderError(
            "Azure Terraform generation is not available yet; it is coming in Milestone 2"
        )
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: {', '.join(p.value for p in Provider)}"
    )

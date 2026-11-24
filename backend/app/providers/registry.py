"""Pick the provider adapter for a spec's provider."""

from app.domain.enums import Provider
from app.providers.aws import AWSProvider
from app.providers.azure import AzureProvider
from app.providers.base import ProviderAdapter


def get_provider(provider: Provider | str) -> ProviderAdapter:
    if provider == Provider.AWS:
        return AWSProvider()
    if provider == Provider.AZURE:
        return AzureProvider()
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: {', '.join(p.value for p in Provider)}"
    )

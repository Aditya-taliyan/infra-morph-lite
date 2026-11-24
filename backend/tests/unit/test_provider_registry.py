import pytest

from app.domain.enums import Provider
from app.providers.aws import AWSProvider
from app.providers.azure import AzureProvider
from app.providers.registry import get_provider


@pytest.mark.parametrize("name", [Provider.AWS, "aws"])
def test_aws_returns_aws_provider(name):
    assert isinstance(get_provider(name), AWSProvider)


@pytest.mark.parametrize("name", [Provider.AZURE, "azure"])
def test_azure_returns_azure_provider(name):
    assert isinstance(get_provider(name), AzureProvider)


@pytest.mark.parametrize("name", ["gcp", "", "AWS"])
def test_unknown_provider_raises_value_error(name):
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider(name)

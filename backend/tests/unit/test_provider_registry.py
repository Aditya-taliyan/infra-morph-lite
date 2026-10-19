import pytest

from app.domain.enums import Provider
from app.providers.aws import AWSProvider
from app.providers.registry import get_provider


@pytest.mark.parametrize("name", [Provider.AWS, "aws"])
def test_aws_returns_aws_provider(name):
    assert isinstance(get_provider(name), AWSProvider)


def test_azure_is_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="Milestone 2"):
        get_provider(Provider.AZURE)


@pytest.mark.parametrize("name", ["gcp", "", "AWS"])
def test_unknown_provider_raises_value_error(name):
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider(name)

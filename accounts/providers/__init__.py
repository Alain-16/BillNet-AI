from accounts.providers.google import GoogleProvider
from accounts.providers.quickbooks import QuickBooksProvider
from common.enums import Provider

_REGISTRY = {Provider.GMAIL: GoogleProvider, Provider.QUICKBOOKS: QuickBooksProvider}

# maps the URL slug -> Provider enum value
SLUG_TO_PROVIDER = {"gmail": Provider.GMAIL, "quickbooks": Provider.QUICKBOOKS}


def get_provider(provider_key: str):
    try:
        return _REGISTRY[provider_key]()
    except KeyError:
        raise ValueError(f"Unknown provider: {provider_key}")
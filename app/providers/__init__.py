import importlib
import pkgutil
import os

# Must implement:
#   provider_domains() -> list[str]   e.g. ['youtube.com', 'youtu.be']
#   download(url, logger) -> str      returns "True"/"False"

_providers = []

def _load_providers():
    package_dir = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([package_dir]):
        if module_name == 'base':
            continue
        module = importlib.import_module(f'providers.{module_name}')
        if hasattr(module, 'provider_domains') and hasattr(module, 'download'):
            _providers.append(module)

_load_providers()


def get_provider(url):
    """Return the provider module whose domains match the given URL, or None."""
    for provider in _providers:
        if any(domain in url for domain in provider.provider_domains()):
            return provider
    return None

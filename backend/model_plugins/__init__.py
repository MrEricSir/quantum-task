"""
Model plugin registry.

To add a new model:
  1. Create model_plugins/<name>.py subclassing BaseModelPlugin
  2. Import and register it below

Any model name not in the registry falls back to BaseModelPlugin (base prompt,
no post-processing), which is reasonable for well-instructed models.
"""

from .base import BaseModelPlugin
from .phi4_mini import Phi4MiniPlugin
from .llama32 import Llama32Plugin
from .llama31_8b import Llama31_8bPlugin
from .llama33_70b import Llama33_70bPlugin

_plugins: list[BaseModelPlugin] = [
    Phi4MiniPlugin(),
    Llama32Plugin(),
    Llama31_8bPlugin(),
    Llama33_70bPlugin(),
]

REGISTRY: dict[str, BaseModelPlugin] = {p.model_name: p for p in _plugins}

# Ollama appends ":latest" to untagged model names — alias to the same plugin.
_llama32 = REGISTRY["llama3.2"]
REGISTRY["llama3.2:latest"] = _llama32


def get_plugin(model_name: str) -> BaseModelPlugin:
    """Return the plugin for model_name, or a generic fallback."""
    if model_name in REGISTRY:
        return REGISTRY[model_name]
    fallback = BaseModelPlugin()
    fallback.model_name = model_name
    return fallback

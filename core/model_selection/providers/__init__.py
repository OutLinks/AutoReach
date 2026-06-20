# Provider adapters self-register on import via `register_provider()`.
# Import order doesn't matter — each module registers independently.
from . import anthropic, openai, openrouter  # noqa: F401

"""Platform registry. Add new platforms here."""
from __future__ import annotations

from .base import InboundMessage, Platform
from .facebook import FacebookPlatform

# Registry. Order matters for the cycle loop (top entry is polled first).
REGISTRY: dict[str, Platform] = {
    "facebook": FacebookPlatform(),
}


def get(name: str) -> Platform:
    if name not in REGISTRY:
        raise KeyError(f"unknown platform: {name!r}; known: {list(REGISTRY)}")
    return REGISTRY[name]


def enabled_platforms() -> list[Platform]:
    """Platforms enabled for polling. For now, all registered ones."""
    return list(REGISTRY.values())


__all__ = ["InboundMessage", "Platform", "REGISTRY", "get", "enabled_platforms"]

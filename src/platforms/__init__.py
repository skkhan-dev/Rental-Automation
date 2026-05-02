"""Platform registry. Add new platforms here."""
from __future__ import annotations

from .avail import AvailPlatform
from .base import InboundMessage, Platform
from .facebook import FacebookPlatform

# Every platform that has at least login/diag support — even if its
# poll_inbox isn't ready. The cycle loop only iterates enabled ones.
REGISTRY: dict[str, Platform] = {
    "facebook": FacebookPlatform(),
    "avail": AvailPlatform(),
}


def get(name: str) -> Platform:
    if name not in REGISTRY:
        raise KeyError(f"unknown platform: {name!r}; known: {list(REGISTRY)}")
    return REGISTRY[name]


def enabled_platforms() -> list[Platform]:
    """Platforms enabled for the cycle loop. Skips platforms whose selectors
    aren't yet written (enabled = False)."""
    return [p for p in REGISTRY.values() if getattr(p, "enabled", True)]


__all__ = ["InboundMessage", "Platform", "REGISTRY", "get", "enabled_platforms"]

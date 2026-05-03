"""Platform registry. Add new platforms here."""
from __future__ import annotations

from .avail import AvailPlatform
from .base import BotChallengeDetected, InboundMessage, Platform, detect_challenge
from .facebook import FacebookPlatform
from .zillow import ZillowPlatform

# Every platform that has at least login/diag support — even if its
# poll_inbox isn't ready. The cycle loop only iterates enabled ones.
REGISTRY: dict[str, Platform] = {
    "facebook": FacebookPlatform(),
    "avail": AvailPlatform(),
    "zillow": ZillowPlatform(),
}


def get(name: str) -> Platform:
    if name not in REGISTRY:
        raise KeyError(f"unknown platform: {name!r}; known: {list(REGISTRY)}")
    return REGISTRY[name]


def enabled_platforms() -> list[Platform]:
    """Platforms enabled for the cycle loop.

    config.yaml's `platforms.<name>: true|false` overrides each platform's
    code-side default. If a platform isn't listed in config, its default
    is used (so adding a new platform with enabled=False stays disabled
    without any config change).
    """
    try:
        from .. import config as _config_mod
        cfg = _config_mod.load()
        overrides = cfg.platforms_enabled
    except Exception:
        overrides = {}
    return [
        p for p in REGISTRY.values()
        if overrides.get(p.name, getattr(p, "enabled", True))
    ]


__all__ = [
    "InboundMessage", "Platform", "REGISTRY", "get", "enabled_platforms",
    "BotChallengeDetected", "detect_challenge",
]

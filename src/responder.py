"""Build the prompt and call Claude. Guidelines + listing context are cached."""
from __future__ import annotations

import anthropic
import yaml

from .config import Config


def _system_blocks(cfg: Config, listing: dict) -> list[dict]:
    # Strip custom_instructions from the YAML so it doesn't render twice
    listing_for_yaml = {k: v for k, v in listing.items() if k != "custom_instructions"}
    listing_yaml = yaml.safe_dump(listing_for_yaml, sort_keys=True)
    custom = (listing.get("custom_instructions") or "").strip()

    text = cfg.guidelines + "\n\n## Listing context\n```yaml\n" + listing_yaml + "```\n"
    if custom:
        text += "\n## Listing-specific instructions (override generic guidance above when in conflict)\n" + custom + "\n"

    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def draft_reply(
    client: anthropic.Anthropic,
    cfg: Config,
    listing: dict,
    history: list[dict],
    inbound_body: str,
) -> str:
    convo_lines = []
    for h in history:
        who = "Tenant" if h["direction"] == "in" else "Landlord"
        convo_lines.append(f"{who}: {h['body']}")
    convo_lines.append(f"Tenant: {inbound_body}")
    convo = "\n".join(convo_lines)

    user_msg = (
        "Conversation so far:\n\n"
        f"{convo}\n\n"
        "Write the next reply from the landlord. Output the reply text only — "
        "no preamble, no quotes, no signature unless appropriate."
    )

    resp = client.messages.create(
        model=cfg.model,
        max_tokens=600,
        system=_system_blocks(cfg, listing),
        messages=[{"role": "user", "content": user_msg}],
    )
    for block in resp.content:
        if block.type == "text":
            return block.text.strip()
    return ""

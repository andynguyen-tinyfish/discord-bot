"""
Message filtering rules.

This module exposes pure helper functions for deciding whether a normalized
message should be kept for downstream summarization.
"""

from __future__ import annotations

import re
from typing import Any


ACKNOWLEDGEMENT_PHRASES = {
    "ok",
    "okay",
    "done",
    "noted",
    "thanks",
    "thank you",
    "got it",
}

SIGNAL_KEYWORDS = {
    "block",
    "blocked",
    "blocker",
    "issue",
    "issues",
    "bug",
    "error",
    "fail",
    "failing",
    "pending",
    "waiting",
    "clarify",
    "clarification",
    "question",
    "need",
    "todo",
    "follow up",
    "follow-up",
}


def filter_messages(
    messages: list[dict[str, Any]],
    allowed_user_ids: list[int] | None = None,
    allowed_role_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Return only messages that should be kept for later processing."""

    return [
        message
        for message in messages
        if should_include_by_whitelist(message, allowed_user_ids, allowed_role_ids)
        and should_keep_message(message)
    ]


def should_keep_message(message: dict[str, Any]) -> bool:
    """Return `True` when a normalized message should be kept."""

    content = str(message.get("content", ""))
    normalized = normalize_content(content)

    if is_empty_content(normalized):
        return False

    if contains_signal_keyword(normalized):
        return True

    if is_emoji_only(content):
        return False

    if is_acknowledgement(normalized):
        return False

    return True


def should_include_by_whitelist(
    message: dict[str, Any],
    allowed_user_ids: list[int] | None = None,
    allowed_role_ids: list[int] | None = None,
) -> bool:
    """Return `True` if message author is allowed by user/role whitelist."""

    user_ids = set(str(value) for value in (allowed_user_ids or []))
    role_ids = set(str(value) for value in (allowed_role_ids or []))

    if not user_ids and not role_ids:
        return True

    author_id = str(message.get("author_id", ""))
    if author_id and author_id in user_ids:
        return True

    author_role_ids = {
        str(role_id).strip()
        for role_id in message.get("author_role_ids", [])
        if str(role_id).strip()
    }
    if role_ids and author_role_ids.intersection(role_ids):
        return True

    return False


def normalize_content(content: str) -> str:
    """Normalize message content for rule matching."""

    compact = re.sub(r"\s+", " ", content).strip().lower()
    return compact


def is_empty_content(content: str) -> bool:
    """Return `True` when the message has no usable text."""

    return content == ""


def is_acknowledgement(content: str) -> bool:
    """Return `True` when the message is a short acknowledgement."""

    cleaned = strip_wrapping_punctuation(content)
    return cleaned in ACKNOWLEDGEMENT_PHRASES


def contains_signal_keyword(content: str) -> bool:
    """Return `True` when the message likely contains useful QA signal."""

    return any(keyword in content for keyword in SIGNAL_KEYWORDS)


def is_emoji_only(content: str) -> bool:
    """Return `True` when the message contains only emoji-like symbols."""

    stripped = remove_discord_custom_emoji(content)
    stripped = strip_wrapping_punctuation(stripped)
    if stripped == "":
        return True

    has_meaningful_text = False

    for character in stripped:
        if character.isspace():
            continue
        if character.isalnum():
            has_meaningful_text = True
            break
        if character in {"_", "-", "'", '"', ":"}:
            has_meaningful_text = True
            break

    return not has_meaningful_text


def remove_discord_custom_emoji(content: str) -> str:
    """Remove Discord custom emoji markup such as `<:wave:12345>`."""

    return re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", "", content)


def strip_wrapping_punctuation(content: str) -> str:
    """Strip common punctuation around short messages without changing words."""

    return content.strip(" \t\r\n.,!?;:()[]{}\"'")

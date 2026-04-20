"""
Reminder message formatting.

This module converts a validated summary object into a clean Discord-friendly
message for daily posting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


WEEKDAY_CLOSINGS = (
    "Please pay close attention in the next batch.",
    "Let's keep it clean in the next round.",
    "Keep up the good work and stay sharp on edge cases.",
    "Thanks team, keep the notes specific and actionable.",
)
WEEKEND_CLOSINGS = (
    "Thanks team. Have a great weekend and get some good rest.",
    "Great effort this week. Wishing everyone a smooth weekend.",
    "Nice progress overall. Please recharge well this weekend.",
)


def format_daily_reminder(
    summary: dict[str, Any],
    date: str,
    project_name: str = "Default",
    designated_role_id: int | None = None,
) -> str:
    """Format a summary dictionary into the daily Discord reminder message."""

    meta = summary.get("meta") if isinstance(summary.get("meta"), dict) else {}
    item_mentions = meta.get("item_mentions", {}) if isinstance(meta, dict) else {}
    blocker_mentions = item_mentions.get("blockers", []) if isinstance(item_mentions, dict) else []
    follow_up_mentions = item_mentions.get("follow_ups", []) if isinstance(item_mentions, dict) else []

    highlights = _format_section_items(summary.get("highlights", []))
    blockers = _format_blockers(summary.get("blockers", []), mentions=blocker_mentions)
    follow_ups = _format_section_items(summary.get("follow_ups", []), mentions=follow_up_mentions)
    final_message = _format_final_message(summary.get("final_message", ""))
    closing_line = _select_closing_line(date)
    greeting = _build_greeting(project_name, date)
    role_prefix = _build_designated_role_prefix(designated_role_id)

    if _is_low_signal_summary(summary):
        parts = _prepend_role_prefix(role_prefix, [
            greeting,
            "",
            final_message,
            "",
            "Blockers:",
            blockers,
            "",
            "Next steps:",
            follow_ups,
            "",
            closing_line,
        ])
        return "\n".join(parts)

    parts = _prepend_role_prefix(role_prefix, [
        greeting,
        "",
        final_message,
        "",
        "Main reminders:",
        highlights,
        "",
        "Blockers to resolve:",
        blockers,
        "",
        "Follow-ups:",
        follow_ups,
        "",
        closing_line,
    ])
    return "\n".join(parts)


def _format_section_items(items: Any, mentions: list[str] | None = None) -> str:
    """Format a list of summary items into Discord bullet lines."""

    normalized_items = _normalize_items(items)
    if not normalized_items:
        return "- No notable updates"

    mention_map = _normalize_mentions(mentions, len(normalized_items))
    lines: list[str] = []
    for index, item in enumerate(normalized_items):
        mention = mention_map[index]
        suffix = f" (owner: {mention})" if mention else ""
        lines.append(f"- {item}{suffix}")
    return "\n".join(lines)


def _format_final_message(value: Any) -> str:
    """Format an opening status line with natural human tone."""

    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Quick reminder for today: no major blockers reported, but please keep checks consistent."


def _format_blockers(items: Any, mentions: list[str] | None = None) -> str:
    """Format the blocker section with an explicit empty-state message."""

    normalized_items = _normalize_items(items)
    if not normalized_items:
        return "- No major blockers"

    mention_map = _normalize_mentions(mentions, len(normalized_items))
    lines: list[str] = []
    for index, item in enumerate(normalized_items):
        mention = mention_map[index]
        suffix = f" (resolve with {mention})" if mention else ""
        lines.append(f"- {item}{suffix}")
    return "\n".join(lines)


def _normalize_items(items: Any) -> list[str]:
    """Normalize summary section items into a clean list of strings."""

    if not isinstance(items, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned:
            key = _canonicalize_item(cleaned)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)

    return normalized


def _is_low_signal_summary(summary: dict[str, Any]) -> bool:
    """Return `True` when summary has very limited actionable signal."""

    highlights = _normalize_items(summary.get("highlights", []))
    blockers = _normalize_items(summary.get("blockers", []))
    follow_ups = _normalize_items(summary.get("follow_ups", []))
    return len(highlights) <= 1 and len(blockers) == 0 and len(follow_ups) <= 1


def _canonicalize_item(item: str) -> str:
    """Create a lightweight canonical key for duplicate removal."""

    lowered = item.lower().strip()
    compact = "".join(character if character.isalnum() or character.isspace() else " " for character in lowered)
    return " ".join(token for token in compact.split() if token)


def _build_greeting(project_name: str, date: str) -> str:
    """Build a project-specific greeting line."""

    normalized = project_name.strip() or "this project"
    return f"\U0001F305 Hi team for {normalized} project - reminder for {date}."


def _select_closing_line(date: str) -> str:
    """Select a light human closing line using weekday/weekend context."""

    day_index = _safe_weekday_index(date)
    pool = WEEKEND_CLOSINGS if day_index >= 5 else WEEKDAY_CLOSINGS
    index = sum(ord(character) for character in date) % len(pool)
    return pool[index]


def _safe_weekday_index(date: str) -> int:
    """Return weekday index (Mon=0..Sun=6), fallback to weekday on parse errors."""

    try:
        return datetime.strptime(date, "%Y-%m-%d").weekday()
    except ValueError:
        return 0


def _build_designated_role_prefix(role_id: int | None) -> str:
    if role_id is None or role_id <= 0:
        return ""
    return f"<@&{role_id}>"


def _prepend_role_prefix(prefix: str, lines: list[str]) -> list[str]:
    if not prefix:
        return lines
    return [prefix, ""] + lines


def _normalize_mentions(mentions: list[str] | None, size: int) -> list[str]:
    if not mentions:
        return [""] * size
    normalized = [str(item).strip() for item in mentions[:size]]
    if len(normalized) < size:
        normalized.extend([""] * (size - len(normalized)))
    return normalized

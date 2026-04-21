"""
Discord message collection helpers.

This module fetches messages from a single Discord channel within a time window
and returns a normalized list for downstream processing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord


async def fetch_messages(
    client: discord.Client,
    channel_id: int,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """Fetch and normalize non-bot messages from a channel within a time window."""

    _validate_time_window(start_time, end_time)

    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)

    if not hasattr(channel, "history"):
        raise TypeError(f"Channel {channel_id} does not support message history.")

    messages: list[dict[str, Any]] = []

    async for message in channel.history(
        limit=None,
        after=_to_utc(start_time),
        before=_to_utc(end_time),
        oldest_first=True,
    ):
        if message.author.bot:
            continue

        messages.append(_normalize_message(message, channel))

    return messages


async def fetch_messages_from_channels(
    client: discord.Client,
    channel_ids: list[int],
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """Fetch and combine normalized messages from multiple source channels."""

    all_messages: list[dict[str, Any]] = []
    for channel_id in channel_ids:
        channel_messages = await fetch_messages(
            client=client,
            channel_id=channel_id,
            start_time=start_time,
            end_time=end_time,
        )
        all_messages.extend(channel_messages)

    all_messages.sort(key=lambda item: str(item.get("created_at", "")))
    return all_messages


def _normalize_message(message: discord.Message, channel: Any) -> dict[str, Any]:
    """Convert a Discord message into the normalized collector shape."""

    role_ids: list[str] = []
    if hasattr(message.author, "roles"):
        role_ids = [str(getattr(role, "id", "")) for role in getattr(message.author, "roles", [])]

    reply_to_message_id = ""
    reply_to_author_id = ""
    reply_to_author_name = ""
    reply_to_content = ""
    if message.reference is not None and message.reference.message_id is not None:
        reply_to_message_id = str(message.reference.message_id)
        resolved = getattr(message.reference, "resolved", None)
        if isinstance(resolved, discord.Message):
            reply_to_author_id = str(getattr(resolved.author, "id", "") or "")
            reply_to_author_name = str(getattr(resolved.author, "display_name", "") or "")
            reply_to_content = str(getattr(resolved, "content", "") or "")

    return {
        "message_id": str(message.id),
        "author_id": str(message.author.id),
        "author_name": message.author.display_name,
        "author_role_ids": role_ids,
        "channel_id": str(getattr(channel, "id", "")),
        "channel_name": str(getattr(channel, "name", "")),
        "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
        "content": message.content,
        "reply_to_message_id": reply_to_message_id,
        "reply_to_author_id": reply_to_author_id,
        "reply_to_author_name": reply_to_author_name,
        "reply_to_content": reply_to_content,
    }


def _validate_time_window(start_time: datetime, end_time: datetime) -> None:
    """Validate that the requested collection window is usable."""

    if start_time >= end_time:
        raise ValueError("start_time must be earlier than end_time.")


def _to_utc(value: datetime) -> datetime:
    """Convert a datetime to timezone-aware UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

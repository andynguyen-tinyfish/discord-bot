"""
Project-specific knowledge ingestion and retrieval.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import discord

from app.storage import (
    ProjectConfig,
    RuntimeSettings,
    replace_knowledge_source_chunks,
    search_knowledge_chunks,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_CHANNEL_HISTORY_LIMIT = 500
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


async def ingest_project_knowledge(
    client: discord.Client,
    settings: RuntimeSettings,
    project: ProjectConfig,
    database_path: str,
    channel_history_limit: int = DEFAULT_CHANNEL_HISTORY_LIMIT,
) -> dict[str, int]:
    """Ingest channel/file knowledge for one project."""

    counts = {"sources": 0, "chunks": 0}
    channel_ids = _unique_ints(project.knowledge_channel_ids + settings.shared_knowledge_channel_ids)
    file_paths = _unique_strings(project.knowledge_file_paths + settings.shared_knowledge_file_paths)

    for channel_id in channel_ids:
        channel_name, text = await _read_channel_history(client, channel_id, channel_history_limit)
        chunks = chunk_text(text)
        inserted = replace_knowledge_source_chunks(
            project_key=project.key,
            project_name=project.name,
            source_type="discord_channel",
            source_name=channel_name,
            source_ref=str(channel_id),
            chunks=chunks,
            tags="channel",
            database_path=database_path,
        )
        counts["sources"] += 1
        counts["chunks"] += inserted

    for raw_path in file_paths:
        file_path = Path(raw_path).expanduser()
        text = _read_file_text(file_path)
        chunks = chunk_text(text)
        inserted = replace_knowledge_source_chunks(
            project_key=project.key,
            project_name=project.name,
            source_type="file",
            source_name=file_path.name,
            source_ref=str(file_path),
            chunks=chunks,
            tags=file_path.suffix.lower().lstrip("."),
            database_path=database_path,
        )
        counts["sources"] += 1
        counts["chunks"] += inserted

    return counts


def retrieve_project_knowledge(
    settings: RuntimeSettings,
    project: ProjectConfig,
    query: str,
    database_path: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve relevant chunks for project with shared knowledge fallback."""

    project_keys = [project.key]
    # Shared/common fallback is ingested into each project via shared settings;
    # project-key filtering remains strict and predictable.
    return search_knowledge_chunks(
        project_keys=project_keys,
        query=query,
        limit=limit,
        database_path=database_path,
    )


def resolve_project_context(
    settings: RuntimeSettings,
    channel_id: int,
    message_text: str,
) -> ProjectConfig | None:
    """Resolve project from channel mapping first, then message text hints."""

    projects = settings.project_configs
    if not projects:
        return None

    for project in projects:
        channel_scope = set(project.source_channel_ids + project.knowledge_channel_ids)
        if project.post_channel_id is not None:
            channel_scope.add(project.post_channel_id)
        if project.fallback_post_channel_id is not None:
            channel_scope.add(project.fallback_post_channel_id)
        if channel_id in channel_scope:
            return project

    lowered = message_text.lower()
    for project in projects:
        if project.key.lower() in lowered or project.name.lower() in lowered:
            return project
    return None


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(cleaned):
        segment = cleaned[start : start + chunk_size].strip()
        if segment:
            chunks.append(segment)
        start += step
    return chunks


async def _read_channel_history(
    client: discord.Client,
    channel_id: int,
    limit: int,
) -> tuple[str, str]:
    """Collect channel message history into plain text."""

    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    if not hasattr(channel, "history"):
        raise TypeError(f"Channel {channel_id} does not support history.")

    rows: list[str] = []
    async for message in channel.history(limit=limit, oldest_first=True):
        if getattr(message.author, "bot", False):
            continue
        content = str(getattr(message, "content", "")).strip()
        if not content:
            continue
        rows.append(f"[{message.created_at.isoformat()}] {message.author.display_name}: {content}")
    return str(getattr(channel, "name", channel_id)), "\n".join(rows)


def _read_file_text(path: Path) -> str:
    """Read text from markdown/txt/pdf files."""

    if not path.exists():
        LOGGER.warning("Knowledge file not found: %s", path)
        return ""
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    LOGGER.warning("Unsupported knowledge file type: %s", path)
    return ""


def _extract_pdf_text(path: Path) -> str:
    """Best-effort PDF text extraction."""

    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        LOGGER.warning("PDF ingestion requires pypdf. Skipping %s", path)
        return ""
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except Exception:
        LOGGER.exception("Failed to extract PDF text: %s", path)
        return ""


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for item in values:
        value = int(item)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


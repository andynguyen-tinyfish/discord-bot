"""
Scheduled job coordination.

This module orchestrates the nightly summary job and the morning reminder job
using the configured runtime timezone from SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from app.collector import fetch_messages_from_channels
from app.config import Config
from app.filters import filter_messages
from app.formatter import format_daily_reminder
from app.knowledge import ingest_project_knowledge
from app.storage import (
    ProjectConfig,
    RuntimeSettings,
    get_runtime_settings,
    log_job_event,
    get_summary,
    mark_as_unposted,
    save_summary,
    try_claim_scheduler_run,
    try_mark_as_posted,
)
from app.summarizer import summarize_messages_with_meta


LOGGER = logging.getLogger(__name__)


class DailyScheduler:
    """A small polling scheduler for nightly and morning Discord jobs."""

    def __init__(self, client: discord.Client, config: Config, poll_interval_seconds: int = 30) -> None:
        """Initialize the scheduler with a Discord client and app config."""

        self.client = client
        self.config = config
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_nightly_run_key: str | None = None
        self._last_morning_run_key: str | None = None

    def start(self) -> None:
        """Start the background scheduler loop if it is not already running."""

        if self._task is not None and not self._task.done():
            return

        LOGGER.info("Starting scheduler loop using runtime timezone from SQLite")
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        """Poll time and trigger jobs when their configured slot is reached."""

        while not self.client.is_closed():
            try:
                settings = _validate_runtime_settings(get_runtime_settings(self.config.database_path))
            except Exception:
                LOGGER.exception("Invalid runtime settings; scheduler loop will retry")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            now = now_in_timezone(settings.timezone)

            if _matches_schedule(
                now,
                settings.nightly_summary_hour,
                settings.nightly_summary_minute,
            ):
                run_key = f"nightly:{now.strftime('%Y-%m-%d:%H:%M')}"
                if run_key != self._last_nightly_run_key:
                    self._last_nightly_run_key = run_key
                    if not try_claim_scheduler_run(run_key, self.config.database_path):
                        LOGGER.info("Nightly run slot already claimed by another process: %s", run_key)
                        await asyncio.sleep(self.poll_interval_seconds)
                        continue
                    try:
                        await run_nightly_job(self.client, self.config, now=now)
                    except Exception:
                        LOGGER.exception("Nightly job failed")

            if _matches_schedule(
                now,
                settings.morning_post_hour,
                settings.morning_post_minute,
            ):
                run_key = f"morning:{now.strftime('%Y-%m-%d:%H:%M')}"
                if run_key != self._last_morning_run_key:
                    self._last_morning_run_key = run_key
                    try:
                        await run_morning_job(self.client, self.config, now=now)
                    except Exception:
                        LOGGER.exception("Morning job failed")

            await asyncio.sleep(self.poll_interval_seconds)


async def run_nightly_job(
    client: discord.Client,
    config: Config,
    now: datetime | None = None,
    target_date: date | None = None,
) -> dict[str, object]:
    """Collect, filter, summarize, and save yesterday's QA messages."""

    trigger_source = "scheduler" if now is not None else "manual"
    settings = _validate_runtime_settings(get_runtime_settings(config.database_path))
    projects = _resolve_projects(settings)
    if not projects:
        raise ValueError("No project configuration available for nightly job.")
    timezone = ZoneInfo(settings.timezone)
    resolved_date = _resolve_target_date(
        timezone_name=settings.timezone,
        now=now,
        target_date=target_date,
    )
    start_time, end_time = _get_day_window(resolved_date, timezone)
    date_key = resolved_date.isoformat()
    log_job_event(
        job_name="nightly",
        trigger_source=trigger_source,
        status="started",
        target_date=date_key,
        message=f"Nightly job started for {len(projects)} project(s).",
        database_path=config.database_path,
    )

    try:
        LOGGER.info("Nightly job started for %s (%s project(s))", date_key, len(projects))
        project_results: list[dict[str, object]] = []
        for project in projects:
            if not project.source_channel_ids:
                log_job_event(
                    job_name="nightly",
                    trigger_source=trigger_source,
                    status="skipped",
                    target_date=date_key,
                    message=f"[{project.key}] skipped: no source channels.",
                    database_path=config.database_path,
                )
                continue

            messages = await fetch_messages_from_channels(
                client=client,
                channel_ids=project.source_channel_ids,
                start_time=start_time,
                end_time=end_time,
            )
            LOGGER.info(
                "[%s] Collected %s messages from %s source channels for %s",
                project.key,
                len(messages),
                len(project.source_channel_ids),
                date_key,
            )

            filtered_messages = filter_messages(
                messages,
                allowed_user_ids=settings.allowed_user_ids,
                allowed_role_ids=settings.allowed_role_ids,
            )
            LOGGER.info("[%s] Kept %s messages after filtering", project.key, len(filtered_messages))
            log_job_event(
                job_name="nightly",
                trigger_source=trigger_source,
                status="progress",
                target_date=date_key,
                message=(
                    f"[{project.key}] collected={len(messages)}, kept={len(filtered_messages)} "
                    "after filtering."
                ),
                database_path=config.database_path,
            )

            summary, summarize_meta = await asyncio.to_thread(
                summarize_messages_with_meta,
                filtered_messages,
                config.gemini_api_key,
                "gemini-2.5-flash",
                project.name,
            )
            summary["_meta"] = {
                "project_key": project.key,
                "project_name": project.name,
                "used_fallback": bool(summarize_meta.get("used_fallback")),
                "fallback_reason": str(summarize_meta.get("fallback_reason", "")),
                "llm_success": bool(summarize_meta.get("llm_success")),
                "llm_attempted": bool(summarize_meta.get("llm_attempted")),
                "model": str(summarize_meta.get("model", "")),
                "input_messages": int(summarize_meta.get("input_messages", 0)),
                "collected_messages": len(messages),
                "filtered_messages": len(filtered_messages),
            }
            summary["_meta"]["item_mentions"] = _build_item_mentions(summary, filtered_messages)
            if not filtered_messages:
                LOGGER.info("[%s] No filtered messages found for %s", project.key, date_key)

            save_summary(
                date_key,
                summary,
                config.database_path,
                project_key=project.key,
                project_name=project.name,
            )
            LOGGER.info("Saved summary for %s [%s]", date_key, project.key)
            llm_status = "ok" if summarize_meta.get("llm_success") else "fallback"
            fallback_reason = summarize_meta.get("fallback_reason", "")
            status = "success" if summarize_meta.get("llm_success") else "degraded"
            reason_suffix = f", reason={fallback_reason}" if fallback_reason else ""
            log_job_event(
                job_name="nightly",
                trigger_source=trigger_source,
                status=status,
                target_date=date_key,
                message=(
                    f"[{project.key}] Saved summary. collected={len(messages)}, "
                    f"kept={len(filtered_messages)}, gemini={llm_status}{reason_suffix}, "
                    f"model={summarize_meta.get('model')}"
                ),
                database_path=config.database_path,
            )
            project_results.append(
                {
                    "project_key": project.key,
                    "project_name": project.name,
                    "collected": len(messages),
                    "filtered": len(filtered_messages),
                }
            )
        return {"date": date_key, "projects": project_results}
    except Exception as exc:
        log_job_event(
            job_name="nightly",
            trigger_source=trigger_source,
            status="failed",
            target_date=date_key,
            message=str(exc),
            database_path=config.database_path,
        )
        raise


async def run_morning_job(
    client: discord.Client,
    config: Config,
    now: datetime | None = None,
    target_date: date | None = None,
) -> bool:
    """Load yesterday's summary, post it to Discord, and mark it as posted."""

    trigger_source = "scheduler" if now is not None else "manual"
    settings = _validate_runtime_settings(get_runtime_settings(config.database_path))
    projects = _resolve_projects(settings)
    if not projects:
        raise ValueError("No project configuration available for morning job.")
    resolved_date = _resolve_target_date(
        timezone_name=settings.timezone,
        now=now,
        target_date=target_date,
    )
    date_key = resolved_date.isoformat()
    log_job_event(
        job_name="morning",
        trigger_source=trigger_source,
        status="started",
        target_date=date_key,
        message=f"Morning job started for {len(projects)} project(s).",
        database_path=config.database_path,
    )

    try:
        LOGGER.info("Morning job started for %s (%s project(s))", date_key, len(projects))
        success_count = 0

        for project in projects:
            summary = get_summary(date_key, config.database_path, project_key=project.key)
            if summary is None:
                LOGGER.warning("[%s] No summary found for %s; skipping", project.key, date_key)
                log_job_event(
                    job_name="morning",
                    trigger_source=trigger_source,
                    status="skipped",
                    target_date=date_key,
                    message=f"[{project.key}] No summary found for target date.",
                    database_path=config.database_path,
                )
                continue

            if _is_empty_summary(summary):
                LOGGER.info("[%s] Posting empty-summary reminder for %s", project.key, date_key)

            message = format_daily_reminder(
                summary,
                date_key,
                project_name=project.name,
                designated_role_id=_resolve_designated_role_id(project, settings),
            )

            if settings.dry_run:
                await _run_dry_run_post(
                    client=client,
                    settings=settings,
                    message=message,
                    date_key=date_key,
                    project=project,
                )
                log_job_event(
                    job_name="morning",
                    trigger_source=trigger_source,
                    status="success",
                    target_date=date_key,
                    message=f"[{project.key}] Dry-run preview sent (or printed).",
                    database_path=config.database_path,
                )
                success_count += 1
                continue

            post_channel_id = _resolve_post_channel_id(project, settings)
            if post_channel_id is None or post_channel_id <= 0:
                log_job_event(
                    job_name="morning",
                    trigger_source=trigger_source,
                    status="failed",
                    target_date=date_key,
                    message=f"[{project.key}] Missing post channel configuration.",
                    database_path=config.database_path,
                )
                continue

            if not try_mark_as_posted(date_key, config.database_path, project_key=project.key):
                LOGGER.info("[%s] Summary already posted for %s; skipping", project.key, date_key)
                log_job_event(
                    job_name="morning",
                    trigger_source=trigger_source,
                    status="skipped",
                    target_date=date_key,
                    message=f"[{project.key}] Summary already posted.",
                    database_path=config.database_path,
                )
                continue

            try:
                channel = await _get_messageable_channel(client, post_channel_id)
                await channel.send(message)
            except Exception:
                mark_as_unposted(date_key, config.database_path, project_key=project.key)
                raise

            LOGGER.info("[%s] Posted reminder and marked %s as posted", project.key, date_key)
            log_job_event(
                job_name="morning",
                trigger_source=trigger_source,
                status="success",
                target_date=date_key,
                message=f"[{project.key}] Reminder posted to Discord channel {post_channel_id}.",
                database_path=config.database_path,
            )
            success_count += 1

        return success_count > 0
    except Exception as exc:
        log_job_event(
            job_name="morning",
            trigger_source=trigger_source,
            status="failed",
            target_date=date_key,
            message=str(exc),
            database_path=config.database_path,
        )
        raise


async def run_knowledge_ingestion_job(
    client: discord.Client,
    config: Config,
    project_key: str | None = None,
) -> dict[str, int]:
    """Ingest configured knowledge sources into chunk storage."""

    settings = _validate_runtime_settings(get_runtime_settings(config.database_path))
    projects = _resolve_projects(settings)
    if project_key:
        projects = [project for project in projects if project.key == project_key]
    if not projects:
        raise ValueError("No project found for knowledge ingestion.")

    log_job_event(
        job_name="knowledge",
        trigger_source="manual",
        status="started",
        target_date=None,
        message=f"Knowledge ingestion started for {len(projects)} project(s).",
        database_path=config.database_path,
    )
    total_sources = 0
    total_chunks = 0
    for project in projects:
        try:
            result = await ingest_project_knowledge(
                client=client,
                settings=settings,
                project=project,
                database_path=config.database_path,
            )
            total_sources += int(result.get("sources", 0))
            total_chunks += int(result.get("chunks", 0))
            log_job_event(
                job_name="knowledge",
                trigger_source="manual",
                status="success",
                target_date=None,
                message=(
                    f"[{project.key}] Knowledge refreshed: sources={result.get('sources', 0)}, "
                    f"chunks={result.get('chunks', 0)}"
                ),
                database_path=config.database_path,
            )
        except Exception as exc:
            log_job_event(
                job_name="knowledge",
                trigger_source="manual",
                status="failed",
                target_date=None,
                message=f"[{project.key}] Knowledge ingestion failed: {exc}",
                database_path=config.database_path,
            )
            raise
    return {"projects": len(projects), "sources": total_sources, "chunks": total_chunks}


def now_in_timezone(timezone_name: str) -> datetime:
    """Return the current time in the configured timezone."""

    return datetime.now(ZoneInfo(timezone_name))


def _get_day_window(target_date: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Return the full day window for a local date in the configured zone."""

    start_time = datetime.combine(target_date, time.min, tzinfo=zone)
    end_time = start_time + timedelta(days=1)
    return start_time, end_time


def _matches_schedule(current_time: datetime, hour: int, minute: int) -> bool:
    """Return `True` when current local time matches a job slot."""

    return current_time.hour == hour and current_time.minute == minute


def _resolve_target_date(timezone_name: str, now: datetime | None, target_date: date | None) -> date:
    """Resolve the effective local date for a job run in the configured timezone."""

    if target_date is not None:
        return target_date
    current_time = now.astimezone(ZoneInfo(timezone_name)) if now else now_in_timezone(timezone_name)
    return (current_time - timedelta(days=1)).date()


def _is_empty_summary(summary: dict[str, object]) -> bool:
    """Return `True` when the saved summary contains no list items."""

    return not summary.get("highlights") and not summary.get("blockers") and not summary.get(
        "follow_ups"
    )


async def _run_dry_run_post(
    client: discord.Client,
    settings: RuntimeSettings,
    message: str,
    date_key: str,
    project: ProjectConfig,
) -> bool:
    """Execute dry-run posting behavior without touching real reminder channel."""

    if settings.dry_run_channel_id is not None:
        channel = await _get_messageable_channel(client, settings.dry_run_channel_id)
        await channel.send(message)
        LOGGER.info(
            "Dry-run mode: sent preview for %s project=%s to test channel %s",
            date_key,
            project.key,
            settings.dry_run_channel_id,
        )
        return True

    LOGGER.info(
        "Dry-run mode: printing preview for %s project=%s (no post performed)",
        date_key,
        project.key,
    )
    print("\n=== DRY RUN REMINDER PREVIEW ===")
    print(f"PROJECT: {project.name} ({project.key})")
    print(message)
    print("=== END PREVIEW ===\n")
    return True


async def _get_messageable_channel(
    client: discord.Client,
    channel_id: int,
) -> object:
    """Resolve a Discord channel and ensure it supports sending messages."""

    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)

    if not hasattr(channel, "send"):
        raise TypeError(f"Channel {channel_id} does not support sending messages.")

    return channel


def _validate_runtime_settings(settings: RuntimeSettings) -> RuntimeSettings:
    """Validate runtime settings before executing jobs."""

    _validate_time_part("nightly_summary_hour", settings.nightly_summary_hour, 0, 23)
    _validate_time_part("nightly_summary_minute", settings.nightly_summary_minute, 0, 59)
    _validate_time_part("morning_post_hour", settings.morning_post_hour, 0, 23)
    _validate_time_part("morning_post_minute", settings.morning_post_minute, 0, 59)
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone in runtime settings: {settings.timezone!r}") from exc
    return settings


def _resolve_projects(settings: RuntimeSettings) -> list[ProjectConfig]:
    """Resolve project routing from runtime settings, preserving legacy defaults."""

    if settings.project_configs:
        return settings.project_configs
    return [
        ProjectConfig(
            key="default",
            name="Default",
            source_channel_ids=settings.source_channel_ids,
            post_channel_id=settings.reminder_channel_id if settings.reminder_channel_id > 0 else None,
            fallback_post_channel_id=settings.shared_post_channel_id,
            mention_role_id=settings.designated_role_id,
            knowledge_channel_ids=[],
            knowledge_file_paths=[],
        )
    ]


def _resolve_post_channel_id(project: ProjectConfig, settings: RuntimeSettings) -> int | None:
    """Resolve effective post channel for a project, including shared fallbacks."""

    if project.post_channel_id is not None and project.post_channel_id > 0:
        return project.post_channel_id
    if project.fallback_post_channel_id is not None and project.fallback_post_channel_id > 0:
        return project.fallback_post_channel_id
    if settings.shared_post_channel_id is not None and settings.shared_post_channel_id > 0:
        return settings.shared_post_channel_id
    if settings.reminder_channel_id > 0:
        return settings.reminder_channel_id
    return None


def _resolve_designated_role_id(project: ProjectConfig, settings: RuntimeSettings) -> int | None:
    """Resolve role mention id shown before reminder message."""

    if project.mention_role_id is not None and project.mention_role_id > 0:
        return project.mention_role_id
    if settings.designated_role_id is not None and settings.designated_role_id > 0:
        return settings.designated_role_id
    return None


def _build_item_mentions(summary: dict[str, object], messages: list[dict[str, object]]) -> dict[str, list[str]]:
    """Best-effort owner mention mapping for blockers/follow-ups."""

    blockers = [str(item) for item in summary.get("blockers", []) if isinstance(item, str)]
    follow_ups = [str(item) for item in summary.get("follow_ups", []) if isinstance(item, str)]
    return {
        "blockers": [_match_owner_mention(item, messages) for item in blockers],
        "follow_ups": [_match_owner_mention(item, messages) for item in follow_ups],
    }


def _match_owner_mention(item: str, messages: list[dict[str, object]]) -> str:
    """Return <@user_id> mention for the most relevant message author, if confident."""

    item_tokens = _tokenize(item)
    if len(item_tokens) < 2:
        return ""
    best_score = 0
    best_author_id = ""
    best_role_mention = ""
    for message in messages:
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        author_id = str(message.get("author_id", "")).strip()
        if not author_id.isdigit():
            continue
        score = len(item_tokens & _tokenize(content))
        if score > best_score:
            best_score = score
            best_author_id = author_id
            role_match = re.search(r"<@&(\d+)>", content)
            best_role_mention = f"<@&{role_match.group(1)}>" if role_match else ""
    if best_score < 2 or not best_author_id:
        return ""
    if best_role_mention:
        return best_role_mention
    return f"<@{best_author_id}>"


def _tokenize(text: str) -> set[str]:
    tokens = {token for token in re.findall(r"[a-zA-Z0-9]{3,}", text.lower())}
    return tokens


def _validate_time_part(name: str, value: int, minimum: int, maximum: int) -> None:
    """Validate an hour/minute integer range."""

    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}. Received: {value}")

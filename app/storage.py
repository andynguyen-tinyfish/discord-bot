"""
SQLite storage layer for daily summaries.

This module uses the Python standard library `sqlite3` module directly. It
creates required tables on startup and exposes helper functions for saving
summaries and managing runtime settings.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import load_config


@dataclass(frozen=True)
class ProjectConfig:
    """Per-project channel routing configuration stored in runtime settings."""

    key: str
    name: str
    source_channel_ids: list[int]
    post_channel_id: int | None
    fallback_post_channel_id: int | None
    mention_role_id: int | None
    knowledge_channel_ids: list[int]
    knowledge_file_paths: list[str]


@dataclass(frozen=True)
class RuntimeSettings:
    """Operational settings that can be edited at runtime from SQLite."""

    source_channel_ids: list[int]
    reminder_channel_id: int
    timezone: str
    nightly_summary_hour: int
    nightly_summary_minute: int
    morning_post_hour: int
    morning_post_minute: int
    dry_run: bool
    dry_run_channel_id: int | None
    allowed_user_ids: list[int]
    allowed_role_ids: list[int]
    designated_role_id: int | None
    shared_post_channel_id: int | None
    shared_knowledge_channel_ids: list[int]
    shared_knowledge_file_paths: list[str]
    project_configs: list[ProjectConfig]


@dataclass(frozen=True)
class UploadedKnowledgeFile:
    """Metadata for dashboard-uploaded knowledge files."""

    id: int
    project_key: str
    original_filename: str
    stored_path: str
    file_type: str
    file_size_bytes: int
    uploaded_by: str | None
    uploaded_at: str
    is_active: bool
    ingest_status: str
    last_ingested_at: str | None
    last_ingest_error: str | None


def init_db(database_path: str | None = None, seed_settings: RuntimeSettings | None = None) -> None:
    """Create the SQLite database and required tables if they do not exist."""

    db_path = _resolve_database_path(database_path)
    _ensure_parent_directory(db_path)

    with sqlite3.connect(db_path) as connection:
        _ensure_summaries_table_v2(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                project_key TEXT NOT NULL DEFAULT 'default',
                project_name TEXT NOT NULL DEFAULT 'Default',
                highlights TEXT NOT NULL,
                blockers TEXT NOT NULL,
                follow_ups TEXT NOT NULL,
                final_message TEXT,
                summary_meta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                posted INTEGER NOT NULL DEFAULT 0,
                UNIQUE(date, project_key)
            )
            """
        )
        _ensure_column(
            connection,
            table_name="summaries",
            column_name="summary_meta",
            definition="TEXT NOT NULL DEFAULT '{}'",
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                source_channel_ids TEXT NOT NULL DEFAULT '[]',
                reminder_channel_id INTEGER NOT NULL DEFAULT 0,
                timezone TEXT NOT NULL DEFAULT 'Asia/Bangkok',
                nightly_summary_hour INTEGER NOT NULL DEFAULT 0,
                nightly_summary_minute INTEGER NOT NULL DEFAULT 5,
                morning_post_hour INTEGER NOT NULL DEFAULT 9,
                morning_post_minute INTEGER NOT NULL DEFAULT 0,
                dry_run INTEGER NOT NULL DEFAULT 0,
                dry_run_channel_id INTEGER,
                allowed_user_ids TEXT NOT NULL DEFAULT '[]',
                allowed_role_ids TEXT NOT NULL DEFAULT '[]',
                designated_role_id INTEGER,
                shared_knowledge_channel_ids TEXT NOT NULL DEFAULT '[]',
                shared_knowledge_file_paths TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(
            connection,
            table_name="runtime_settings",
            column_name="designated_role_id",
            definition="INTEGER",
        )
        _ensure_column(
            connection,
            table_name="runtime_settings",
            column_name="shared_post_channel_id",
            definition="INTEGER",
        )
        _ensure_column(
            connection,
            table_name="runtime_settings",
            column_name="project_configs",
            definition="TEXT NOT NULL DEFAULT '[]'",
        )
        _ensure_column(
            connection,
            table_name="runtime_settings",
            column_name="shared_knowledge_channel_ids",
            definition="TEXT NOT NULL DEFAULT '[]'",
        )
        _ensure_column(
            connection,
            table_name="runtime_settings",
            column_name="shared_knowledge_file_paths",
            definition="TEXT NOT NULL DEFAULT '[]'",
        )
        runtime_settings_insert = connection.execute(
            """
            INSERT OR IGNORE INTO runtime_settings (
                id,
                source_channel_ids,
                reminder_channel_id,
                timezone,
                nightly_summary_hour,
                nightly_summary_minute,
                morning_post_hour,
                morning_post_minute,
                dry_run,
                dry_run_channel_id,
                allowed_user_ids,
                allowed_role_ids,
                designated_role_id,
                shared_knowledge_channel_ids,
                shared_knowledge_file_paths,
                shared_post_channel_id,
                project_configs,
                updated_at
            )
            VALUES (1, '[]', 0, 'Asia/Bangkok', 0, 5, 9, 0, 0, NULL, '[]', '[]', NULL, '[]', '[]', NULL, '[]', ?)
            """,
            (_utc_now_iso(),),
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_run_claims (
                run_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                job_name TEXT NOT NULL,
                target_date TEXT,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT NOT NULL,
                project_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_key, source_type, source_ref, chunk_index)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_project ON knowledge_chunks(project_key)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_type, source_ref)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_knowledge_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_type TEXT NOT NULL DEFAULT '',
                file_size_bytes INTEGER NOT NULL DEFAULT 0,
                uploaded_by TEXT,
                uploaded_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                ingest_status TEXT NOT NULL DEFAULT 'pending',
                last_ingested_at TEXT,
                last_ingest_error TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_uploaded_knowledge_project ON uploaded_knowledge_files(project_key, is_active)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_uploaded_knowledge_path_active ON uploaded_knowledge_files(project_key, stored_path, is_active)"
        )
        connection.commit()
        runtime_settings_created = runtime_settings_insert.rowcount == 1

    if seed_settings is not None and runtime_settings_created:
        save_runtime_settings(seed_settings, db_path)


def get_runtime_settings(database_path: str | None = None) -> RuntimeSettings:
    """Read runtime settings from SQLite."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                source_channel_ids,
                reminder_channel_id,
                timezone,
                nightly_summary_hour,
                nightly_summary_minute,
                morning_post_hour,
                morning_post_minute,
                dry_run,
                dry_run_channel_id,
                allowed_user_ids,
                allowed_role_ids,
                designated_role_id,
                shared_knowledge_channel_ids,
                shared_knowledge_file_paths,
                shared_post_channel_id,
                project_configs
            FROM runtime_settings
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        raise ValueError("Runtime settings are not initialized.")

    return RuntimeSettings(
        source_channel_ids=_deserialize_int_list(row["source_channel_ids"]),
        reminder_channel_id=int(row["reminder_channel_id"]),
        timezone=str(row["timezone"]),
        nightly_summary_hour=int(row["nightly_summary_hour"]),
        nightly_summary_minute=int(row["nightly_summary_minute"]),
        morning_post_hour=int(row["morning_post_hour"]),
        morning_post_minute=int(row["morning_post_minute"]),
        dry_run=bool(row["dry_run"]),
        dry_run_channel_id=_to_optional_int(row["dry_run_channel_id"]),
        allowed_user_ids=_deserialize_int_list(row["allowed_user_ids"]),
        allowed_role_ids=_deserialize_int_list(row["allowed_role_ids"]),
        designated_role_id=_to_optional_int(row["designated_role_id"]),
        shared_knowledge_channel_ids=_deserialize_int_list(row["shared_knowledge_channel_ids"]),
        shared_knowledge_file_paths=_deserialize_string_list(str(row["shared_knowledge_file_paths"])),
        shared_post_channel_id=_to_optional_int(row["shared_post_channel_id"]),
        project_configs=_deserialize_project_configs(str(row["project_configs"])),
    )


def save_runtime_settings(settings: RuntimeSettings, database_path: str | None = None) -> None:
    """Persist runtime settings to SQLite."""

    db_path = _resolve_database_path(database_path)
    _ensure_parent_directory(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runtime_settings (
                id,
                source_channel_ids,
                reminder_channel_id,
                timezone,
                nightly_summary_hour,
                nightly_summary_minute,
                morning_post_hour,
                morning_post_minute,
                dry_run,
                dry_run_channel_id,
                allowed_user_ids,
                allowed_role_ids,
                designated_role_id,
                shared_knowledge_channel_ids,
                shared_knowledge_file_paths,
                shared_post_channel_id,
                project_configs,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_channel_ids = excluded.source_channel_ids,
                reminder_channel_id = excluded.reminder_channel_id,
                timezone = excluded.timezone,
                nightly_summary_hour = excluded.nightly_summary_hour,
                nightly_summary_minute = excluded.nightly_summary_minute,
                morning_post_hour = excluded.morning_post_hour,
                morning_post_minute = excluded.morning_post_minute,
                dry_run = excluded.dry_run,
                dry_run_channel_id = excluded.dry_run_channel_id,
                allowed_user_ids = excluded.allowed_user_ids,
                allowed_role_ids = excluded.allowed_role_ids,
                designated_role_id = excluded.designated_role_id,
                shared_knowledge_channel_ids = excluded.shared_knowledge_channel_ids,
                shared_knowledge_file_paths = excluded.shared_knowledge_file_paths,
                shared_post_channel_id = excluded.shared_post_channel_id,
                project_configs = excluded.project_configs,
                updated_at = excluded.updated_at
            """,
            (
                1,
                _serialize_int_list(settings.source_channel_ids),
                int(settings.reminder_channel_id),
                settings.timezone,
                int(settings.nightly_summary_hour),
                int(settings.nightly_summary_minute),
                int(settings.morning_post_hour),
                int(settings.morning_post_minute),
                1 if settings.dry_run else 0,
                settings.dry_run_channel_id,
                _serialize_int_list(settings.allowed_user_ids),
                _serialize_int_list(settings.allowed_role_ids),
                settings.designated_role_id,
                _serialize_int_list(settings.shared_knowledge_channel_ids),
                _serialize_string_list(settings.shared_knowledge_file_paths),
                settings.shared_post_channel_id,
                _serialize_project_configs(settings.project_configs),
                _utc_now_iso(),
            ),
        )
        connection.commit()


def save_summary(
    date: str,
    data: dict[str, Any],
    database_path: str | None = None,
    project_key: str = "default",
    project_name: str = "Default",
) -> None:
    """Insert or update a summary for a specific date."""

    db_path = _resolve_database_path(database_path)
    _ensure_parent_directory(db_path)

    highlights = _serialize_field(data.get("highlights", []))
    blockers = _serialize_field(data.get("blockers", []))
    follow_ups = _serialize_field(data.get("follow_ups", []))
    final_message = data.get("final_message")
    summary_meta = _serialize_field(data.get("_meta", {}))
    created_at = _utc_now_iso()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO summaries (
                date,
                project_key,
                project_name,
                highlights,
                blockers,
                follow_ups,
                final_message,
                summary_meta,
                created_at,
                posted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, project_key) DO UPDATE SET
                project_name = excluded.project_name,
                highlights = excluded.highlights,
                blockers = excluded.blockers,
                follow_ups = excluded.follow_ups,
                final_message = excluded.final_message,
                summary_meta = excluded.summary_meta
            """,
            (
                date,
                project_key,
                project_name,
                highlights,
                blockers,
                follow_ups,
                final_message,
                summary_meta,
                created_at,
                0,
            ),
        )
        connection.commit()


def get_summary(
    date: str,
    database_path: str | None = None,
    project_key: str = "default",
) -> dict[str, Any] | None:
    """Return the stored summary for a specific date, or `None` if not found."""

    db_path = _resolve_database_path(database_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                id,
                date,
                project_key,
                project_name,
                highlights,
                blockers,
                follow_ups,
                final_message,
                summary_meta,
                created_at,
                posted
            FROM summaries
            WHERE date = ? AND project_key = ?
            """,
            (date, project_key),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "date": row["date"],
        "project_key": row["project_key"],
        "project_name": row["project_name"],
        "highlights": _deserialize_field(row["highlights"]),
        "blockers": _deserialize_field(row["blockers"]),
        "follow_ups": _deserialize_field(row["follow_ups"]),
        "final_message": row["final_message"],
        "meta": _deserialize_field(row["summary_meta"] or "{}"),
        "created_at": row["created_at"],
        "posted": bool(row["posted"]),
    }


def get_recent_summaries(limit: int = 20, database_path: str | None = None) -> list[dict[str, Any]]:
    """Return recent summaries ordered by date descending."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                id,
                date,
                project_key,
                project_name,
                highlights,
                blockers,
                follow_ups,
                final_message,
                summary_meta,
                created_at,
                posted
            FROM summaries
            ORDER BY date DESC, project_name ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "date": row["date"],
            "project_key": row["project_key"],
            "project_name": row["project_name"],
            "highlights": _deserialize_field(row["highlights"]),
            "blockers": _deserialize_field(row["blockers"]),
            "follow_ups": _deserialize_field(row["follow_ups"]),
            "final_message": row["final_message"],
            "meta": _deserialize_field(row["summary_meta"] or "{}"),
            "created_at": row["created_at"],
            "posted": bool(row["posted"]),
        }
        for row in rows
    ]


def try_claim_scheduler_run(run_key: str, database_path: str | None = None) -> bool:
    """Claim a scheduler run key once across processes."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO scheduler_run_claims (run_key, created_at)
            VALUES (?, ?)
            """,
            (run_key, _utc_now_iso()),
        )
        connection.commit()
    return cursor.rowcount == 1


def log_job_event(
    job_name: str,
    trigger_source: str,
    status: str,
    message: str | None = None,
    target_date: str | None = None,
    database_path: str | None = None,
) -> None:
    """Append a job event log row for dashboard visibility."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO job_logs (
                created_at,
                job_name,
                target_date,
                trigger_source,
                status,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_utc_now_iso(), job_name, target_date, trigger_source, status, message),
        )
        connection.commit()


def get_recent_job_logs(limit: int = 200, database_path: str | None = None) -> list[dict[str, Any]]:
    """Return recent job logs in reverse chronological order."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                id,
                created_at,
                job_name,
                target_date,
                trigger_source,
                status,
                message
            FROM job_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "job_name": row["job_name"],
            "target_date": row["target_date"],
            "trigger_source": row["trigger_source"],
            "status": row["status"],
            "message": row["message"],
        }
        for row in rows
    ]


def add_uploaded_knowledge_file(
    project_key: str,
    original_filename: str,
    stored_path: str,
    file_type: str,
    file_size_bytes: int,
    uploaded_by: str | None = None,
    database_path: str | None = None,
) -> int:
    """Insert or reactivate one uploaded project knowledge file metadata row."""

    db_path = _resolve_database_path(database_path)
    now = _utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        existing = connection.execute(
            """
            SELECT id
            FROM uploaded_knowledge_files
            WHERE project_key = ? AND stored_path = ? AND is_active = 1
            """,
            (project_key, stored_path),
        ).fetchone()
        if existing is not None:
            file_id = int(existing[0])
            connection.execute(
                """
                UPDATE uploaded_knowledge_files
                SET original_filename = ?,
                    file_type = ?,
                    file_size_bytes = ?,
                    uploaded_by = ?,
                    uploaded_at = ?,
                    ingest_status = 'pending',
                    last_ingest_error = NULL
                WHERE id = ?
                """,
                (
                    original_filename,
                    file_type,
                    int(file_size_bytes),
                    uploaded_by,
                    now,
                    file_id,
                ),
            )
            connection.commit()
            return file_id

        cursor = connection.execute(
            """
            INSERT INTO uploaded_knowledge_files (
                project_key,
                original_filename,
                stored_path,
                file_type,
                file_size_bytes,
                uploaded_by,
                uploaded_at,
                is_active,
                ingest_status,
                last_ingested_at,
                last_ingest_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'pending', NULL, NULL)
            """,
            (
                project_key,
                original_filename,
                stored_path,
                file_type,
                int(file_size_bytes),
                uploaded_by,
                now,
            ),
        )
        connection.commit()
    return int(cursor.lastrowid)


def list_uploaded_knowledge_files(
    project_key: str | None = None,
    only_active: bool = True,
    database_path: str | None = None,
) -> list[UploadedKnowledgeFile]:
    """List dashboard-uploaded knowledge file metadata rows."""

    db_path = _resolve_database_path(database_path)
    where_clauses: list[str] = []
    params: list[Any] = []
    if project_key:
        where_clauses.append("project_key = ?")
        params.append(project_key)
    if only_active:
        where_clauses.append("is_active = 1")
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT
                id,
                project_key,
                original_filename,
                stored_path,
                file_type,
                file_size_bytes,
                uploaded_by,
                uploaded_at,
                is_active,
                ingest_status,
                last_ingested_at,
                last_ingest_error
            FROM uploaded_knowledge_files
            {where_sql}
            ORDER BY uploaded_at DESC, id DESC
            """,
            tuple(params),
        ).fetchall()

    return [
        UploadedKnowledgeFile(
            id=int(row["id"]),
            project_key=str(row["project_key"]),
            original_filename=str(row["original_filename"]),
            stored_path=str(row["stored_path"]),
            file_type=str(row["file_type"] or ""),
            file_size_bytes=int(row["file_size_bytes"] or 0),
            uploaded_by=str(row["uploaded_by"]) if row["uploaded_by"] else None,
            uploaded_at=str(row["uploaded_at"]),
            is_active=bool(row["is_active"]),
            ingest_status=str(row["ingest_status"] or "pending"),
            last_ingested_at=str(row["last_ingested_at"]) if row["last_ingested_at"] else None,
            last_ingest_error=str(row["last_ingest_error"]) if row["last_ingest_error"] else None,
        )
        for row in rows
    ]


def get_uploaded_knowledge_file(file_id: int, database_path: str | None = None) -> UploadedKnowledgeFile | None:
    """Get one uploaded file metadata row by id."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                id,
                project_key,
                original_filename,
                stored_path,
                file_type,
                file_size_bytes,
                uploaded_by,
                uploaded_at,
                is_active,
                ingest_status,
                last_ingested_at,
                last_ingest_error
            FROM uploaded_knowledge_files
            WHERE id = ?
            """,
            (int(file_id),),
        ).fetchone()
    if row is None:
        return None
    return UploadedKnowledgeFile(
        id=int(row["id"]),
        project_key=str(row["project_key"]),
        original_filename=str(row["original_filename"]),
        stored_path=str(row["stored_path"]),
        file_type=str(row["file_type"] or ""),
        file_size_bytes=int(row["file_size_bytes"] or 0),
        uploaded_by=str(row["uploaded_by"]) if row["uploaded_by"] else None,
        uploaded_at=str(row["uploaded_at"]),
        is_active=bool(row["is_active"]),
        ingest_status=str(row["ingest_status"] or "pending"),
        last_ingested_at=str(row["last_ingested_at"]) if row["last_ingested_at"] else None,
        last_ingest_error=str(row["last_ingest_error"]) if row["last_ingest_error"] else None,
    )


def get_project_uploaded_knowledge_paths(
    project_key: str,
    database_path: str | None = None,
) -> list[str]:
    """Return active uploaded file paths for one project."""

    rows = list_uploaded_knowledge_files(
        project_key=project_key,
        only_active=True,
        database_path=database_path,
    )
    return [row.stored_path for row in rows if row.stored_path.strip()]


def set_uploaded_knowledge_file_active(
    file_id: int,
    is_active: bool,
    database_path: str | None = None,
) -> bool:
    """Activate/deactivate one uploaded file metadata row."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE uploaded_knowledge_files
            SET is_active = ?
            WHERE id = ?
            """,
            (1 if is_active else 0, int(file_id)),
        )
        connection.commit()
    return cursor.rowcount == 1


def mark_uploaded_knowledge_file_ingest_result(
    file_ids: list[int],
    status: str,
    error_message: str | None,
    database_path: str | None = None,
) -> None:
    """Store ingestion status for one or many uploaded files."""

    deduped_ids = sorted({int(file_id) for file_id in file_ids if int(file_id) > 0})
    if not deduped_ids:
        return
    db_path = _resolve_database_path(database_path)
    now = _utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            UPDATE uploaded_knowledge_files
            SET ingest_status = ?,
                last_ingested_at = ?,
                last_ingest_error = ?
            WHERE id = ?
            """,
            [(status, now, error_message, file_id) for file_id in deduped_ids],
        )
        connection.commit()


def replace_knowledge_source_chunks(
    project_key: str,
    project_name: str,
    source_type: str,
    source_name: str,
    source_ref: str,
    chunks: list[str],
    tags: str = "",
    database_path: str | None = None,
) -> int:
    """Replace stored chunks for one source and return inserted row count."""

    db_path = _resolve_database_path(database_path)
    now = _utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            DELETE FROM knowledge_chunks
            WHERE project_key = ? AND source_type = ? AND source_ref = ?
            """,
            (project_key, source_type, source_ref),
        )
        inserted = 0
        for index, chunk in enumerate(chunks):
            cleaned = chunk.strip()
            if not cleaned:
                continue
            connection.execute(
                """
                INSERT INTO knowledge_chunks (
                    project_key,
                    project_name,
                    source_type,
                    source_name,
                    source_ref,
                    chunk_index,
                    chunk_text,
                    tags,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_key,
                    project_name,
                    source_type,
                    source_name,
                    source_ref,
                    index,
                    cleaned,
                    tags,
                    now,
                    now,
                ),
            )
            inserted += 1
        connection.commit()
    return inserted


def delete_knowledge_source_chunks(
    project_key: str,
    source_type: str,
    source_ref: str,
    database_path: str | None = None,
) -> int:
    """Delete stored chunks for one knowledge source."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            DELETE FROM knowledge_chunks
            WHERE project_key = ? AND source_type = ? AND source_ref = ?
            """,
            (project_key, source_type, source_ref),
        )
        connection.commit()
    return int(cursor.rowcount)


def search_knowledge_chunks(
    project_keys: list[str],
    query: str,
    limit: int = 12,
    database_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return simple relevance-ranked chunks by project and query tokens."""

    db_path = _resolve_database_path(database_path)
    keys = [item for item in project_keys if item.strip()]
    if not keys:
        return []
    like_terms = [token.strip() for token in query.lower().split() if len(token.strip()) >= 3][:8]
    conditions = " OR ".join(["LOWER(chunk_text) LIKE ?"] * len(like_terms))
    params: list[Any] = keys[:]
    sql = """
        SELECT
            project_key,
            project_name,
            source_type,
            source_name,
            source_ref,
            chunk_index,
            chunk_text
        FROM knowledge_chunks
        WHERE project_key IN ({placeholders})
    """.format(placeholders=",".join(["?"] * len(keys)))
    if like_terms:
        sql += f" AND ({conditions})"
        params.extend([f"%{term}%" for term in like_terms])
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(int(limit * 5))

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(sql, tuple(params)).fetchall()

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        text = str(row["chunk_text"])
        score = 0
        for term in like_terms:
            if term in text.lower():
                score += 1
        scored.append(
            (
                score,
                {
                    "project_key": row["project_key"],
                    "project_name": row["project_name"],
                    "source_type": row["source_type"],
                    "source_name": row["source_name"],
                    "source_ref": row["source_ref"],
                    "chunk_index": int(row["chunk_index"]),
                    "chunk_text": text,
                },
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def delete_job_logs(
    date_from: str | None = None,
    date_to: str | None = None,
    database_path: str | None = None,
) -> int:
    """Delete job logs, optionally constrained by inclusive date range."""

    db_path = _resolve_database_path(database_path)
    query = "DELETE FROM job_logs"
    params: list[Any] = []
    where_clauses: list[str] = []

    if date_from is not None:
        where_clauses.append("COALESCE(target_date, substr(created_at, 1, 10)) >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("COALESCE(target_date, substr(created_at, 1, 10)) <= ?")
        params.append(date_to)

    if where_clauses:
        query = f"{query} WHERE {' AND '.join(where_clauses)}"

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(query, tuple(params))
        connection.commit()
    return int(cursor.rowcount)


def delete_summaries(
    date_from: str | None = None,
    date_to: str | None = None,
    database_path: str | None = None,
) -> int:
    """Delete summaries, optionally constrained by inclusive date range."""

    db_path = _resolve_database_path(database_path)
    query = "DELETE FROM summaries"
    params: list[Any] = []
    where_clauses: list[str] = []

    if date_from is not None:
        where_clauses.append("date >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("date <= ?")
        params.append(date_to)

    if where_clauses:
        query = f"{query} WHERE {' AND '.join(where_clauses)}"

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(query, tuple(params))
        connection.commit()
    return int(cursor.rowcount)


def clear_operational_data(database_path: str | None = None) -> dict[str, int]:
    """Delete all summaries, job logs, and scheduler claims."""

    db_path = _resolve_database_path(database_path)
    with sqlite3.connect(db_path) as connection:
        deleted_summaries = int(connection.execute("DELETE FROM summaries").rowcount)
        deleted_job_logs = int(connection.execute("DELETE FROM job_logs").rowcount)
        deleted_claims = int(connection.execute("DELETE FROM scheduler_run_claims").rowcount)
        connection.commit()
    return {
        "summaries": deleted_summaries,
        "job_logs": deleted_job_logs,
        "scheduler_claims": deleted_claims,
    }


def mark_as_posted(date: str, database_path: str | None = None, project_key: str = "default") -> None:
    """Mark the summary for a specific date as posted."""

    db_path = _resolve_database_path(database_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE summaries SET posted = 1 WHERE date = ? AND project_key = ?",
            (date, project_key),
        )
        connection.commit()


def try_mark_as_posted(
    date: str,
    database_path: str | None = None,
    project_key: str = "default",
) -> bool:
    """Atomically mark as posted only if not posted yet."""

    db_path = _resolve_database_path(database_path)

    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "UPDATE summaries SET posted = 1 WHERE date = ? AND project_key = ? AND posted = 0",
            (date, project_key),
        )
        connection.commit()

    return cursor.rowcount == 1


def mark_as_unposted(date: str, database_path: str | None = None, project_key: str = "default") -> None:
    """Reset posted status for retry when post delivery fails."""

    db_path = _resolve_database_path(database_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE summaries SET posted = 0 WHERE date = ? AND project_key = ?",
            (date, project_key),
        )
        connection.commit()


def check_if_posted(date: str, database_path: str | None = None, project_key: str = "default") -> bool:
    """Return `True` if the summary for the given date has already been posted."""

    db_path = _resolve_database_path(database_path)

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT posted FROM summaries WHERE date = ? AND project_key = ?",
            (date, project_key),
        ).fetchone()

    if row is None:
        return False

    return bool(row[0])


def _resolve_database_path(database_path: str | None) -> str:
    """Resolve the database path from an argument or application config."""

    if database_path:
        return database_path
    return load_config().database_path


def _ensure_parent_directory(database_path: str) -> None:
    """Create the database parent directory if it does not exist."""

    Path(database_path).parent.mkdir(parents=True, exist_ok=True)


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Ensure a table has a specific column by applying a lightweight migration."""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_columns = {str(row[1]) for row in rows}
    if column_name in existing_columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _ensure_summaries_table_v2(connection: sqlite3.Connection) -> None:
    """Migrate summaries table to support per-project rows."""

    rows = connection.execute("PRAGMA table_info(summaries)").fetchall()
    if not rows:
        return
    columns = {str(row[1]) for row in rows}
    if "project_key" in columns and "project_name" in columns:
        return

    connection.execute("ALTER TABLE summaries RENAME TO summaries_legacy")
    connection.execute(
        """
        CREATE TABLE summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            project_key TEXT NOT NULL,
            project_name TEXT NOT NULL,
            highlights TEXT NOT NULL,
            blockers TEXT NOT NULL,
            follow_ups TEXT NOT NULL,
            final_message TEXT,
            summary_meta TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            posted INTEGER NOT NULL DEFAULT 0,
            UNIQUE(date, project_key)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO summaries (
            date,
            project_key,
            project_name,
            highlights,
            blockers,
            follow_ups,
            final_message,
            summary_meta,
            created_at,
            posted
        )
        SELECT
            date,
            'default',
            'Default',
            highlights,
            blockers,
            follow_ups,
            final_message,
            COALESCE(summary_meta, '{}'),
            created_at,
            posted
        FROM summaries_legacy
        """
    )
    connection.execute("DROP TABLE summaries_legacy")


def _serialize_field(value: Any) -> str:
    """Serialize structured summary content into JSON text."""

    return json.dumps(value, ensure_ascii=True)


def _deserialize_field(value: str) -> Any:
    """Deserialize JSON text back into Python data."""

    return json.loads(value)


def _serialize_int_list(values: list[int]) -> str:
    """Serialize list[int] into JSON for SQLite storage."""

    return json.dumps([int(value) for value in values], ensure_ascii=True)


def _deserialize_int_list(raw_value: str) -> list[int]:
    """Deserialize list[int] JSON safely."""

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid runtime settings list JSON in database.") from exc
    if not isinstance(parsed, list):
        raise ValueError("Runtime settings list value must be a JSON array.")
    return [int(value) for value in parsed]


def _serialize_string_list(values: list[str]) -> str:
    """Serialize list[str] into JSON for SQLite storage."""

    return json.dumps([str(value).strip() for value in values if str(value).strip()], ensure_ascii=True)


def _deserialize_string_list(raw_value: str) -> list[str]:
    """Deserialize list[str] JSON safely."""

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid runtime settings string list JSON in database.") from exc
    if not isinstance(parsed, list):
        raise ValueError("Runtime settings string list value must be a JSON array.")
    return [str(value).strip() for value in parsed if str(value).strip()]


def _serialize_project_configs(values: list[ProjectConfig]) -> str:
    """Serialize project config list to JSON."""

    payload = [
        {
            "key": value.key,
            "name": value.name,
            "source_channel_ids": [int(item) for item in value.source_channel_ids],
            "post_channel_id": value.post_channel_id,
            "fallback_post_channel_id": value.fallback_post_channel_id,
            "mention_role_id": value.mention_role_id,
            "knowledge_channel_ids": [int(item) for item in value.knowledge_channel_ids],
            "knowledge_file_paths": [str(item) for item in value.knowledge_file_paths],
        }
        for value in values
    ]
    return json.dumps(payload, ensure_ascii=True)


def _deserialize_project_configs(raw_value: str) -> list[ProjectConfig]:
    """Deserialize project config list from JSON."""

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid project_configs JSON in database.") from exc
    if not isinstance(parsed, list):
        raise ValueError("project_configs must be a JSON array.")

    items: list[ProjectConfig] = []
    for row in parsed:
        if not isinstance(row, dict):
            raise ValueError("Each project config must be an object.")
        source_channel_ids = [int(item) for item in row.get("source_channel_ids", [])]
        key = str(row.get("key", "")).strip()
        name = str(row.get("name", "")).strip()
        if not key or not name:
            raise ValueError("Project config key and name are required.")
        items.append(
            ProjectConfig(
                key=key,
                name=name,
                source_channel_ids=source_channel_ids,
                post_channel_id=_to_optional_int(row.get("post_channel_id")),
                fallback_post_channel_id=_to_optional_int(row.get("fallback_post_channel_id")),
                mention_role_id=_to_optional_int(row.get("mention_role_id")),
                knowledge_channel_ids=[int(item) for item in row.get("knowledge_channel_ids", [])],
                knowledge_file_paths=[str(item) for item in row.get("knowledge_file_paths", [])],
            )
        )
    return items


def _to_optional_int(value: Any) -> int | None:
    """Convert a nullable DB value to int | None."""

    if value is None:
        return None
    return int(value)


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()

"""
Application configuration loading.

This module reads environment variables from `.env` and the process
environment, validates required settings, and returns a single config object
for the rest of the application.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the Discord reminder bot."""

    discord_bot_token: str
    gemini_api_key: str
    database_path: str
    admin_password: str
    admin_session_secret: str
    seed_source_channel_ids: list[int]
    seed_reminder_channel_id: int
    seed_timezone: str
    seed_nightly_summary_hour: int
    seed_nightly_summary_minute: int
    seed_morning_post_hour: int
    seed_morning_post_minute: int
    seed_dry_run: bool
    seed_dry_run_channel_id: int | None
    seed_allowed_user_ids: list[int]
    seed_allowed_role_ids: list[int]


def load_config() -> Config:
    """Load, validate, and return application configuration."""

    load_dotenv("setting.env")
    load_dotenv()

    required_vars = ["DISCORD_BOT_TOKEN", "GEMINI_API_KEY", "DATABASE_PATH"]

    values: dict[str, str] = {}
    missing_vars: list[str] = []

    for name in required_vars:
        raw_value = os.getenv(name)
        if raw_value is None or raw_value.strip() == "":
            missing_vars.append(name)
            continue
        values[name] = raw_value.strip()

    if missing_vars:
        missing_list = ", ".join(missing_vars)
        raise ValueError(
            f"Missing required environment variables: {missing_list}. "
            "Add them to your environment, `setting.env`, or `.env` file."
        )

    admin_password = _resolve_admin_password()
    timezone = _parse_seed_timezone(os.getenv("TIMEZONE"))

    return Config(
        discord_bot_token=values["DISCORD_BOT_TOKEN"],
        gemini_api_key=values["GEMINI_API_KEY"],
        database_path=values["DATABASE_PATH"],
        admin_password=admin_password,
        admin_session_secret=(
            os.getenv("ADMIN_SESSION_SECRET", "").strip() or admin_password
        ),
        seed_source_channel_ids=_parse_optional_int_list_or_default(
            "SOURCE_CHANNEL_IDS",
            os.getenv("SOURCE_CHANNEL_IDS"),
            [],
        ),
        seed_reminder_channel_id=_parse_optional_int_or_default(
            "REMINDER_CHANNEL_ID",
            os.getenv("REMINDER_CHANNEL_ID"),
            0,
        ),
        seed_timezone=timezone,
        seed_nightly_summary_hour=_parse_time_part_or_default(
            "NIGHTLY_SUMMARY_HOUR",
            os.getenv("NIGHTLY_SUMMARY_HOUR"),
            0,
            23,
            0,
        ),
        seed_nightly_summary_minute=_parse_time_part_or_default(
            "NIGHTLY_SUMMARY_MINUTE",
            os.getenv("NIGHTLY_SUMMARY_MINUTE"),
            0,
            59,
            5,
        ),
        seed_morning_post_hour=_parse_time_part_or_default(
            "MORNING_POST_HOUR",
            os.getenv("MORNING_POST_HOUR"),
            0,
            23,
            9,
        ),
        seed_morning_post_minute=_parse_time_part_or_default(
            "MORNING_POST_MINUTE",
            os.getenv("MORNING_POST_MINUTE"),
            0,
            59,
            0,
        ),
        seed_dry_run=_parse_bool_or_default("DRY_RUN", os.getenv("DRY_RUN"), False),
        seed_dry_run_channel_id=_parse_optional_int(
            "DRY_RUN_CHANNEL_ID",
            os.getenv("DRY_RUN_CHANNEL_ID"),
        ),
        seed_allowed_user_ids=_parse_optional_int_list_or_default(
            "ALLOWED_USER_IDS",
            os.getenv("ALLOWED_USER_IDS"),
            [],
        ),
        seed_allowed_role_ids=_parse_optional_int_list_or_default(
            "ALLOWED_ROLE_IDS",
            os.getenv("ALLOWED_ROLE_IDS"),
            [],
        ),
    )


def _parse_int(name: str, value: str) -> int:
    """Parse an integer environment variable with a clear error message."""

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer. Received: {value!r}") from exc


def _parse_time_part(name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse and validate a time component such as hour or minute."""

    parsed_value = _parse_int(name, value)
    if not minimum <= parsed_value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}. "
            f"Received: {parsed_value}"
        )
    return parsed_value


def _validate_timezone(timezone_name: str) -> None:
    """Validate that the configured timezone exists."""

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"TIMEZONE is invalid: {timezone_name!r}. "
            "Use a valid IANA timezone such as 'Asia/Bangkok'."
        ) from exc


def _parse_bool(name: str, value: str) -> bool:
    """Parse a boolean-like environment variable."""

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(
        f"{name} must be a boolean value (true/false). Received: {value!r}"
    )


def _parse_optional_int(name: str, value: str | None) -> int | None:
    """Parse an optional integer environment variable."""

    if value is None or value.strip() == "":
        return None
    return _parse_int(name, value.strip())


def _parse_int_list(name: str, value: str) -> list[int]:
    """Parse a comma-separated integer list environment variable."""

    raw_items = [item.strip() for item in value.split(",")]
    items = [item for item in raw_items if item]
    if not items:
        raise ValueError(f"{name} must contain at least one channel ID.")
    return [_parse_int(name, item) for item in items]


def _parse_optional_int_list(name: str, value: str | None) -> list[int]:
    """Parse an optional comma-separated integer list."""

    if value is None or value.strip() == "":
        return []
    return _parse_int_list(name, value)


def _resolve_admin_password() -> str:
    """Resolve admin auth secret/password from env variables."""

    admin_password = (
        os.getenv("ADMIN_PASSWORD", "").strip()
        or os.getenv("ADMIN_AUTH_SECRET", "").strip()
    )
    if not admin_password:
        raise ValueError(
            "Missing required environment variable: ADMIN_PASSWORD "
            "(or ADMIN_AUTH_SECRET)."
        )
    return admin_password


def _parse_seed_timezone(raw_value: str | None) -> str:
    """Parse bootstrap timezone with a safe fallback."""

    timezone_name = (raw_value or "Asia/Bangkok").strip() or "Asia/Bangkok"
    try:
        _validate_timezone(timezone_name)
    except ValueError:
        return "Asia/Bangkok"
    return timezone_name


def _parse_time_part_or_default(
    name: str,
    value: str | None,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    """Parse bootstrap hour/minute; fallback on invalid values."""

    if value is None or value.strip() == "":
        return default
    try:
        return _parse_time_part(name, value, minimum, maximum)
    except ValueError:
        return default


def _parse_optional_int_or_default(name: str, value: str | None, default: int) -> int:
    """Parse optional integer; fallback on invalid values."""

    try:
        parsed = _parse_optional_int(name, value)
    except ValueError:
        return default
    return parsed if parsed is not None else default


def _parse_optional_int_list_or_default(
    name: str,
    value: str | None,
    default: list[int],
) -> list[int]:
    """Parse optional integer list; fallback on invalid values."""

    try:
        return _parse_optional_int_list(name, value)
    except ValueError:
        return list(default)


def _parse_bool_or_default(name: str, value: str | None, default: bool) -> bool:
    """Parse optional bool; fallback on invalid values."""

    if value is None or value.strip() == "":
        return default
    try:
        return _parse_bool(name, value)
    except ValueError:
        return default

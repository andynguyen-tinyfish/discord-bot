"""
Application entrypoint.

This module loads configuration, initializes storage, starts the Discord client,
and wires the scheduler into the bot lifecycle.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from datetime import date, datetime

import discord

from app.admin_dashboard import create_admin_app
from app.config import Config, load_config
from app.qa_responder import apply_target_reactions, handle_mention_qna
from app.scheduler import DailyScheduler, run_knowledge_ingestion_job, run_morning_job, run_nightly_job
from app.storage import RuntimeSettings, init_db


LOGGER = logging.getLogger(__name__)


class ReminderBot(discord.Client):
    """Discord client that runs either the scheduler loop or a one-off job."""

    def __init__(
        self,
        config: Config,
        mode: str,
        target_date: date | None = None,
        project_key: str | None = None,
    ) -> None:
        """Initialize the Discord client with runtime config and execution mode."""

        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True

        super().__init__(intents=intents)
        self.config = config
        self.mode = mode
        self.target_date = target_date
        self.project_key = project_key
        self.scheduler = DailyScheduler(self, config) if mode == "serve" else None

    async def on_ready(self) -> None:
        """Start the background scheduler or run a one-off job after login."""

        LOGGER.info("Discord client connected as %s", self.user)

        if self.mode == "serve":
            if self.scheduler is not None:
                self.scheduler.start()
            return

        if self.mode == "nightly":
            await run_nightly_job(self, self.config, target_date=self.target_date)
            await self.close()
            return

        if self.mode == "morning":
            await run_morning_job(self, self.config, target_date=self.target_date)
            await self.close()
            return

        if self.mode == "ingest-knowledge":
            await run_knowledge_ingestion_job(self, self.config, project_key=self.project_key)
            await self.close()

    async def on_message(self, message: discord.Message) -> None:
        """Answer mention-based Q&A in serve mode."""

        if self.mode != "serve":
            return
        if message.author.bot:
            return
        if self.user is None:
            return
        if self.user not in message.mentions:
            return
        try:
            result = await handle_mention_qna(self, self.config, message)
            await message.reply(result.reply_text, mention_author=False)
            await apply_target_reactions(self, message, result)
        except Exception:
            LOGGER.exception("Mention Q&A reply failed")


def main() -> None:
    """Run the Discord bot in scheduler mode or one-off manual mode."""

    args = _parse_args()
    config = load_config()
    _configure_logging()
    init_db(
        config.database_path,
        seed_settings=_build_seed_runtime_settings(config),
    )

    LOGGER.info("Database initialized at %s", config.database_path)
    LOGGER.info("Starting application in %s mode", args.mode)

    if args.mode == "all":
        _run_combined_mode(config=config, host=args.host, port=_resolve_web_port(args.port))
        return

    if args.mode == "admin":
        app = create_admin_app(config)
        app.run(host=args.host, port=_resolve_web_port(args.port))
        return

    target_date = _parse_target_date(args.date)
    if args.mode == "serve" and target_date is not None:
        raise ValueError("--date is supported only with --mode nightly or --mode morning.")
    if target_date is not None:
        LOGGER.info("Using custom target date: %s", target_date.isoformat())

    client = ReminderBot(
        config=config,
        mode=args.mode,
        target_date=target_date,
        project_key=args.project_key,
    )
    client.run(config.discord_bot_token, log_handler=None)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for scheduler or one-off execution."""

    parser = argparse.ArgumentParser(description="Discord QA reminder bot")
    parser.add_argument(
        "--mode",
        choices=("serve", "nightly", "morning", "admin", "all", "ingest-knowledge"),
        default="serve",
        help="Run scheduler, one-off jobs, internal admin dashboard, or combined mode.",
    )
    parser.add_argument(
        "--date",
        help="Custom target date in YYYY-MM-DD (for nightly/morning one-off runs).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Admin dashboard host (used with --mode admin or --mode all).",
    )
    parser.add_argument(
        "--port",
        default=None,
        type=int,
        help="Admin dashboard port (used with --mode admin or --mode all). Defaults to $PORT or 8080.",
    )
    parser.add_argument(
        "--project-key",
        help="Optional project key filter (used with --mode ingest-knowledge).",
    )
    return parser.parse_args()


def _configure_logging() -> None:
    """Configure basic application logging."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _parse_target_date(raw_date: str | None) -> date | None:
    """Parse an optional custom target date argument."""

    if raw_date is None:
        return None
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid --date value: {raw_date!r}. Use format YYYY-MM-DD."
        ) from exc


def _build_seed_runtime_settings(config: Config) -> RuntimeSettings:
    """Build initial runtime settings from env bootstrap values."""

    return RuntimeSettings(
        source_channel_ids=config.seed_source_channel_ids,
        reminder_channel_id=config.seed_reminder_channel_id,
        timezone=config.seed_timezone,
        nightly_summary_hour=config.seed_nightly_summary_hour,
        nightly_summary_minute=config.seed_nightly_summary_minute,
        morning_post_hour=config.seed_morning_post_hour,
        morning_post_minute=config.seed_morning_post_minute,
        dry_run=config.seed_dry_run,
        dry_run_channel_id=config.seed_dry_run_channel_id,
        allowed_user_ids=config.seed_allowed_user_ids,
        allowed_role_ids=config.seed_allowed_role_ids,
        designated_role_id=None,
        shared_post_channel_id=None,
        shared_knowledge_channel_ids=[],
        shared_knowledge_file_paths=[],
        project_configs=[],
    )


def _resolve_web_port(port_arg: int | None) -> int:
    """Resolve web port from CLI arg, then $PORT, then default 8080."""

    if port_arg is not None:
        return port_arg
    env_port = os.getenv("PORT", "").strip()
    if env_port:
        try:
            return int(env_port)
        except ValueError as exc:
            raise ValueError(f"Invalid PORT value: {env_port!r}. Must be an integer.") from exc
    return 8080


def _run_combined_mode(config: Config, host: str, port: int) -> None:
    """Run bot worker + scheduler with admin dashboard in one process."""

    client = ReminderBot(config=config, mode="serve")
    app = create_admin_app(config)
    bot_started = threading.Event()

    def _run_bot() -> None:
        bot_started.set()
        client.run(config.discord_bot_token, log_handler=None)

    bot_thread = threading.Thread(target=_run_bot, name="discord-bot", daemon=True)
    bot_thread.start()
    bot_started.wait(timeout=2.0)
    LOGGER.info("Combined mode: bot worker started in background thread.")
    LOGGER.info("Combined mode: starting admin server on %s:%s", host, port)

    try:
        app.run(host=host, port=port)
    finally:
        try:
            if getattr(client, "loop", None) and client.loop.is_running():
                future = asyncio.run_coroutine_threadsafe(client.close(), client.loop)
                future.result(timeout=5)
        except Exception:
            LOGGER.exception("Combined mode shutdown: failed to close Discord client cleanly")


if __name__ == "__main__":
    main()

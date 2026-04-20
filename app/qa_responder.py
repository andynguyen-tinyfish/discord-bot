"""
Mention-based Q&A reply flow with project-filtered knowledge retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib import request

import discord

from app.config import Config
from app.knowledge import resolve_project_context, retrieve_project_knowledge
from app.prompt_templates import QA_MENTION_REPLY_PROMPT
from app.storage import get_runtime_settings


LOGGER = logging.getLogger(__name__)
GEMINI_TEXT_MODEL = "gemini-2.5-flash"
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TARGETING_PHRASE_PATTERNS = (
    r"\banswer this\b",
    r"\bhow about this question\b",
    r"\banswer the one above\b",
    r"\bplease answer above\b",
    r"\bone above\b",
    r"\bthis question\b",
)
QUESTION_HINT_PATTERNS = (
    r"\?$",
    r"^(how|what|why|when|where|which|who|can|should|is|are|do|does|did)\b",
)
STYLE_MODES = ("direct", "playful", "concise", "clarifying")
DISCORD_MESSAGE_LINK_PATTERN = re.compile(
    r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)"
)


@dataclass(frozen=True)
class TargetResolution:
    """Resolved primary target used for Q&A answering."""

    target_text: str
    target_author: str
    target_author_id: int | None
    target_message_id: int | None
    target_channel_id: int | None
    reason: str
    needs_clarification: bool = False
    clarification_text: str = ""


@dataclass(frozen=True)
class QAReplyResult:
    """Structured mention Q&A result used by message handler."""

    reply_text: str
    target_message_id: int | None = None
    target_channel_id: int | None = None
    style_mode: str = "direct"
    target_reason: str = ""
    should_react: bool = False


async def handle_mention_qna(
    client: discord.Client,
    config: Config,
    message: discord.Message,
) -> QAReplyResult:
    """Handle one mention question and return structured reply result."""

    settings = get_runtime_settings(config.database_path)
    project = resolve_project_context(
        settings=settings,
        channel_id=int(message.channel.id),
        message_text=message.content,
    )
    if project is None:
        return QAReplyResult(
            reply_text=(
                "Yep, I saw the mention 👀 but I can't map this channel/message to a configured project yet. "
                "Please include project key/name or update project routing."
            )
        )

    invocation_text = _strip_bot_mention(message)
    nearby_messages = await _collect_nearby_messages(message, limit=24)
    resolution = await _resolve_primary_target(message, invocation_text, nearby_messages)
    if resolution.needs_clarification:
        LOGGER.info(
            "Q&A target ambiguous message_id=%s project=%s reason=%s",
            message.id,
            project.key,
            resolution.reason,
        )
        return QAReplyResult(
            reply_text=_build_reply_prefix(
                invoker_id=int(message.author.id),
                target_author_id=resolution.target_author_id,
                mode="clarifying",
                serious_mode=True,
            )
            + resolution.clarification_text,
            style_mode="clarifying",
            target_reason=resolution.reason,
        )

    retrieval_query = f"{resolution.target_text}\n{invocation_text}".strip()
    chunks = retrieve_project_knowledge(
        settings=settings,
        project=project,
        query=retrieval_query,
        database_path=config.database_path,
        limit=10,
    )

    serious_mode = _is_serious_policy_question(resolution.target_text)
    style_mode = _select_style_mode(
        invocation_id=int(message.id),
        target_id=resolution.target_message_id,
        serious_mode=serious_mode,
    )
    prompt = _build_user_prompt(
        project_name=project.name,
        invocation_text=invocation_text,
        target=resolution,
        nearby_messages=nearby_messages[-12:],
        knowledge_chunks=chunks,
        serious_mode=serious_mode,
        style_mode=style_mode,
    )

    LOGGER.info(
        "Q&A target selected invocation=%s target=%s reason=%s project=%s",
        message.id,
        resolution.target_message_id,
        resolution.reason,
        project.key,
    )
    LOGGER.info(
        "Q&A context invocation=%s style=%s nearby_ids=%s chunk_refs=%s",
        message.id,
        style_mode,
        [row["id"] for row in nearby_messages[-12:]],
        [f"{row.get('source_type')}:{row.get('source_name')}#{row.get('chunk_index')}" for row in chunks],
    )

    try:
        response = _call_gemini_text(
            api_key=config.gemini_api_key,
            model=GEMINI_TEXT_MODEL,
            system_prompt=QA_MENTION_REPLY_PROMPT,
            user_prompt=prompt,
        )
        text = response.strip()
        if not text:
            raise ValueError("empty_response")
        prefix = _build_reply_prefix(
            invoker_id=int(message.author.id),
            target_author_id=resolution.target_author_id,
            mode=style_mode,
            serious_mode=serious_mode,
        )
        return QAReplyResult(
            reply_text=f"{prefix}{text}",
            target_message_id=resolution.target_message_id,
            target_channel_id=resolution.target_channel_id,
            style_mode=style_mode,
            target_reason=resolution.reason,
            should_react=True,
        )
    except Exception as exc:
        LOGGER.warning("Q&A mention response fallback (%s)", exc)
        prefix = _build_reply_prefix(
            invoker_id=int(message.author.id),
            target_author_id=resolution.target_author_id,
            mode="direct",
            serious_mode=True,
        )
        return QAReplyResult(
            reply_text=(
                f"{prefix}Short answer: I couldn't confidently generate a grounded reply right now 😅\n"
                "Please check the latest project guidelines/announcements, or rerun knowledge ingestion and ask again."
            ),
            target_message_id=resolution.target_message_id,
            target_channel_id=resolution.target_channel_id,
            style_mode="direct",
            target_reason=resolution.reason,
        )


def _strip_bot_mention(message: discord.Message) -> str:
    text = str(message.content or "")
    if message.guild is None or message.guild.me is None:
        return text.strip()
    mention_tokens = {
        f"<@{message.guild.me.id}>",
        f"<@!{message.guild.me.id}>",
    }
    cleaned = text
    for token in mention_tokens:
        cleaned = cleaned.replace(token, " ")
    return " ".join(cleaned.split()).strip()


async def _collect_nearby_messages(message: discord.Message, limit: int = 24) -> list[dict[str, Any]]:
    """Collect recent nearby messages for supporting context."""

    rows: list[dict[str, Any]] = []
    async for row in message.channel.history(limit=limit, before=message.created_at, oldest_first=False):
        content = str(row.content or "").strip()
        if not content:
            continue
        rows.append(
            {
                "id": int(row.id),
                "author": str(row.author.display_name),
                "author_id": int(row.author.id),
                "author_is_bot": bool(getattr(row.author, "bot", False)),
                "content": content,
                "created_at": row.created_at.isoformat(),
            }
        )
    rows.reverse()
    return rows


async def _resolve_primary_target(
    message: discord.Message,
    invocation_text: str,
    nearby_messages: list[dict[str, Any]],
) -> TargetResolution:
    """Resolve primary target by strict priority and avoid guessing."""

    reply_target = await _resolve_reply_target_message(message)
    if reply_target is not None:
        return TargetResolution(
            target_text=str(reply_target.content or "").strip(),
            target_author=str(reply_target.author.display_name),
            target_author_id=int(reply_target.author.id),
            target_message_id=int(reply_target.id),
            target_channel_id=int(reply_target.channel.id),
            reason="priority_a_reply_target",
        )

    explicit_target = await _resolve_explicit_reference_target(message, invocation_text, nearby_messages)
    if explicit_target is not None:
        return explicit_target

    if _contains_targeting_phrase(invocation_text):
        candidate = _find_nearest_relevant_candidate(nearby_messages)
        if candidate is None:
            return TargetResolution(
                target_text="",
                target_author="",
                target_author_id=None,
                target_message_id=None,
                target_channel_id=None,
                reason="priority_c_no_candidate",
                needs_clarification=True,
                clarification_text=(
                    "I might be missing which message you want me to answer 🤔 "
                    "Can you reply directly to that message or paste the message link?"
                ),
            )
        if candidate.get("ambiguous_with"):
            other = candidate["ambiguous_with"]
            return TargetResolution(
                target_text="",
                target_author="",
                target_author_id=None,
                target_message_id=None,
                target_channel_id=None,
                reason="priority_d_ambiguous",
                needs_clarification=True,
                clarification_text=(
                    f"Do you mean {candidate['author']}'s question above: "
                    f"\"{_snippet(candidate['content'])}\" "
                    f"or {other['author']}'s one: \"{_snippet(other['content'])}\"?"
                ),
            )
        return TargetResolution(
            target_text=str(candidate["content"]),
            target_author=str(candidate["author"]),
            target_author_id=int(candidate.get("author_id")) if str(candidate.get("author_id", "")).isdigit() else None,
            target_message_id=int(candidate["id"]),
            target_channel_id=int(message.channel.id),
            reason="priority_c_nearest_relevant_above",
        )

    if _is_question_like(invocation_text):
        return TargetResolution(
            target_text=invocation_text,
            target_author=str(message.author.display_name),
            target_author_id=int(message.author.id),
            target_message_id=int(message.id),
            target_channel_id=int(message.channel.id),
            reason="invocation_self_question",
        )

    fallback_candidate = _find_nearest_relevant_candidate(nearby_messages)
    if fallback_candidate is not None:
        return TargetResolution(
            target_text="",
            target_author="",
            target_author_id=None,
            target_message_id=None,
            target_channel_id=None,
            reason="priority_d_ambiguous_fallback",
            needs_clarification=True,
            clarification_text=(
                f"Do you mean {fallback_candidate['author']}'s question above about "
                f"\"{_snippet(fallback_candidate['content'])}\"?"
            ),
        )

    return TargetResolution(
        target_text="",
        target_author="",
        target_author_id=None,
        target_message_id=None,
        target_channel_id=None,
        reason="priority_d_no_target",
        needs_clarification=True,
        clarification_text=(
            "I’m not sure which question you want me to answer. "
            "Please reply directly to it or paste its message link."
        ),
    )


async def _resolve_reply_target_message(message: discord.Message) -> discord.Message | None:
    reference = message.reference
    if reference is None:
        return None
    if isinstance(reference.resolved, discord.Message):
        return reference.resolved
    if reference.message_id is None:
        return None
    try:
        fetched = await message.channel.fetch_message(reference.message_id)
        return fetched
    except Exception:
        return None


async def _resolve_explicit_reference_target(
    message: discord.Message,
    invocation_text: str,
    nearby_messages: list[dict[str, Any]],
) -> TargetResolution | None:
    link_match = DISCORD_MESSAGE_LINK_PATTERN.search(invocation_text)
    if link_match:
        channel_id = int(link_match.group(1))
        message_id = int(link_match.group(2))
        channel = message.guild.get_channel(channel_id) if message.guild else None
        if channel is None and message.guild is not None:
            try:
                channel = await message.guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if channel is not None and hasattr(channel, "fetch_message"):
            try:
                target = await channel.fetch_message(message_id)
                return TargetResolution(
                    target_text=str(target.content or "").strip(),
                    target_author=str(target.author.display_name),
                    target_author_id=int(target.author.id),
                    target_message_id=int(target.id),
                    target_channel_id=int(target.channel.id),
                    reason="priority_b_message_link",
                )
            except Exception:
                pass

    quote_lines = [line[1:].strip() for line in invocation_text.splitlines() if line.strip().startswith(">")]
    quote_lines = [line for line in quote_lines if len(line) >= 8]
    if quote_lines:
        quoted = quote_lines[0].lower()
        for row in reversed(nearby_messages):
            if row["author_is_bot"]:
                continue
            content = str(row["content"]).lower()
            if quoted in content or content in quoted:
                return TargetResolution(
                    target_text=str(row["content"]),
                    target_author=str(row["author"]),
                    target_author_id=int(row.get("author_id")) if str(row.get("author_id", "")).isdigit() else None,
                    target_message_id=int(row["id"]),
                    target_channel_id=int(message.channel.id),
                    reason="priority_b_quote_match",
                )
    return None


def _contains_targeting_phrase(text: str) -> bool:
    lowered = text.lower().strip()
    return any(re.search(pattern, lowered) for pattern in TARGETING_PHRASE_PATTERNS)


def _is_question_like(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    return any(re.search(pattern, lowered) for pattern in QUESTION_HINT_PATTERNS)


def _find_nearest_relevant_candidate(nearby_messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    non_bot = [row for row in nearby_messages if not row["author_is_bot"]]
    if not non_bot:
        return None
    question_candidates = [row for row in reversed(non_bot) if _is_question_like(str(row["content"]))]
    if question_candidates:
        best = question_candidates[0]
        if len(question_candidates) >= 2:
            second = question_candidates[1]
            if _close_competing_questions(best, second):
                with_ambiguity = dict(best)
                with_ambiguity["ambiguous_with"] = second
                return with_ambiguity
        return best
    return list(reversed(non_bot))[0]


def _close_competing_questions(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_tokens = _tokenize(str(first["content"]))
    second_tokens = _tokenize(str(second["content"]))
    shared = len(first_tokens & second_tokens)
    return shared >= 2


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", text.lower())}


def _snippet(text: str, limit: int = 70) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _is_serious_policy_question(text: str) -> bool:
    lowered = text.lower()
    keywords = ("policy", "guideline", "label", "classification", "security", "blocker", "risk", "should")
    return any(keyword in lowered for keyword in keywords)


def _build_user_prompt(
    project_name: str,
    invocation_text: str,
    target: TargetResolution,
    nearby_messages: list[dict[str, Any]],
    knowledge_chunks: list[dict[str, Any]],
    serious_mode: bool,
    style_mode: str,
) -> str:
    history_block = (
        "\n".join(
            f"- [{row['id']}] {row['author']}: {row['content']}"
            for row in nearby_messages
        )
        or "- (no nearby context)"
    )
    chunks_block = (
        "\n".join(
            (
                f"- [{chunk.get('source_type')}:{chunk.get('source_name')}#{chunk.get('chunk_index')}] "
                f"{str(chunk.get('chunk_text', ''))[:700]}"
            )
            for chunk in knowledge_chunks
        )
        or "- (no retrieved knowledge)"
    )
    target_meta = (
        f"id={target.target_message_id}, author={target.target_author}, reason={target.reason}"
        if target.target_message_id is not None
        else f"author={target.target_author}, reason={target.reason}"
    )
    tone_mode = "careful" if serious_mode else style_mode
    return (
        f"PROJECT: {project_name}\n"
        f"TONE MODE: {tone_mode}\n\n"
        f"PRIMARY TARGET QUESTION:\n"
        f"[{target_meta}]\n"
        f"{target.target_text}\n\n"
        f"BOT INVOCATION MESSAGE:\n"
        f"{invocation_text or '(empty invocation message)'}\n\n"
        f"NEARBY CHANNEL CONTEXT:\n"
        f"{history_block}\n\n"
        f"RETRIEVED KNOWLEDGE / GUIDELINES:\n"
        f"{chunks_block}\n\n"
        "Answer the PRIMARY TARGET QUESTION first."
    )


def _call_gemini_text(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Call Gemini and return plain text response."""

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    endpoint = GEMINI_API_URL_TEMPLATE.format(model=model)
    api_request = request.Request(
        f"{endpoint}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(api_request, timeout=30) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    parts = parsed["candidates"][0]["content"]["parts"]
    return "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()


def _select_style_mode(invocation_id: int, target_id: int | None, serious_mode: bool) -> str:
    if serious_mode:
        return "direct"
    seed = invocation_id + (target_id or 0)
    return STYLE_MODES[seed % len(STYLE_MODES)]


def _build_reply_prefix(
    invoker_id: int,
    target_author_id: int | None,
    mode: str,
    serious_mode: bool,
) -> str:
    invoker_mention = f"<@{invoker_id}>"
    if target_author_id is None or target_author_id == invoker_id:
        options = (
            f"{invoker_mention} — ",
            f"{invoker_mention}, ",
            "",
        )
        return options[(invoker_id + len(mode)) % len(options)]

    asker_mention = f"<@{target_author_id}>"
    if serious_mode:
        return f"{asker_mention} cc {invoker_mention} — "
    options = (
        f"{asker_mention} cc {invoker_mention} — ",
        f"{invoker_mention} looping in {asker_mention} — ",
        f"{asker_mention} + {invoker_mention} — ",
    )
    return options[(invoker_id + target_author_id + len(mode)) % len(options)]


async def apply_target_reactions(
    client: discord.Client,
    invocation_message: discord.Message,
    result: QAReplyResult,
) -> None:
    """Add lightweight reactions to resolved target message."""

    if not result.should_react:
        return
    if result.target_message_id is None or result.target_channel_id is None:
        return

    target_message = await _fetch_target_message(client, invocation_message, result.target_channel_id, result.target_message_id)
    if target_message is None:
        return

    emojis = _pick_reactions(result.style_mode, result.target_reason)
    LOGGER.info(
        "Q&A reactions target=%s channel=%s emojis=%s",
        result.target_message_id,
        result.target_channel_id,
        emojis,
    )
    for emoji in emojis:
        try:
            await target_message.add_reaction(emoji)
        except Exception:
            LOGGER.debug("Failed adding reaction %s to target=%s", emoji, result.target_message_id)


async def _fetch_target_message(
    client: discord.Client,
    invocation_message: discord.Message,
    channel_id: int,
    message_id: int,
) -> discord.Message | None:
    if int(invocation_message.channel.id) == channel_id:
        try:
            return await invocation_message.channel.fetch_message(message_id)
        except Exception:
            return None
    channel = client.get_channel(channel_id)
    if channel is None and invocation_message.guild is not None:
        try:
            channel = await invocation_message.guild.fetch_channel(channel_id)
        except Exception:
            channel = None
    if channel is None or not hasattr(channel, "fetch_message"):
        return None
    try:
        return await channel.fetch_message(message_id)
    except Exception:
        return None


def _pick_reactions(style_mode: str, target_reason: str) -> list[str]:
    if "ambiguous" in target_reason:
        return ["🤔"]
    if style_mode in {"direct", "clarifying"}:
        return ["👀", "✅"]
    if style_mode == "concise":
        return ["✅"]
    if style_mode == "playful":
        return ["👀", "🤝"]
    return ["✅"]

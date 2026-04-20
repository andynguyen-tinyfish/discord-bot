"""
Daily project summary generation.

This module sends filtered messages to an LLM, validates the JSON response, and
returns a stable summary object for downstream storage and posting.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request

from app.config import load_config
from app.prompt_templates import QA_SUMMARY_JSON_PROMPT


LOGGER = logging.getLogger(__name__)
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-2.5-flash"
MAX_HIGHLIGHTS = 5
MAX_BLOCKERS = 5
MAX_FOLLOW_UPS = 5
SUMMARY_PROMPT = QA_SUMMARY_JSON_PROMPT


def summarize_messages(
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    model: str = GEMINI_MODEL,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Summarize filtered messages and return a validated summary object."""

    summary, _meta = summarize_messages_with_meta(
        messages,
        api_key=api_key,
        model=model,
        project_name=project_name,
    )
    return summary


def summarize_messages_with_meta(
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    model: str = GEMINI_MODEL,
    project_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Summarize messages and return both summary and execution metadata."""

    if not messages:
        return _fallback_summary(), {
            "llm_attempted": False,
            "llm_success": False,
            "used_fallback": True,
            "fallback_reason": "no_messages",
            "model": model,
            "input_messages": 0,
        }

    resolved_api_key = api_key or load_config().gemini_api_key
    payload = _build_request_payload(messages, model, project_name=project_name)

    try:
        raw_content = _call_llm_api(payload, resolved_api_key, model)
        summary = _parse_summary_json(raw_content)
        validated = _validate_summary(summary)
        return validated, {
            "llm_attempted": True,
            "llm_success": True,
            "used_fallback": False,
            "fallback_reason": "",
            "model": model,
            "input_messages": len(messages),
        }
    except (ValueError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
        reason = _build_error_reason(exc)
        LOGGER.warning("Gemini summarization failed (%s)", reason)
        return _fallback_summary(messages), {
            "llm_attempted": True,
            "llm_success": False,
            "used_fallback": True,
            "fallback_reason": reason,
            "model": model,
            "input_messages": len(messages),
        }


def _build_request_payload(
    messages: list[dict[str, Any]],
    model: str,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Build the Gemini API request payload."""

    return {
        "system_instruction": {
            "parts": [{"text": SUMMARY_PROMPT}],
        },
        "contents": [
            {
                "parts": [{"text": _format_messages_for_prompt(messages, project_name=project_name)}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }


def _format_messages_for_prompt(messages: list[dict[str, Any]], project_name: str | None = None) -> str:
    """Format normalized messages into a compact prompt input block."""

    project = (project_name or "this project").strip()
    lines = [
        f"Project name: {project}",
        "Summarize these QA messages into practical internal reminders for this project:",
    ]
    for message in messages:
        author_name = str(message.get("author_name", "Unknown"))
        created_at = str(message.get("created_at", ""))
        content = str(message.get("content", "")).strip()
        lines.append(f"- [{created_at}] {author_name}: {content}")
    return "\n".join(lines)


def _call_llm_api(payload: dict[str, Any], api_key: str, model: str) -> str:
    """Call the Gemini API and return the raw response content string."""

    body = json.dumps(payload).encode("utf-8")
    endpoint = GEMINI_API_URL_TEMPLATE.format(model=model)
    api_request = request.Request(
        f"{endpoint}?key={api_key}",
        data=body,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with request.urlopen(api_request, timeout=30) as response:
        response_body = response.read().decode("utf-8")

    parsed = json.loads(response_body)
    try:
        parts = parsed["candidates"][0]["content"]["parts"]
        text_parts = [str(part.get("text", "")) for part in parts if isinstance(part, dict)]
        content = "".join(text_parts).strip()
        if not content:
            raise ValueError("Gemini response has empty content.")
        return content
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected API response shape from Gemini provider.") from exc


def _parse_summary_json(raw_content: str) -> dict[str, Any]:
    """Parse the model response into a Python dictionary."""

    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


def _validate_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the summary JSON shape."""

    required_keys = {"highlights", "blockers", "follow_ups", "final_message"}
    missing_keys = required_keys - summary.keys()
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise ValueError(f"Summary JSON is missing required keys: {missing}")

    blockers = _dedupe_items(_validate_string_list("blockers", summary["blockers"]))
    follow_ups = _remove_overlap(
        blockers,
        _dedupe_items(_validate_string_list("follow_ups", summary["follow_ups"])),
    )
    highlights = _remove_overlap(
        blockers + follow_ups,
        _dedupe_items(_validate_string_list("highlights", summary["highlights"])),
    )

    validated = {
        "highlights": highlights[:MAX_HIGHLIGHTS],
        "blockers": blockers[:MAX_BLOCKERS],
        "follow_ups": follow_ups[:MAX_FOLLOW_UPS],
        "final_message": _validate_final_message(summary["final_message"]),
    }

    if not validated["final_message"]:
        validated["final_message"] = _build_final_message_from_sections(validated)

    return validated


def _validate_string_list(name: str, value: Any) -> list[str]:
    """Validate that a summary field is a list of strings."""

    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")

    normalized_items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{name} must contain only strings.")
        cleaned = item.strip()
        if cleaned:
            normalized_items.append(cleaned)

    return normalized_items


def _validate_final_message(value: Any) -> str:
    """Validate that the final message is a string."""

    if not isinstance(value, str):
        raise ValueError("final_message must be a string.")
    return value.strip()


def _fallback_summary(messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a safe fallback summary when summarization fails."""

    if not messages:
        return {
            "highlights": [],
            "blockers": [],
            "follow_ups": [],
            "final_message": "No significant QA updates for this period.",
        }

    if len(messages) <= 3:
        highlights = _build_low_signal_highlights(messages)
        return {
            "highlights": highlights,
            "blockers": [],
            "follow_ups": ["Continue monitoring and log concrete blockers when they appear."],
            "final_message": "Limited meaningful QA signal; no confirmed blockers from available messages.",
        }

    return {
        "highlights": [],
        "blockers": [],
        "follow_ups": [],
        "final_message": "Unable to generate an automatic summary. Please review the source messages.",
    }


def _dedupe_items(items: list[str]) -> list[str]:
    """Deduplicate semantically similar items using a lightweight canonical key."""

    deduped: list[str] = []
    seen_keys: set[str] = set()
    for item in items:
        key = _canonicalize_item(item)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    return deduped


def _remove_overlap(reference_items: list[str], items: list[str]) -> list[str]:
    """Remove items that overlap with an existing section."""

    reference_keys = {_canonicalize_item(item) for item in reference_items}
    return [item for item in items if _canonicalize_item(item) not in reference_keys]


def _canonicalize_item(item: str) -> str:
    """Build a normalized comparison key for summary items."""

    lowered = item.lower().strip()
    compact = "".join(character if character.isalnum() or character.isspace() else " " for character in lowered)
    tokens = [token for token in compact.split() if token]
    return " ".join(tokens)


def _build_final_message_from_sections(summary: dict[str, Any]) -> str:
    """Build a stable one-line operational message when model output is empty."""

    blocker_count = len(summary.get("blockers", []))
    follow_up_count = len(summary.get("follow_ups", []))
    if blocker_count > 0:
        return f"{blocker_count} blocker(s) need attention; prioritize follow-ups today."
    if follow_up_count > 0:
        return f"No major blockers; {follow_up_count} follow-up item(s) are queued."
    return "No major blockers or follow-ups identified."


def _build_low_signal_highlights(messages: list[dict[str, Any]]) -> list[str]:
    """Create lightweight highlights for very small input sets."""

    highlights: list[str] = []
    for message in messages[:2]:
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 140:
            content = f"{content[:137]}..."
        highlights.append(content)
    return _dedupe_items(highlights)


def _build_error_reason(exc: Exception) -> str:
    """Build a compact reason string for summarization fallback logging."""

    if isinstance(exc, error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, error.URLError):
        return "network_error"
    if isinstance(exc, json.JSONDecodeError):
        return "json_decode_error"
    return str(exc)[:140]

"""Utilities for cleaning raw LLM outputs before JSON parsing."""

from __future__ import annotations

import json
import re

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL
)
_LEADING_JSON_PATTERN = re.compile(r"^[^\{\[]*([\{\[])", re.DOTALL)
_TRAILING_JSON_PATTERN = re.compile(r"([\}\]])[^\}\]]*$", re.DOTALL)


def _extract_balanced_json_fragment(text: str) -> str | None:
    """Extract first balanced JSON object/array while respecting JSON strings."""
    start = -1
    opening = ""
    closing = ""
    depth = 0
    in_string = False
    escaped = False

    for idx, ch in enumerate(text):
        if start == -1:
            if ch == "{":
                start = idx
                opening, closing = "{", "}"
                depth = 1
            elif ch == "[":
                start = idx
                opening, closing = "[", "]"
                depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def clean_json_output(raw_text: str) -> str:
    """Clean LLM output into a JSON-like string.

    This helper strips markdown code fences and trims explanatory text that
    sometimes appears before or after a JSON payload.
    """
    text = raw_text.strip()

    fenced_match = _JSON_FENCE_PATTERN.search(text)
    if fenced_match:
        text = fenced_match.group(1).strip()

    leading_match = _LEADING_JSON_PATTERN.search(text)
    trailing_match = _TRAILING_JSON_PATTERN.search(text)
    if leading_match and trailing_match:
        start = leading_match.start(1)
        end = trailing_match.end(1)
        if start < end:
            text = text[start:end]

    # Some providers return JSON as a quoted JSON string.
    for _ in range(2):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, str):
            text = parsed.strip()
            continue
        return json.dumps(parsed, ensure_ascii=False)

    # Fallback for noisy outputs: keep the first balanced JSON fragment.
    fragment = _extract_balanced_json_fragment(text)
    if fragment is not None:
        return fragment.strip()

    return text.strip()

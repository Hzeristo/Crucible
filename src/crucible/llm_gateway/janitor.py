"""Utilities for cleaning raw LLM outputs before JSON parsing."""

from __future__ import annotations

import re

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL
)
_LEADING_JSON_PATTERN = re.compile(r"^[^\{\[]*([\{\[])", re.DOTALL)
_TRAILING_JSON_PATTERN = re.compile(r"([\}\]])[^\}\]]*$", re.DOTALL)


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

    return text.strip()

"""Composition root helpers: wire Settings into concrete clients (no business logic)."""

from __future__ import annotations

import logging

from pydantic import SecretStr

from src.crucible.core.config import Settings
from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)


def build_openai_client(settings: Settings) -> OpenAICompatibleClient:
    """Build LLM client from loaded settings (single place for key resolution)."""
    if settings.OPENAI_API_KEY is None:
        raise ValueError(
            "OPENAI_API_KEY is required: set in environment or .env before building OpenAICompatibleClient."
        )
    api_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is empty.")
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=settings.default_llm_base_url,
        model=settings.default_llm_model,
        timeout_seconds=float(settings.default_llm_timeout_seconds),
    )


def build_openai_client_from_params(
    *,
    api_key: str | SecretStr | None,
    base_url: str | None,
    model: str | None,
    timeout_seconds: float | None = None,
    default_settings: Settings | None = None,
) -> OpenAICompatibleClient:
    """
    Build client for per-request overrides (e.g. Oligo). Falls back to default_settings for None fields.
    """
    s = default_settings
    if s is None:
        from src.crucible.core.config import load_config

        s = load_config()
    resolved_key: str | None = None
    if api_key is not None and str(api_key).strip():
        resolved_key = (
            api_key.get_secret_value().strip()
            if isinstance(api_key, SecretStr)
            else str(api_key).strip()
        )
    if not resolved_key and s.OPENAI_API_KEY is not None:
        resolved_key = s.OPENAI_API_KEY.get_secret_value().strip()
    if not resolved_key:
        raise ValueError("api_key is required for OpenAICompatibleClient.")
    return OpenAICompatibleClient(
        api_key=resolved_key,
        base_url=base_url if base_url else s.default_llm_base_url,
        model=model if model else s.default_llm_model,
        timeout_seconds=(
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(s.default_llm_timeout_seconds)
        ),
    )


def build_wash_client(settings: Settings) -> OpenAICompatibleClient | None:
    """
    Optional cheap OpenAI-compatible client for Oligo Wash / dirty-work paths.
    Requires ``WASH_MODEL_BASE_URL`` and ``WASH_MODEL_NAME`` (non-empty).
    API key: ``WASH_MODEL_API_KEY`` if set, else falls back to ``OPENAI_API_KEY``.
    """
    base_url = (settings.WASH_MODEL_BASE_URL or "").strip()
    model = (settings.WASH_MODEL_NAME or "").strip()
    if not base_url or not model:
        return None

    resolved_key: str | None = None
    if settings.WASH_MODEL_API_KEY is not None:
        resolved_key = settings.WASH_MODEL_API_KEY.get_secret_value().strip()
    if not resolved_key and settings.OPENAI_API_KEY is not None:
        resolved_key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not resolved_key:
        logger.warning(
            "[Oligo] WASH_MODEL_BASE_URL and WASH_MODEL_NAME are set but no API key "
            "(configure WASH_MODEL_API_KEY or OPENAI_API_KEY); wash_client disabled."
        )
        return None

    return OpenAICompatibleClient(
        api_key=resolved_key,
        base_url=base_url,
        model=model,
        timeout_seconds=float(settings.default_llm_timeout_seconds),
        provider_name="Wash (cheap)",
    )

"""
LLM Client 工厂函数。

推荐使用：
- build_openai_client_from_model_config: 从 ChimeraConfig.llm.* 构建
- build_openai_client_from_params: 从请求参数构建（Astrocyte 所选槽与 `llm.providers` / env 对齐）
- build_openai_client: 从全局配置构建（批处理脚本）

已废弃：
- build_wash_client: 已删除，使用 build_openai_client_from_model_config(settings, settings.llm.wash)
"""

from __future__ import annotations

import logging

from pydantic import SecretStr

from src.crucible.core.config import ChimeraConfig, LLMModelConfig, get_config
from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)


def _resolve_slot_api_key(settings: ChimeraConfig, model_cfg: LLMModelConfig) -> str:
    k = (model_cfg.api_key or "").strip()
    if k:
        return k
    p = (model_cfg.provider or "").lower()
    if p == "openai" and settings.OPENAI_API_KEY is not None:
        return settings.OPENAI_API_KEY.get_secret_value().strip()
    if p == "deepseek" and settings.DEEPSEEK_API_KEY is not None:
        return settings.DEEPSEEK_API_KEY.get_secret_value().strip()
    if p == "anthropic" and settings.ANTHROPIC_API_KEY is not None:
        return settings.ANTHROPIC_API_KEY.get_secret_value().strip()
    if p == "gemini" and settings.GEMINI_API_KEY is not None:
        return settings.GEMINI_API_KEY.get_secret_value().strip()
    if settings.OPENAI_API_KEY is not None:
        v = settings.OPENAI_API_KEY.get_secret_value().strip()
        if v:
            return v
    raise ValueError(
        f"Empty api_key in config for provider={model_cfg.provider!r} and no usable root _API_KEY.",
    )


def _secret_strip(secret: SecretStr | None) -> str:
    if secret is None:
        return ""
    return (secret.get_secret_value() or "").strip()


def _fallback_api_key_from_base_url(settings: ChimeraConfig, base_url: str) -> str | None:
    """Oligo 请求体未带 key 时：按 ``base_url`` 主机特征匹配对应 ``*_API_KEY``。"""
    u = (base_url or "").strip().lower()
    if not u:
        return None
    if "openai.com" in u:
        k = _secret_strip(settings.OPENAI_API_KEY)
        return k or None
    if "deepseek.com" in u:
        k = _secret_strip(settings.DEEPSEEK_API_KEY)
        return k or None
    if "anthropic.com" in u:
        k = _secret_strip(settings.ANTHROPIC_API_KEY)
        return k or None
    if "generativelanguage.googleapis.com" in u:
        k = _secret_strip(settings.GEMINI_API_KEY)
        return k or None
    return None


def build_openai_client_from_model_config(
    settings: ChimeraConfig,
    model_cfg: LLMModelConfig,
    *,
    provider_name: str = "LLM",
) -> OpenAICompatibleClient | None:
    """
    Build a single long-lived :class:`OpenAICompatibleClient` from an ``LLMModelConfig`` slot
    (e.g. wash / router). Returns ``None`` if the API key cannot be resolved (logs a warning).
    """
    try:
        api_key = _resolve_slot_api_key(settings, model_cfg)
    except ValueError as e:
        logger.warning("[Bootstrap] %s (slot=%s)", e, provider_name)
        return None
    b = (model_cfg.base_url or "").strip()
    m = (model_cfg.model or "").strip()
    if not b or not m:
        logger.warning(
            "[Bootstrap] Missing base_url or model (slot=%s); client disabled.",
            provider_name,
        )
        return None
    if not api_key:
        return None
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=b,
        model=m,
        temperature=float(model_cfg.temperature),
        timeout_seconds=float(model_cfg.timeout_seconds),
        provider_name=provider_name,
    )


def build_openai_client(settings: ChimeraConfig) -> OpenAICompatibleClient:
    """
    从全局配置构建 Working Client（批处理脚本使用）。

    支持多 Provider 密钥解析（与 ``_resolve_slot_api_key`` / ``build_openai_client_from_model_config`` 相同）。
    ``base_url`` / ``model`` 使用 ``ChimeraConfig`` 的默认访问器，以便在 working 槽位留空
    时仍与 ``default_llm_base_url`` 的 OpenAI 兼容回退一致。
    """
    working = settings.llm.working
    api_key = _resolve_slot_api_key(settings, working)
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=settings.default_llm_base_url,
        model=settings.default_llm_model,
        temperature=float(working.temperature),
        timeout_seconds=settings.default_llm_timeout_seconds,
        provider_name="Working",
    )


def build_openai_client_from_params(
    *,
    api_key: str | SecretStr | None,
    base_url: str | None,
    model: str | None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    default_settings: ChimeraConfig | None = None,
) -> OpenAICompatibleClient:
    """
    Build client for per-request overrides (e.g. Oligo). Falls back to default_settings for None fields.
    When ``temperature`` is None, uses ``llm.working.temperature`` from config (or 0.7 if no settings).

    若请求体 ``api_key`` 为空：先按 ``base_url`` 推断供应商并尝试对应 ``OPENAI_API_KEY`` /
    ``DEEPSEEK_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY``；仍为空时再使用
    ``_resolve_slot_api_key`` 解析 ``llm.working``（含槽位 ``api_key`` 与历史 OpenAI 兜底）。
    """
    s = default_settings
    if s is None:
        s = get_config()
    effective_base = (base_url or "").strip() or s.default_llm_base_url

    resolved_key: str | None = None
    if api_key is not None and str(api_key).strip():
        resolved_key = (
            api_key.get_secret_value().strip()
            if isinstance(api_key, SecretStr)
            else str(api_key).strip()
        )

    if not resolved_key:
        resolved_key = _fallback_api_key_from_base_url(s, effective_base)

    if not resolved_key:
        try:
            resolved_key = _resolve_slot_api_key(s, s.llm.working)
        except ValueError:
            resolved_key = None

    if not resolved_key:
        logger.warning(
            "[Bootstrap] No API key resolved for Oligo/per-request client (base_url=%r)",
            effective_base,
        )
        raise ValueError("api_key is required for OpenAICompatibleClient.")
    resolved_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(s.default_llm_timeout_seconds)
    )
    if temperature is not None:
        resolved_temperature = float(temperature)
    else:
        resolved_temperature = float(s.llm.working.temperature)
    return OpenAICompatibleClient(
        api_key=resolved_key,
        base_url=effective_base,
        model=model if model else s.default_llm_model,
        temperature=resolved_temperature,
        timeout_seconds=resolved_timeout,
    )

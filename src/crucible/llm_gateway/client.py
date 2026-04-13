"""Unified structured-output client for OpenAI-compatible APIs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Type

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, OpenAI
from pydantic import BaseModel, SecretStr, ValidationError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.crucible.core.config import Settings, load_config
from src.crucible.llm_gateway.janitor import clean_json_output

logger = logging.getLogger(__name__)


def _secret_to_str(value: str | SecretStr | None) -> str | None:
    """Convert plain or secret string into plain text value."""
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    if isinstance(value, str):
        return value.strip()
    return None


def _resolve_api_key_from_env(settings: Settings) -> str:
    """
    Resolve API key from environment-backed SecretStr fields only (never from YAML
    dict plumbing). Uses OPENAI_API_KEY only.
    """
    if settings.OPENAI_API_KEY is not None:
        plain = settings.OPENAI_API_KEY.get_secret_value().strip()
        if plain:
            return plain
    raise ValueError(
        "OPENAI_API_KEY not found in environment variables or .env file."
    )


def _detect_api_key_source(
    settings: Settings,
    explicit_api_key: str | None,
) -> str:
    """Return API key source for startup diagnostics."""
    if explicit_api_key:
        return "explicit"
    if settings.OPENAI_API_KEY is not None and settings.OPENAI_API_KEY.get_secret_value().strip():
        return "project_dotenv"
    return "unavailable"


def _log_before_retry(state: RetryCallState) -> None:
    """Emit warning logs before each retry attempt."""
    if state.outcome is None:
        return
    exc = state.outcome.exception()
    if exc is None:
        return
    logger.warning(
        "Structured generation failed at attempt %s/%s; retrying due to %s: %s",
        state.attempt_number,
        3,
        type(exc).__name__,
        exc,
    )


def _log_final_failure(
    exc: Exception, provider_name: str, model: str, response_model: Type[BaseModel]
) -> None:
    """Log the terminal failure after retry exhaustion."""
    logger.error(
        "%s structured generation failed after retries for model=%s, response_model=%s: %s",
        provider_name,
        model,
        response_model.__name__,
        exc,
        exc_info=True,
    )


class OpenAICompatibleClient:
    """Generic OpenAI-compatible client with structured JSON response parsing."""

    def __init__(
        self,
        api_key: str | SecretStr | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        provider_name: str = "OpenAI-compatible",
    ) -> None:
        settings = load_config()
        explicit_api_key = _secret_to_str(api_key)
        resolved_api_key = explicit_api_key or _resolve_api_key_from_env(
            settings=settings,
        )

        resolved_timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(settings.default_llm_timeout_seconds)
        )

        self.provider_name = provider_name
        self.model = (
            model if model is not None else settings.default_llm_model
        )
        resolved_base_url = (
            base_url if base_url is not None else settings.default_llm_base_url
        )
        api_key_source = _detect_api_key_source(settings, explicit_api_key)
        logger.info(
            "%s client initialized | api_key_source=%s | model=%s | base_url=%s",
            provider_name,
            api_key_source,
            self.model,
            resolved_base_url,
        )
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=resolved_timeout,
        )
        self._async_client = AsyncOpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=resolved_timeout,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(
            (
                json.JSONDecodeError,
                ValidationError,
                APITimeoutError,
                APIConnectionError,
                APIError,
                TimeoutError,
                ConnectionError,
            )
        ),
        before_sleep=_log_before_retry,
        reraise=True,
    )
    def _generate_structured_data_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """Call provider with JSON mode and validate response via Pydantic model."""
        if "json" not in system_prompt.lower():
            system_prompt += "\nOutput MUST be valid JSON."
            
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0.01,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        if not response.choices or response.choices[0].message.content is None:
            raise RuntimeError(
                f"{self.provider_name} API returned empty message content. "
                f"Response object: {response!r}"
            )

        raw_text = response.choices[0].message.content
        cleaned_text = clean_json_output(raw_text)
        json.loads(cleaned_text)
        return response_model.model_validate_json(cleaned_text)

    def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """Call provider with JSON mode and validate response via Pydantic model."""
        try:
            return self._generate_structured_data_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
            )
        except (
            json.JSONDecodeError,
            ValidationError,
            APITimeoutError,
            APIConnectionError,
            APIError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            _log_final_failure(
                exc=exc,
                provider_name=self.provider_name,
                model=self.model,
                response_model=response_model,
            )
            raise

    async def _generate_structured_data_once_async(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """Single async attempt: JSON mode + Pydantic validate (no retry)."""
        sp = system_prompt
        if "json" not in sp.lower():
            sp = f"{sp}\nOutput MUST be valid JSON."
        response = await self._async_client.chat.completions.create(
            model=self.model,
            temperature=0.01,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sp},
                {"role": "user", "content": user_prompt},
            ],
        )
        if not response.choices or response.choices[0].message.content is None:
            raise RuntimeError(
                f"{self.provider_name} API returned empty message content. "
                f"Response object: {response!r}"
            )
        raw_text = response.choices[0].message.content
        cleaned_text = clean_json_output(raw_text)
        json.loads(cleaned_text)
        return response_model.model_validate_json(cleaned_text)

    async def generate_structured_data_async(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """
        Async structured generation with retries (for concurrent Optics / Lens calls).
        """
        last_exc: Exception | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                return await self._generate_structured_data_once_async(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                )
            except (
                json.JSONDecodeError,
                ValidationError,
                APITimeoutError,
                APIConnectionError,
                APIError,
                TimeoutError,
                ConnectionError,
                RuntimeError,
            ) as exc:
                last_exc = exc
                if attempt + 1 < max_attempts:
                    wait_s = 1.0 * (2**attempt)
                    logger.warning(
                        "Async structured generation attempt %s/%s failed (%s): %s; retrying in %.1fs",
                        attempt + 1,
                        max_attempts,
                        type(exc).__name__,
                        exc,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                continue
        assert last_exc is not None
        _log_final_failure(
            exc=last_exc,
            provider_name=self.provider_name,
            model=self.model,
            response_model=response_model,
        )
        raise last_exc

    async def generate_raw_text(self, messages: list[dict[str, str]]) -> str:
        """
        非流式请求：发送消息列表，返回完整回答。
        用于 ReAct 闭门思考阶段，避免流式截断带来的状态机灾难。
        """
        response = await self._async_client.chat.completions.create(
            model=self.model,
            temperature=0.7,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        )
        if not response.choices or response.choices[0].message.content is None:
            raise RuntimeError(
                f"{self.provider_name} API returned empty message content. "
                f"Response: {response!r}"
            )
        return response.choices[0].message.content

    async def stream_generate(
        self, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        """
        流式请求：发送消息列表，逐个 yield 文本片段。
        用于 ReAct 终极推流阶段，提供打字机体验。
        """
        stream = await self._async_client.chat.completions.create(
            model=self.model,
            temperature=0.7,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


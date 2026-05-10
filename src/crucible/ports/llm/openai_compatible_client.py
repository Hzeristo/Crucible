"""Unified structured-output client for OpenAI-compatible APIs.

``timeout_seconds=None`` resolves to ``ChimeraConfig.default_llm_timeout_seconds`` via ``get_config()``.
Prefer passing an explicit timeout from ``bootstrap.build_*`` when config is already loaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Type

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.crucible.ports.llm.json_janitor import clean_json_output

logger = logging.getLogger(__name__)


class LLMRawTextTimeoutError(RuntimeError):
    """Raised when ``generate_raw_text`` exceeds ``wait_for(..., timeout=timeout_seconds)``."""


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, (json.JSONDecodeError, ValidationError, APITimeoutError, APIConnectionError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, APIError):
        s = getattr(exc, "status_code", None)
        return isinstance(s, int) and s >= 500
    return False


def _log_before_retry(state: RetryCallState) -> None:
    if state.outcome is None:
        return
    exc = state.outcome.exception()
    if exc is None:
        return
    logger.warning(
        "[LLM] Structured generation failed at attempt %s/%s; retrying due to %s: %s",
        state.attempt_number,
        3,
        type(exc).__name__,
        exc,
    )


def _log_final_failure(
    exc: Exception, provider_name: str, model: str, response_model: Type[BaseModel]
) -> None:
    logger.error(
        "[LLM] %s structured generation failed after retries for model=%s, response_model=%s: %s",
        provider_name,
        model,
        response_model.__name__,
        exc,
        exc_info=True,
    )


class OpenAICompatibleClient:
    """
    OpenAI-compatible LLM client.

    Implements: LLMClient Protocol
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float | None = None,
        temperature: float = 0.7,
        structured_temperature: float = 0.01,
        provider_name: str = "OpenAI-compatible",
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be non-empty (pass from get_config() / bootstrap).")
        if timeout_seconds is None:
            from src.crucible.core.config import get_config

            timeout_seconds = float(get_config().default_llm_timeout_seconds)
        else:
            timeout_seconds = float(timeout_seconds)
        self.temperature = float(temperature)
        self.structured_temperature = float(structured_temperature)
        self.provider_name = provider_name
        self.model = model
        self._resolved_base_url = base_url
        logger.info(
            "[LLM] %s client initialized | model=%s | base_url=%s",
            provider_name,
            self.model,
            self._resolved_base_url,
        )
        self.timeout_seconds = timeout_seconds
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._async_client = AsyncOpenAI(
            api_key=api_key.strip(),
            base_url=base_url,
            timeout=timeout_seconds,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception(is_transient_error),
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
        sp = system_prompt
        if "json" not in sp.lower():
            sp = f"{sp}\nOutput MUST be valid JSON."

        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.structured_temperature,
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

    def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
    ) -> BaseModel:
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
        sp = system_prompt
        if "json" not in sp.lower():
            sp = f"{sp}\nOutput MUST be valid JSON."
        response = await self._async_client.chat.completions.create(
            model=self.model,
            temperature=self.structured_temperature,
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
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                return await self._generate_structured_data_once_async(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                )
            except Exception as exc:
                last_exc = exc
                if not is_transient_error(exc):
                    _log_final_failure(
                        exc=exc,
                        provider_name=self.provider_name,
                        model=self.model,
                        response_model=response_model,
                    )
                    raise
                if attempt + 1 < max_attempts:
                    wait_s = 1.0 * (2**attempt)
                    logger.warning(
                        "[LLM] Async structured generation attempt %s/%s failed (%s): %s; retrying in %.1fs",
                        attempt + 1,
                        max_attempts,
                        type(exc).__name__,
                        exc,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                _log_final_failure(
                    exc=exc,
                    provider_name=self.provider_name,
                    model=self.model,
                    response_model=response_model,
                )
                raise

    async def generate_raw_text(self, messages: list[dict[str, str]]) -> str:
        try:
            response = await asyncio.wait_for(
                self._async_client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=[
                        {"role": m["role"], "content": m["content"]} for m in messages
                    ],
                ),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise LLMRawTextTimeoutError(
                f"[LLM Timeout]: The model did not respond within {self.timeout_seconds}s."
            ) from None
        if not response.choices or response.choices[0].message.content is None:
            raise RuntimeError(
                f"{self.provider_name} API returned empty message content. "
                f"Response: {response!r}"
            )
        return response.choices[0].message.content

    async def stream_generate(
        self, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        try:
            stream = await asyncio.wait_for(
                self._async_client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=[
                        {"role": m["role"], "content": m["content"]} for m in messages
                    ],
                    stream=True,
                ),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[LLM] %s stream create timed out after %ss",
                self.provider_name,
                self.timeout_seconds,
            )
            return

        ait = stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    ait.__anext__(),
                    timeout=self.timeout_seconds,
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning(
                    "[LLM] %s stream idle timeout after %ss (no chunk)",
                    self.provider_name,
                    self.timeout_seconds,
                )
                break
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

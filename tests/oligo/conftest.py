"""Pytest fixtures and mocks for oligo tests."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient


class MockLLMClient:
    """Mock LLM client that simulates OpenAICompatibleClient for testing.

    Supports both sync and async structured generation with configurable responses.
    """

    def __init__(
        self,
        *,
        response_model: type[BaseModel] | None = None,
        responses: list[BaseModel] | None = None,
        raw_responses: list[str] | None = None,
        raise_exc: Exception | None = None,
        provider_name: str = "Mock",
        model: str = "mock-model",
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self._response_model = response_model
        self._responses = responses or []
        self._raw_responses = raw_responses or []
        self._raise_exc = raise_exc
        self._call_count = 0
        self._async_call_count = 0

        # Track calls for assertions
        self.calls: list[dict[str, Any]] = []
        self.async_calls: list[dict[str, Any]] = []

    def _build_response(self, response_model: type[BaseModel]) -> BaseModel:
        if self._responses:
            return self._responses.pop(0)
        if self._raise_exc:
            raise self._raise_exc
        return response_model.model_validate({})

    def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        self._call_count += 1
        call_record = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_model": response_model,
        }
        self.calls.append(call_record)

        if self._raise_exc:
            raise self._raise_exc
        if self._responses:
            return self._responses.pop(0)
        return response_model.model_validate({})

    async def generate_structured_data_async(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        self._async_call_count += 1
        call_record = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_model": response_model,
        }
        self.async_calls.append(call_record)

        if self._raise_exc:
            raise self._raise_exc
        if self._responses:
            return self._responses.pop(0)
        return response_model.model_validate({})

    async def generate_raw_text(self, messages: list[dict[str, str]]) -> str:
        self._async_call_count += 1
        self.async_calls.append({"messages": messages})
        if self._raise_exc:
            raise self._raise_exc
        if self._raw_responses:
            return self._raw_responses.pop(0)
        return "mock raw text response"

    async def stream_generate(
        self, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        self._async_call_count += 1
        self.async_calls.append({"messages": messages})
        if self._raise_exc:
            raise self._raise_exc
        if self._raw_responses:
            response = self._raw_responses.pop(0)
        else:
            response = "mock streamed response"
        for chunk in response.split():
            yield chunk


@pytest.fixture
def mock_llm_client() -> MockLLMClient:
    """Provide a fresh MockLLMClient for each test."""
    return MockLLMClient()


@pytest.fixture
def mock_llm_client_with_responses() -> MockLLMClient:
    """Provide a MockLLMClient pre-configured with an empty response."""
    return MockLLMClient()


@pytest.fixture
def mock_llm_client_raising() -> MockLLMClient:
    """Provide a MockLLMClient that raises an exception on calls."""
    return MockLLMClient(raise_exc=RuntimeError("Mock LLM error"))


# ---------------------------------------------------------------------------
# Fixture helpers for common response models
# ---------------------------------------------------------------------------

class DummyResponse(BaseModel):
    """Simple response model for basic tests."""
    status: str = "ok"
    message: str = ""


@pytest.fixture
def dummy_response_model() -> type[DummyResponse]:
    """Provide the DummyResponse model class."""
    return DummyResponse

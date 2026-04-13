"""LLM 网关：大模型连接、JSON 清洗、Prompt 模板管理."""

from src.crucible.llm_gateway.client import OpenAICompatibleClient
from src.crucible.llm_gateway.janitor import clean_json_output
from src.crucible.llm_gateway.prompt_manager import PromptManager

__all__: list[str] = ["OpenAICompatibleClient", "PromptManager", "clean_json_output"]

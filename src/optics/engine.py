"""OpticsEngine：YAML 透镜列表 + 并发结构化 LLM 折射 → ``DeepReadAtlas``。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Final

from openai import APIConnectionError, APIError, APITimeoutError
from pydantic import BaseModel, ValidationError

from src.crucible.llm_gateway.client import OpenAICompatibleClient

from .schema import (
    ConsensusAndBottlenecks,
    DeepReadAtlas,
    EvalRigorExtraction,
    LensConfig,
    MathArchExtraction,
    MemoryPhysicsExtraction,
    StructuralGaps,
    TaxonomyExtraction,
)

logger = logging.getLogger(__name__)

SCHEMA_REGISTRY: Final[dict[str, type[BaseModel]]] = {
    "MathArchExtraction": MathArchExtraction,
    "EvalRigorExtraction": EvalRigorExtraction,
    "MemoryPhysicsExtraction": MemoryPhysicsExtraction,
    "TaxonomyExtraction": TaxonomyExtraction,
    "ConsensusAndBottlenecks": ConsensusAndBottlenecks,
    "StructuralGaps": StructuralGaps,
}

ATLAS_FIELD_BY_SCHEMA: Final[dict[str, str]] = {
    "MathArchExtraction": "math_arch",
    "EvalRigorExtraction": "eval_rigor",
    "MemoryPhysicsExtraction": "memory_physics",
    "TaxonomyExtraction": "taxonomy",
    "ConsensusAndBottlenecks": "consensus_bottlenecks",
    "StructuralGaps": "structural_gaps",
}

_RED = "\033[31m"
_RESET = "\033[0m"


class OpticsEngine:
    """并发执行多个 Lens，将结构化结果装入 ``DeepReadAtlas``。"""

    def __init__(
        self,
        llm_client: OpenAICompatibleClient,
        lenses: list[LensConfig],
    ) -> None:
        self._llm = llm_client
        self.lenses = list(lenses)

    def _log_optics_failure(self, lens: LensConfig, exc: BaseException) -> None:
        logger.error(
            "%s[Optics Failure]%s lens_id=%s schema=%s: %s",
            _RED,
            _RESET,
            lens.id,
            lens.output_schema_name,
            exc,
        )

    async def _run_single_lens(
        self,
        lens: LensConfig,
        paper_chunks: str,
    ) -> tuple[str, str, BaseModel | None, BaseException | None]:
        """
        返回 ``(lens_id, output_schema_name, model_or_none, error_or_none)``。
        不向外抛异常，供 ``gather`` 并行调度。
        """
        name = lens.output_schema_name
        model_cls = SCHEMA_REGISTRY.get(name)
        if model_cls is None:
            err = ValueError(f"Unknown output_schema_name (not in SCHEMA_REGISTRY): {name}")
            self._log_optics_failure(lens, err)
            return (lens.id, name, None, err)
        try:
            out = await self._llm.generate_structured_data_async(
                system_prompt=lens.system_prompt,
                user_prompt=paper_chunks,
                response_model=model_cls,
            )
            return (lens.id, name, out, None)
        except (ValidationError, APIError, APIConnectionError, APITimeoutError) as exc:
            self._log_optics_failure(lens, exc)
            return (lens.id, name, None, exc)
        except Exception as exc:  # noqa: BLE001
            self._log_optics_failure(lens, exc)
            return (lens.id, name, None, exc)

    async def irradiate(
        self,
        paper_chunks: str,
        metadata: Mapping[str, Any],
    ) -> DeepReadAtlas:
        """
        并发调用全部 Lens；失败项在 Atlas 中对应字段为 ``None``，不阻断其他透镜。

        ``metadata`` 须至少包含 ``arxiv_id``、``short_moniker``；可选 ``title``、``is_survey``。
        """
        try:
            arxiv_id = str(metadata["arxiv_id"]).strip()
            short_moniker = str(metadata["short_moniker"]).strip()
        except KeyError as exc:
            raise ValueError("metadata must include 'arxiv_id' and 'short_moniker'") from exc
        if not arxiv_id or not short_moniker:
            raise ValueError("metadata 'arxiv_id' and 'short_moniker' must be non-empty strings.")

        title_raw = metadata.get("title")
        title = str(title_raw).strip() if title_raw is not None else None
        if title == "":
            title = None

        is_survey = bool(metadata.get("is_survey", False))

        tasks = [self._run_single_lens(lens, paper_chunks) for lens in self.lenses]
        rows = await asyncio.gather(*tasks)

        atlas_kwargs: dict[str, Any] = {
            "arxiv_id": arxiv_id,
            "short_moniker": short_moniker[:64],
            "title": title,
            "is_survey": is_survey,
            "math_arch": None,
            "eval_rigor": None,
            "memory_physics": None,
            "taxonomy": None,
            "consensus_bottlenecks": None,
            "structural_gaps": None,
        }

        for _lid, schema_name, model_obj, err in rows:
            if err is not None or model_obj is None:
                continue
            field = ATLAS_FIELD_BY_SCHEMA.get(schema_name)
            if field is None:
                logger.error(
                    "%s[Optics Failure]%s schema=%s has no atlas field mapping",
                    _RED,
                    _RESET,
                    schema_name,
                )
                continue
            atlas_kwargs[field] = model_obj

        return DeepReadAtlas.model_validate(atlas_kwargs)

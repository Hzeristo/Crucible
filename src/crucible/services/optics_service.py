"""Optics: lens irradiation + run_lens CLI orchestration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from openai import APIConnectionError, APIError, APITimeoutError
from pydantic import BaseModel, ValidationError

from src.crucible.core.config import Settings
from src.crucible.core.naming import expected_stem
from src.crucible.core.schemas import (
    ConsensusAndBottlenecks,
    DeepReadAtlas,
    EvalRigorExtraction,
    LensConfig,
    MathArchExtraction,
    MemoryPhysicsExtraction,
    StructuralGaps,
    TaxonomyExtraction,
)
from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient
from src.crucible.ports.papers.paper_loader import PaperLoader
from src.crucible.ports.vault.vault_note_writer import VaultNoteWriter
from src.crucible.ports.vault.vault_read_adapter import VaultReadAdapter
from src.crucible.services.optics_lens_registry import load_lens_configs, load_survey_lens_configs

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

_FILTERED_VERDICT_DIRS: tuple[str, ...] = ("Must_Read", "Skim", "Reject")


def resolve_filtered_fulltext_markdown(
    settings: Settings,
    arxiv_id: str,
    short_moniker: str,
) -> Path | None:
    root = settings.paper_miner_or_default.filtered_dir
    if not root.is_dir():
        return None

    stem = expected_stem(arxiv_id, short_moniker)
    for sub in _FILTERED_VERDICT_DIRS:
        hit = root / sub / f"{stem}.md"
        if hit.is_file():
            return hit.resolve()

    candidates = sorted(
        p.resolve()
        for p in root.rglob("*.md")
        if p.is_file() and arxiv_id in p.name
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    exact = [p for p in candidates if p.stem == stem]
    if len(exact) == 1:
        return exact[0]
    pool = exact if exact else candidates

    def _rank(p: Path) -> tuple[int, int]:
        parent = p.parent.name
        try:
            tier = _FILTERED_VERDICT_DIRS.index(parent)
        except ValueError:
            tier = len(_FILTERED_VERDICT_DIRS)
        return (tier, -p.stat().st_size)

    return sorted(pool, key=_rank)[0]


class OpticsService:
    """Concurrent lens calls + deep-read write."""

    def __init__(
        self,
        settings: Settings,
        llm_client: OpenAICompatibleClient,
        vault_writer: VaultNoteWriter,
    ) -> None:
        self._settings = settings
        self._llm = llm_client
        self._vault_writer = vault_writer

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
        lenses: list[LensConfig],
    ) -> DeepReadAtlas:
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

        tasks = [self._run_single_lens(lens, paper_chunks) for lens in lenses]
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

    async def run_lens_cli(
        self,
        arxiv_id: str,
        *,
        survey: bool,
        vault: VaultReadAdapter,
        paper_loader: PaperLoader,
    ) -> tuple[int, str | None]:
        """
        Returns (exit_code, atlas_path_str_or_none).
        """
        located = vault.find_authenticated_paper(arxiv_id)
        if located is None:
            return (1, None)

        _triage_note_path, short_moniker, _pdf_path = located

        md_path = resolve_filtered_fulltext_markdown(
            self._settings, arxiv_id, short_moniker
        )
        if md_path is None:
            return (6, None)

        paper = paper_loader.load_clean_md(md_path)

        lenses = (
            load_survey_lens_configs(self._settings)
            if survey
            else load_lens_configs(self._settings)
        )

        try:
            atlas = await self.irradiate(
                paper.raw_text,
                metadata={
                    "arxiv_id": arxiv_id,
                    "short_moniker": short_moniker,
                    "title": paper.title,
                    "is_survey": survey,
                },
                lenses=lenses,
            )
        except ValueError:
            return (2, None)
        except (APIConnectionError, APITimeoutError, APIError):
            return (3, None)
        except Exception:
            logger.exception("Irradiate failed")
            return (4, None)

        note_asset_basename = f"{arxiv_id}-{short_moniker}"
        try:
            atlas_path = self._vault_writer.write_deep_read_node(
                paper,
                atlas,
                note_asset_basename=note_asset_basename,
            )
        except OSError:
            return (5, None)

        return (0, str(atlas_path.resolve()))

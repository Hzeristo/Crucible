"""Load Lens YAML from ``lenses_dir``; persist built-in defaults when missing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from src.crucible.core.config import Settings
from src.crucible.core.schemas import LensConfig

logger = logging.getLogger(__name__)

_DEFAULT_FILE_STEM = "default_lenses"

_NO_CODE_FENCES_FOR_CODE = (
    "For `pseudo_code` and each string in `core_equations`: DO NOT wrap your output in markdown "
    "code blocks (```). Just output the raw text / LaTeX inside the JSON string values."
)

_NARRATIVE_BULLETS = (
    "For long narrative fields like `architecture_narrative`, `ablation_target`, or "
    "`mechanics_deep_dive`: DO NOT output a single wall of text! You MUST break your answer into "
    "2–3 logical bullet points, each starting with a **Bolded Key Concept** (markdown bold), "
    "followed by explanation."
)

_JSON_MATH = (
    _NO_CODE_FENCES_FOR_CODE
    + " "
    + _NARRATIVE_BULLETS
    + ' Output ONLY one JSON object with keys: "architecture_narrative" (string), '
    '"core_equations" (array of strings: pure LaTeX per element, no ``` fences), '
    '"pseudo_code" (string: Python-style pseudocode, no ``` fences), '
    '"architecture_type" (array of short tags). '
    "No markdown fences around the whole JSON, no text outside JSON."
)

_JSON_EVAL = (
    _NARRATIVE_BULLETS
    + ' Output ONLY one JSON object with keys: "baselines", "datasets", "metrics_used" '
    '(each array of strings), "ablation_target" (string: savage critique of empirical setup and '
    "ablations—how they validate or weaken claims; use the bullet + **Bolded Key** structure above; "
    'empty string only if the paper has no ablation discussion). '
    "No markdown fences around the whole JSON, no text outside JSON."
)

_JSON_MEM = (
    _NARRATIVE_BULLETS
    + ' Output ONLY one JSON object with keys: "mechanics_deep_dive" (string: memory mechanics), '
    '"forgetting_mechanism" (string: prose description; empty string if not discussed), '
    '"context_window_tricks" (string: one cohesive descriptive paragraph about context-window and '
    "state tricks—not a list of tags). "
    "No markdown fences around the whole JSON, no text outside JSON."
)

_JSON_TAX = (
    'Output ONLY one JSON object with keys: "classification_axes" (array of short strings), '
    '"core_categories" (object: keys are category names, values are strings). '
    "Each value MUST be 1–3 sentences: how this survey explicitly defines or implements that "
    "category for LLMs/Agents, including architectural bounds that separate it from other categories "
    "(not a table of contents). Axes stay abstract slicing dimensions, not model names. "
    "No markdown fences, no text outside JSON."
)

_JSON_CONS = (
    'Output ONLY one JSON object with keys: "major_limitations" (array of strings). '
    "Each string must be a single dense sentence of at least ~20 Chinese characters or ~80 English "
    "characters summarizing a widely agreed bottleneck. No markdown fences, no text outside JSON."
)

_JSON_GAP = (
    'Output ONLY one JSON object with keys: "future_directions" (array of objects). '
    'Each object has "direction" (short name) and "technical_void" (string): the concrete '
    "architectural or theoretical limitation that keeps the problem open—why it is still unsolved "
    "(e.g. quadratic graph-update cost), not a buzzword or section heading alone. "
    "No markdown fences, no text outside JSON."
)


def _builtin_lens_configs() -> list[LensConfig]:
    return [
        LensConfig(
            id="math_arch",
            output_schema_name="MathArchExtraction",
            description="反编译式数学与流水线伪代码抽取。",
            system_prompt=(
                "You are a ruthless code decompiler. Ignore filler text. Extract mathematical "
                "formulations (in pure LaTeX) and summarize the pipeline in Python pseudocode. "
                "List the high-level architecture types.\n"
                "You MUST provide a comprehensive, multi-paragraph narrative explaining the design "
                "philosophy and data flow of the architecture, in addition to the pseudo-code.\n\n"
                + _JSON_MATH
            ),
        ),
        LensConfig(
            id="eval_rigor",
            output_schema_name="EvalRigorExtraction",
            description="对抗式审稿：数据集、基线、指标与消融靶点。",
            system_prompt=(
                "You are an adversarial reviewer. Pinpoint exactly which datasets and baselines "
                "are used and what metrics they rely on.\n"
                "Do not just list metrics. Give me a savage, detailed critique of their empirical "
                "setup. Explain EXACTLY how their ablation study validates or weakens their claims.\n\n"
                + _JSON_EVAL
            ),
        ),
        LensConfig(
            id="memory_physics",
            output_schema_name="MemoryPhysicsExtraction",
            description="上下文边界与状态管理物理。",
            system_prompt=(
                "You focus exclusively on context bounds and state management.\n"
                "Put the soul of your answer in `mechanics_deep_dive` using the mandated "
                "2–3 bullet lines with **Bolded Key** lead-ins. `forgetting_mechanism` and "
                "`context_window_tricks` must each be readable prose strings (paragraph-style), "
                "not sparse labels or tag lists.\n\n"
                + _JSON_MEM
            ),
        ),
    ]


def _builtin_survey_lens_configs() -> list[LensConfig]:
    return [
        LensConfig(
            id="survey_taxonomy",
            output_schema_name="TaxonomyExtraction",
            description="抽象维度与领域类别，而非模型枚举。",
            system_prompt=(
                "You are dissecting a survey paper. Do not give a sterile table of contents. "
                "Ignore model lists and bibliographies. For every category you place in "
                "`core_categories`, you MUST state exactly what architectural bounds this paper "
                "uses to distinguish it from the others (e.g. episodic memory implemented as "
                "timestamped vector–JSON chunks retrieved via Top-K semantic search vs external "
                "memory as a tool-call KV store). Short labels live in keys; the technical story "
                "lives in values.\n\n"
                + _JSON_TAX
            ),
        ),
        LensConfig(
            id="survey_consensus",
            output_schema_name="ConsensusAndBottlenecks",
            description="跨工作共识的深层缺陷与硬瓶颈。",
            system_prompt=(
                "Extract only the deeply agreed-upon flaws and hard bottlenecks of current memory "
                "or agent architectures (e.g. retrieval limits, context collapse, stale memory, "
                "evaluation gaps). Each bullet must be a dense, non-trivial limitation, not a "
                "generic complaint.\n\n"
                + _JSON_CONS
            ),
        ),
        LensConfig(
            id="survey_gaps",
            output_schema_name="StructuralGaps",
            description="作者明确点出的未探索空位。",
            system_prompt=(
                "You are hunting for unsolved research problems in a survey. Do not spit back "
                "Future Work section headings as empty labels. For each gap, pair a short "
                "`direction` with a `technical_void` that names the specific reason the field has "
                "not closed it yet (e.g. O(n^2) graph-update scaling, non-composable evaluation "
                "protocols), not generic 'efficiency' or 'better models'. Ignore vague wish lists.\n\n"
                + _JSON_GAP
            ),
        ),
    ]


def load_survey_lens_configs(settings: Settings) -> list[LensConfig]:
    base: Path = settings.lenses_dir
    yaml_path = base / "survey_lenses.yaml"
    yml_path = base / "survey_lenses.yml"
    json_path = base / "survey_lenses.json"

    for candidate in (yaml_path, yml_path, json_path):
        try:
            parsed = _try_read_config_file(candidate)
        except (OSError, yaml.YAMLError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Survey lens config unreadable at %s: %s", candidate, exc)
            continue
        if parsed is not None:
            return parsed

    lenses = _builtin_survey_lens_configs()
    try:
        _persist_lens_yaml(yaml_path, lenses)
    except OSError as exc:
        logger.warning("Could not persist survey lens YAML to %s: %s", yaml_path, exc)
    return lenses


def _parse_lens_payload(raw: Any) -> list[LensConfig]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and "lenses" in raw:
        items = raw["lenses"]
    else:
        raise ValueError("Lens file must be a list or an object with a 'lenses' array.")
    if not isinstance(items, list):
        raise ValueError("'lenses' must be a list.")
    return [LensConfig.model_validate(x) for x in items]


def _try_read_config_file(path: Path) -> list[LensConfig] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(text)
    elif suffix == ".json":
        raw = json.loads(text)
    else:
        return None
    return _parse_lens_payload(raw)


def _persist_lens_yaml(target: Path, lenses: list[LensConfig]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"lenses": [lc.model_dump() for lc in lenses]}
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_default_yaml(target: Path, lenses: list[LensConfig]) -> None:
    _persist_lens_yaml(target, lenses)


def load_lens_configs(settings: Settings) -> list[LensConfig]:
    base: Path = settings.lenses_dir
    yaml_path = base / f"{_DEFAULT_FILE_STEM}.yaml"
    yml_path = base / f"{_DEFAULT_FILE_STEM}.yml"
    json_path = base / f"{_DEFAULT_FILE_STEM}.json"

    for candidate in (yaml_path, yml_path, json_path):
        try:
            parsed = _try_read_config_file(candidate)
        except (OSError, yaml.YAMLError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Lens config unreadable at %s: %s", candidate, exc)
            continue
        if parsed is not None:
            return parsed

    lenses = _builtin_lens_configs()
    try:
        _write_default_yaml(yaml_path, lenses)
    except OSError as exc:
        logger.warning("Could not persist default lens YAML to %s: %s", yaml_path, exc)
    return lenses

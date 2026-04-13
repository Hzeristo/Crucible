"""Optics：Vault 寻址与深读图谱契约（引擎与 Lens 待接入）。"""

from .engine import OpticsEngine, SCHEMA_REGISTRY
from .loader import load_lens_configs, load_survey_lens_configs
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
from .vault_indexer import find_paper_in_vault

__all__ = [
    "ConsensusAndBottlenecks",
    "DeepReadAtlas",
    "EvalRigorExtraction",
    "LensConfig",
    "MathArchExtraction",
    "MemoryPhysicsExtraction",
    "OpticsEngine",
    "SCHEMA_REGISTRY",
    "StructuralGaps",
    "TaxonomyExtraction",
    "find_paper_in_vault",
    "load_lens_configs",
    "load_survey_lens_configs",
]

"""
Crucible 核心：配置与 Pydantic 模型.
"""

from src.crucible.core.config import (
    PaperMinerSettings,
    Settings,
    load_config,
)

__all__: list[str] = ["Settings", "load_config", "PaperMinerSettings"]

"""PaperMiner 业务域核心数据结构。"""

from .paper import Paper, SourceType
from .verdict import VerdictDecision, PaperAnalysisResult

__all__ = [
    "Paper",
    "SourceType",
    "VerdictDecision",
    "PaperAnalysisResult",
]

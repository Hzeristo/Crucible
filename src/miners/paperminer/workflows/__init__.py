"""PaperMiner 工作流：串联各子组件的业务胶水。

各 workflow 按需导入，避免 batch_filter 等依赖 LLM 的模块在 run_ingest 时被加载。
"""

__all__ = [
    "run_batch_filter",
    "run_daily_pipeline",
    "run_collect_paper",
    "run_arxiv_fetch",
    "run_pdf_ingestion",
]


def __getattr__(name: str):
    """Lazy import to avoid loading LLM deps when only ingest/fetch needed."""
    if name == "run_batch_filter":
        from .batch_filter import run_batch_filter
        return run_batch_filter
    if name == "run_daily_pipeline":
        from .chimera_daily import run_daily_pipeline
        return run_daily_pipeline
    if name == "run_collect_paper":
        from .collect_markdown import run_collect_paper
        return run_collect_paper
    if name == "run_arxiv_fetch":
        from .fetch_arxiv import run_arxiv_fetch
        return run_arxiv_fetch
    if name == "run_pdf_ingestion":
        from .ingest_pdfs import run_pdf_ingestion
        return run_pdf_ingestion
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Optics CLI：Vault 寻址 → 源 Markdown → OpticsEngine 辐照 → Deep Read Atlas。"""

# 使用示例:
#   python scripts/run_lens.py -i 2601.06966v1
#   python scripts/run_lens.py -i 2602.06052v3 -s   # 综述 Survey Lenses
#   python scripts/run_lens.py --id 2601.15311v3 -l DEBUG

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import APIConnectionError, APIError, APITimeoutError  # noqa: E402

from src.crucible.core.config import Settings, load_config  # noqa: E402
from src.crucible.llm_gateway.client import OpenAICompatibleClient  # noqa: E402
from src.crucible.llm_gateway.prompt_manager import PromptManager  # noqa: E402
from src.miners.paperminer.io_adapter.paper_loader import PaperLoader  # noqa: E402
from src.miners.paperminer.io_adapter.vault_writer import VaultWriter  # noqa: E402
from src.optics.engine import OpticsEngine  # noqa: E402
from src.optics.loader import load_lens_configs, load_survey_lens_configs  # noqa: E402
from src.optics.vault_indexer import find_paper_in_vault  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Optics：Vault 认证 → 辐照 → 01_Deep_Reads Atlas。",
    )
    p.add_argument(
        "-i",
        "--id",
        required=True,
        help="arXiv ID（须已在 Vault inbox + assets 完成配对认证）。",
    )
    p.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别。",
    )
    p.add_argument(
        "-s",
        "--survey",
        action="store_true",
        help="综述模式：仅加载 Survey Lenses（分类拓扑 / 共识瓶颈 / 结构空白），不跑实验型三刀。",
    )
    return p


def _configure_logging(level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger("optics.run")


# 与 ``file_router.PaperRouter.route_and_cleanup`` 归档目录一致（Verdict.value → 文件夹名）
_FILTERED_VERDICT_DIRS: tuple[str, ...] = ("Must_Read", "Skim", "Reject")


def _resolve_filtered_fulltext_markdown(
    settings: Settings,
    arxiv_id: str,
    short_moniker: str,
) -> Path | None:
    """
    仅在 ``papers/.../filtered`` 官方热区下游检索全文 Markdown（初筛 pipeline 归档产物）。

    1) 按判决子目录顺序命中 ``{arxiv_id}-{short_moniker}.md``；
    2) 否则在 ``filtered_dir`` 内递归匹配文件名含 ``arxiv_id`` 的 ``*.md``（单命中直接采用；
       多命中时优先精确 stem，再按判决目录优先级，再大文件）。
    """
    root = settings.paper_miner_or_default.filtered_dir
    if not root.is_dir():
        return None

    expected_stem = f"{arxiv_id}-{short_moniker}"
    for sub in _FILTERED_VERDICT_DIRS:
        hit = root / sub / f"{expected_stem}.md"
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

    exact = [p for p in candidates if p.stem == expected_stem]
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


async def _async_main(args: argparse.Namespace, log: logging.Logger) -> int:
    settings = load_config()
    settings.ensure_directories()

    located = find_paper_in_vault(args.id, settings)
    if located is None:
        log.error(
            "[Fatal] Target %s not triaged or assets missing. The Obsidian Ledger denies existence.",
            args.id,
        )
        return 1

    triage_note_path, short_moniker, pdf_path = located
    log.info(
        "Vault authentication passed | id=%s | note=%s | pdf=%s",
        args.id,
        triage_note_path,
        pdf_path,
    )

    md_path = _resolve_filtered_fulltext_markdown(settings, args.id, short_moniker)
    if md_path is None:
        log.error(
            "[Fatal] Source full-text Markdown not found in 'filtered' archive. "
            "Run ingestion again or provide physical path. Optics engine aborted."
        )
        return 6

    log.info("Filtered full-text markdown: %s", md_path)

    loader = PaperLoader()
    paper = loader.load_clean_md(md_path)

    lenses = load_survey_lens_configs(settings) if args.survey else load_lens_configs(settings)
    client = OpenAICompatibleClient(
        api_key=settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
        base_url=settings.default_llm_base_url,
        model=settings.default_llm_model,
        timeout_seconds=settings.default_llm_timeout_seconds,
    )
    engine = OpticsEngine(client, lenses)

    try:
        atlas = await engine.irradiate(
            paper.raw_text,
            metadata={
                "arxiv_id": args.id,
                "short_moniker": short_moniker,
                "title": paper.title,
                "is_survey": args.survey,
            },
        )
    except ValueError as e:
        log.error("Irradiate aborted: %s", e)
        return 2
    except (APIConnectionError, APITimeoutError, APIError) as e:
        log.error("LLM transport/API failure: %s", e)
        return 3
    except Exception as e:  # noqa: BLE001
        log.error("Irradiate failed: %s", e, exc_info=True)
        return 4

    note_asset_basename = f"{args.id}-{short_moniker}"
    try:
        vault_writer = VaultWriter(settings, PromptManager())
        atlas_path = vault_writer.write_deep_read_node(
            paper,
            atlas,
            note_asset_basename=note_asset_basename,
        )
    except OSError as e:
        log.error("Atlas write failed: %s", e)
        return 5

    print(
        f"\033[32m[✔] Optics matrix irradiation complete. Atlas forged.\033[0m\n"
        f"    {atlas_path.resolve()}"
    )
    log.info("Deep read atlas written: %s", atlas_path)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    log = _configure_logging(args.log_level)
    try:
        return asyncio.run(_async_main(args, log))
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

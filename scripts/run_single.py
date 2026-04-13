"""单文件靶向管线：PDF → MinerU → Paper → Filter → Vault → PaperRouter 归档至 papers/filtered（与 batch_filter 一致）。"""

# 使用示例:
#   python scripts/run_single.py -p papers/arxivpdf/2602.06052v3.pdf
#   python scripts/run_single.py -p papers/wild_arxivpdf/2410.10813v2.pdf -o papers/md_papers_raw
#   python scripts/run_single.py -m papers/md_papers/2401.00001.md
#   python scripts/run_single.py -m ./note.md --force -l DEBUG

from __future__ import annotations


import argparse
import logging
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.crucible.core.config import load_config  # noqa: E402
from src.crucible.llm_gateway.client import OpenAICompatibleClient  # noqa: E402
from src.crucible.llm_gateway.prompt_manager import PromptManager  # noqa: E402
from src.miners.paperminer.core.verdict import VerdictDecision  # noqa: E402
from src.miners.paperminer.io_adapter.cli_presenter import (  # noqa: E402
    print_success,
    print_triage_banner,
)
from src.miners.paperminer.decision.filter_engine import PaperFilterEngine  # noqa: E402
from src.miners.paperminer.io_adapter.file_router import PaperRouter  # noqa: E402
from src.miners.paperminer.io_adapter.paper_loader import PaperLoader  # noqa: E402
from src.miners.paperminer.io_adapter.vault_writer import VaultWriter  # noqa: E402
from src.miners.paperminer.workflows.ingest_pdfs import ingest_to_papers  # noqa: E402


def _normalize_path(path: Path, project_root: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (project_root / expanded).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单点处理：一个 PDF 或 Markdown → 初筛 →（可选）Vault 知识节点 → 与日报相同的 filtered/ 入库与审计。",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "-p",
        "--pdf",
        type=Path,
        default=None,
        help="目标 PDF（绝对或相对项目根的路径）。",
    )
    src.add_argument(
        "-m",
        "--md",
        type=Path,
        default=None,
        help="已转换好的 Markdown；跳过 MinerU。",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="与 -p 联用：MinerU 原始输出根目录（须位于 papers/ 下；默认 papers/md_papers_raw）。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使 Verdict 为 Reject 也写入 Vault。",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别。",
    )
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.out is not None and args.pdf is None:
        parser.error("--out/-o 仅在与 --pdf/-p 一起使用时有效。")

    configure_logging(args.log_level)
    log = logging.getLogger("run_single")

    settings = load_config()
    settings.ensure_directories()
    prompt_manager = PromptManager()
    router = PaperRouter(settings=settings)

    raw_dir_for_cleanup: Path | None = None
    generated_clean_md: Path | None = None
    try:
        if args.pdf is not None:
            try:
                _staged_pdf, raw_dir_for_cleanup, clean_md = ingest_to_papers(
                    pdf_path=args.pdf,
                    settings=settings,
                    raw_output_root=args.out,
                )
                generated_clean_md = clean_md
                log.info(
                    "阶段 1→2 | Papers ingest 完成：raw=%s clean_md=%s",
                    raw_dir_for_cleanup,
                    clean_md,
                )
            except EnvironmentError as exc:
                log.error("MinerU 不可用或未安装：%s", exc)
                return 2
            except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
                log.error("阶段 1 剥皮失败：%s", exc)
                return 3
        else:
            clean_md = _normalize_path(args.md, settings.project_root)
            log.info("阶段 1 | 跳过 MinerU，直接使用 MD：%s", clean_md)

        log.info("阶段 2 | 附魔与装载：PaperLoader.load_paper")
        loader = PaperLoader()
        try:
            paper = loader.load_paper(clean_md)
        except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
            log.error("阶段 2 装载失败：%s", exc)
            return 4

        log.info("阶段 3 | 审判：PaperFilterEngine.evaluate_paper")
        engine = PaperFilterEngine(
            llm_client=OpenAICompatibleClient(
                api_key=settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
                base_url=settings.default_llm_base_url,
                model=settings.default_llm_model,
            ),
            prompt_manager=prompt_manager,
        )
        result = engine.evaluate_paper(paper)

        print_triage_banner(result)

        should_write = result.verdict != VerdictDecision.REJECT or args.force
        if not should_write:
            log.info("阶段 4 | Verdict=Reject 且未使用 --force，跳过 Vault。")
        else:
            log.info("阶段 4 | 落葬：VaultWriter.write_knowledge_node")
            try:
                writer = VaultWriter(settings=settings, prompt_manager=prompt_manager)
                out_path = writer.write_knowledge_node(paper, result)
            except Exception as exc:
                log.error("阶段 4 写入 Vault 失败：%s", exc)
                return 6

            deploy_msg = f"[✔] Knowledge Node deployed at {out_path}"
            log.info(deploy_msg)
            print_success(deploy_msg)

        # 与 ``run_batch_filter`` / 日报管线一致：无论 Verdict，均归档至 ``papers/filtered/{Verdict}/``。
        log.info("阶段 4b | 入库：PaperRouter.route_and_cleanup → papers/filtered")
        try:
            router.route_and_cleanup(paper, result)
        except RuntimeError as exc:
            log.error("阶段 4b 归档失败（filtered / 审计 / PDF）：%s", exc)
            return 7

        archive_msg = (
            f"[✔] Paper archived under papers/filtered/{result.verdict.value.replace(' ', '_')}/"
        )
        log.info(archive_msg)
        print_success(archive_msg)

        return 0

    except KeyboardInterrupt:
        log.warning("用户中断。")
        return 130
    except Exception:
        log.exception("run_single 未预期失败。")
        return 99
    finally:
        if raw_dir_for_cleanup is not None:
            log.info("阶段 5 | Finally 清扫：router.cleanup_playground (MinerU raw under papers/)")
            try:
                router.cleanup_playground(raw_dir_for_cleanup, generated_clean_md)
                clean_msg = "[✔] MinerU raw staging cleaned (finally)."
                log.info(clean_msg)
                print_success(clean_msg)
            except RuntimeError as exc:
                log.warning("Cleanup failed: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())

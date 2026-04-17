"""单文件靶向管线：PDF → MinerU → Paper → Filter → Vault → 归档。"""

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
from src.crucible.services.single_paper_pipeline_service import (  # noqa: E402
    SinglePaperPipelineService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单点处理：一个 PDF 或 Markdown → 初筛 → Vault → filtered/。",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("-p", "--pdf", type=Path, default=None, help="目标 PDF。")
    src.add_argument("-m", "--md", type=Path, default=None, help="已转换好的 Markdown。")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="与 -p 联用：MinerU 原始输出根目录。",
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

    settings = load_config()
    settings.ensure_directories()
    svc = SinglePaperPipelineService(settings)
    return svc.run_single(
        pdf=args.pdf,
        md=args.md,
        raw_output_root=args.out,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())

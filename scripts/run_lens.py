"""Optics CLI：Vault 寻址 → 源 Markdown → Optics 辐照 → Deep Read Atlas。"""

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

from src.crucible.bootstrap import build_openai_client  # noqa: E402
from src.crucible.core.config import get_config  # noqa: E402
from src.crucible.ports.papers.paper_loader import PaperLoader  # noqa: E402
from src.crucible.ports.prompts.jinja_prompt_manager import PromptManager  # noqa: E402
from src.crucible.ports.vault.vault_note_writer import VaultNoteWriter  # noqa: E402
from src.crucible.ports.vault.vault_read_adapter import VaultReadAdapter  # noqa: E402
from src.crucible.services.optics_service import OpticsService  # noqa: E402


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
        help="综述模式：仅加载 Survey Lenses。",
    )
    return p


def _configure_logging(level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("optics.run")


async def _async_main(args: argparse.Namespace, log: logging.Logger) -> int:
    settings = get_config()
    settings.ensure_directories()

    llm = build_openai_client(settings)
    prompts = PromptManager()
    vault = VaultReadAdapter(settings)
    writer = VaultNoteWriter(settings, prompts)
    service = OpticsService(
        settings=settings,
        llm_client=llm,
        vault_writer=writer,
    )
    papers = PaperLoader()

    code, atlas_path = await service.run_lens_cli(
        args.id,
        survey=args.survey,
        vault=vault,
        paper_loader=papers,
    )
    if code == 1:
        log.error(
            "[Fatal] Target %s not triaged or assets missing. The Obsidian Ledger denies existence.",
            args.id,
        )
        return 1
    if code == 6:
        log.error(
            "[Fatal] Source full-text Markdown not found in 'filtered' archive. "
            "Run ingestion again or provide physical path. Optics engine aborted."
        )
        return 6
    if code == 2:
        log.error("Irradiate aborted.")
        return 2
    if code == 3:
        log.error("LLM transport/API failure.")
        return 3
    if code == 4:
        log.error("Irradiate failed.")
        return 4
    if code == 5:
        log.error("Atlas write failed.")
        return 5

    print(
        f"\033[32m[✔] Optics matrix irradiation complete. Atlas forged.\033[0m\n"
        f"    {atlas_path}"
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

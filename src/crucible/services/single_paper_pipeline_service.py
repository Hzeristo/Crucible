"""Single PDF/MD pipeline: ingest → filter → vault → archive."""

from __future__ import annotations

import logging
from pathlib import Path

from src.crucible.bootstrap import build_openai_client
from src.crucible.core.config import Settings
from src.crucible.core.schemas import VerdictDecision
from src.crucible.ports.ingest.mineru_pipeline import ingest_to_papers
from src.crucible.ports.papers.paper_archive_adapter import PaperArchiveAdapter
from src.crucible.ports.papers.paper_loader import PaperLoader
from src.crucible.ports.prompts.jinja_prompt_manager import PromptManager
from src.crucible.ports.vault.vault_note_writer import VaultNoteWriter
from src.crucible.services.filter_service import FilterService

logger = logging.getLogger(__name__)


def normalize_path(path: Path, project_root: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (project_root / expanded).resolve()


class SinglePaperPipelineService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run_single(
        self,
        *,
        pdf: Path | None,
        md: Path | None,
        raw_output_root: Path | None,
        force: bool,
    ) -> int:
        prompt_manager = PromptManager()
        router = PaperArchiveAdapter(settings=self._settings)
        llm = build_openai_client(self._settings)
        engine = FilterService(llm_client=llm, prompt_manager=prompt_manager)

        raw_dir_for_cleanup: Path | None = None
        generated_clean_md: Path | None = None
        try:
            if pdf is not None:
                try:
                    _staged_pdf, raw_dir_for_cleanup, clean_md = ingest_to_papers(
                        pdf_path=pdf,
                        settings=self._settings,
                        raw_output_root=raw_output_root,
                    )
                    generated_clean_md = clean_md
                    logger.info(
                        "阶段 1→2 | Papers ingest 完成：raw=%s clean_md=%s",
                        raw_dir_for_cleanup,
                        clean_md,
                    )
                except EnvironmentError as exc:
                    logger.error("MinerU 不可用或未安装：%s", exc)
                    return 2
                except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
                    logger.error("阶段 1 剥皮失败：%s", exc)
                    return 3
            else:
                assert md is not None
                clean_md = normalize_path(md, self._settings.project_root)
                logger.info("阶段 1 | 跳过 MinerU，直接使用 MD：%s", clean_md)

            logger.info("阶段 2 | 附魔与装载：PaperLoader.load_paper")
            loader = PaperLoader()
            try:
                paper = loader.load_paper(clean_md)
            except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
                logger.error("阶段 2 装载失败：%s", exc)
                return 4

            logger.info("阶段 3 | 审判：FilterService.evaluate_paper")
            result = engine.evaluate_paper(paper)

            from src.crucible.services.cli_presenter import print_triage_banner

            print_triage_banner(result)

            should_write = result.verdict != VerdictDecision.REJECT or force
            if not should_write:
                logger.info("阶段 4 | Verdict=Reject 且未使用 --force，跳过 Vault。")
            else:
                logger.info("阶段 4 | 落葬：VaultNoteWriter.write_knowledge_node")
                try:
                    writer = VaultNoteWriter(
                        settings=self._settings, prompt_manager=prompt_manager
                    )
                    out_path = writer.write_knowledge_node(paper, result)
                except Exception as exc:
                    logger.error("阶段 4 写入 Vault 失败：%s", exc)
                    return 6

                deploy_msg = f"[✔] Knowledge Node deployed at {out_path}"
                logger.info(deploy_msg)
                from src.crucible.services.cli_presenter import print_success

                print_success(deploy_msg)

            logger.info("阶段 4b | 入库：PaperArchiveAdapter.route_and_cleanup")
            try:
                router.route_and_cleanup(paper, result)
            except RuntimeError as exc:
                logger.error("阶段 4b 归档失败（filtered / 审计 / PDF）：%s", exc)
                return 7

            archive_msg = (
                f"[✔] Paper archived under papers/filtered/{result.verdict.value.replace(' ', '_')}/"
            )
            logger.info(archive_msg)
            from src.crucible.services.cli_presenter import print_success

            print_success(archive_msg)

            return 0

        except KeyboardInterrupt:
            logger.warning("用户中断。")
            return 130
        except Exception:
            logger.exception("run_single 未预期失败。")
            return 99
        finally:
            if raw_dir_for_cleanup is not None:
                logger.info(
                    "阶段 5 | Finally 清扫：router.cleanup_playground (MinerU raw under papers/)"
                )
                try:
                    router.cleanup_playground(raw_dir_for_cleanup, generated_clean_md)
                    clean_msg = "[✔] MinerU raw staging cleaned (finally)."
                    logger.info(clean_msg)
                    from src.crucible.services.cli_presenter import print_success

                    print_success(clean_msg)
                except RuntimeError as exc:
                    logger.warning("Cleanup failed: %s", exc)

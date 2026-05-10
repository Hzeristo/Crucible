"""Paper filtering / triage (formerly PaperFilterEngine)."""

from __future__ import annotations

import json
import logging

from src.crucible.core.schemas import Paper, PaperAnalysisResult, VerdictDecision
from src.crucible.ports.llm.openai_compatible_client import OpenAICompatibleClient
from src.crucible.ports.prompts.jinja_prompt_manager import PromptManager

logger = logging.getLogger(__name__)


class FilterService:
    """Evaluate a paper and produce typed analysis results."""

    def __init__(
        self,
        llm_client: OpenAICompatibleClient,
        prompt_manager: PromptManager,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_manager = prompt_manager

    def evaluate_paper(self, paper: Paper) -> PaperAnalysisResult:
        logger.info("[Service] Evaluating payload: %s", paper.title)
        try:
            if len(paper.raw_text.strip()) < 80:
                raise ValueError(
                    f"Paper content is too short for stable evaluation. "
                    f"paper_id={paper.id}, title={paper.title}"
                )

            system_prompt = self.prompt_manager.render("chimera_sys/reviewer_zero.j2")
            schema_dict = PaperAnalysisResult.model_json_schema()
            schema_str = json.dumps(schema_dict, ensure_ascii=False, indent=2)

            user_prompt = self.prompt_manager.render(
                "tasks/filter_task.j2",
                paper=paper,
                json_schema=schema_str,
            )

            result = self.llm_client.generate_structured_data(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=PaperAnalysisResult,
            )

            if isinstance(result, PaperAnalysisResult):
                logger.info(
                    "[Service] Evaluation completed: %s | verdict=%s score=%s",
                    paper.title,
                    result.verdict.value,
                    result.score,
                )
                return result

            validated = PaperAnalysisResult.model_validate(result)

            logger.info(
                "[Service] Evaluation completed: %s | verdict=%s score=%s",
                paper.title,
                validated.verdict.value,
                validated.score,
            )
            return validated
        except Exception as exc:
            logger.exception("[Service] Evaluation failed for paper: %s", paper.title)
            return PaperAnalysisResult(
                verdict=VerdictDecision.REJECT,
                short_moniker="EvalDegraded",
                score=0,
                novelty_delta="N/A: evaluation degraded because analysis failed.",
                mechanism_summary="Insufficient content or unexpected failure during evaluation.",
                critical_flaws=[f"{type(exc).__name__}: {exc}"],
            )

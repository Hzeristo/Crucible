"""Business logic for paper filtering and triage decisions."""

from __future__ import annotations

import json
import logging

from src.crucible.llm_gateway.client import OpenAICompatibleClient
from src.crucible.llm_gateway.prompt_manager import PromptManager

from ..core.paper import Paper
from ..core.verdict import PaperAnalysisResult, VerdictDecision

logger = logging.getLogger(__name__)


def _validate_prompt_boundary(system_prompt: str, user_prompt: str) -> None:
    """Enforce role boundary between system/user prompts before LLM call."""
    if (
        "Reviewer Zero" not in system_prompt
        or "[THE TRIAGE PROTOCOL" not in system_prompt
    ):
        raise ValueError(
            "System prompt contract violated: missing Reviewer Zero role or Triage Protocol."
        )

    user_preamble = user_prompt.split("[PAPER CONTENT START]", maxsplit=1)[0]
    if "[USER PROFILE & RESEARCH STANCE]" not in user_preamble:
        raise ValueError(
            "User prompt contract violated: user profile injection marker not found."
        )

    if "[THE TRIAGE PROTOCOL" in user_preamble:
        raise ValueError(
            "Prompt role boundary violated: Triage Protocol must not appear in user prompt."
        )

    if "you are" in user_preamble.lower():
        raise ValueError(
            "Prompt role boundary violated: avoid role-defining 'You are' in user prompt."
        )


class PaperFilterEngine:
    """Evaluate a paper and produce typed analysis results."""

    def __init__(
        self,
        llm_client: OpenAICompatibleClient | None = None,
        prompt_manager: PromptManager | None = None,
    ) -> None:
        if llm_client is None:
            from src.crucible.core.config import load_config
            settings = load_config()
            llm_client = OpenAICompatibleClient(
                api_key=settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
                base_url=settings.default_llm_base_url,
                model=settings.default_llm_model
            )
        self.llm_client = llm_client
        self.prompt_manager = prompt_manager or PromptManager()

    def evaluate_paper(self, paper: Paper) -> PaperAnalysisResult:
        """Run reviewer prompt + LLM structured parsing for one paper."""
        logger.info("[Crucible Engine] Evaluating payload: %s", paper.title)
        try:
            if len(paper.raw_text.strip()) < 80:
                raise ValueError(
                    f"Paper content is too short for stable evaluation. "
                    f"paper_id={paper.id}, title={paper.title}"
                )

            system_prompt = self.prompt_manager.render("base/reviewer_zero.j2")
            schema_dict = PaperAnalysisResult.model_json_schema()
            schema_str = json.dumps(schema_dict, ensure_ascii=False, indent=2)

            user_prompt = self.prompt_manager.render(
                "tasks/filter_task.j2",
                paper=paper,
                json_schema=schema_str,
            )
            _validate_prompt_boundary(
                system_prompt=system_prompt, user_prompt=user_prompt
            )

            result = self.llm_client.generate_structured_data(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=PaperAnalysisResult,
            )

            if isinstance(result, PaperAnalysisResult):
                logger.info(
                    "[Crucible Engine] Evaluation completed: %s | verdict=%s score=%s",
                    paper.title,
                    result.verdict.value,
                    result.score,
                )
                return result

            validated = PaperAnalysisResult.model_validate(result)

            logger.info(
                "[Crucible Engine] Evaluation completed: %s | verdict=%s score=%s",
                paper.title,
                validated.verdict.value,
                validated.score,
            )
            return validated
        except Exception as exc:
            logger.exception("Evaluation failed for paper: %s", paper.title)
            return PaperAnalysisResult(
                verdict=VerdictDecision.REJECT,
                short_moniker="EvalDegraded",
                score=0,
                novelty_delta="N/A: evaluation degraded because analysis failed.",
                mechanism_summary="Insufficient content or unexpected failure during evaluation.",
                critical_flaws=[f"{type(exc).__name__}: {exc}"],
            )

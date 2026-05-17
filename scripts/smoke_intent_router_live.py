"""Optional live Router: same agent stack + real Working LLM (API key from ChimeraConfig).

Unstable (model-dependent). For release gate, run when keys are configured::

    python scripts/smoke_intent_router_live.py

Exit codes: 0 = stream shows both read_vault_file and search_vault tool traces;
1 = ran but did not observe both tools; 2 = could not build client (skip).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crucible.bootstrap import build_openai_client_from_model_config  # noqa: E402
from src.crucible.core.config import get_config  # noqa: E402
from src.oligo.core.agent import ChimeraAgent  # noqa: E402

logger = logging.getLogger(__name__)

USER = (
    "First call read_vault_file with an empty path \"\". "
    "After you see the tool failed, call search_vault with query \"live-smoke\"."
)


async def main() -> int:
    settings = get_config()
    try:
        client = build_openai_client_from_model_config(
            settings, settings.llm.working, provider_name="Working"
        )
    except Exception as exc:
        logger.warning("[SmokeLive] skip: %s", exc)
        return 2
    if client is None:
        logger.warning("[SmokeLive] skip: no working client")
        return 2

    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": USER}],
        system_core="You are a concise assistant; follow the user's tool sequence.",
        skill_override=None,
        llm_client=client,
        router_client=client,
        max_turns=6,
        allowed_tools=None,
    )
    blob: list[str] = []
    async for chunk in agent.run_theater():
        blob.append(chunk)
    text = "".join(blob)
    has_rf = "read_vault_file" in text
    has_sv = "search_vault" in text
    if has_rf and has_sv:
        logger.info("[SmokeLive] ok: saw both tools in stream")
        return 0
    logger.error("[SmokeLive] expected both tools in stream (rf=%s sv=%s)", has_rf, has_sv)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(asyncio.run(main()))

"""Manual / CI smoke: tool failure → Router sees reason + hint; SSE tool telemetry."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.oligo.core.agent import ChimeraAgent  # noqa: E402

logger = logging.getLogger(__name__)


class _SmokeIntentMockLLM:
    """Router probes + Final buffer; records snapshots. Probe 2 only fires if failure hint is visible."""

    def __init__(self) -> None:
        self.router_snapshots: list[list[dict[str, Any]]] = []
        self.final_text: str = "Smoke IR.5 final (no real LLM)."
        self._probe_index: int = 0

    async def generate_raw_text(self, messages: list[dict[str, Any]]) -> str:
        sys0 = (messages[0].get("content") or "") if messages else ""
        if "Chimera OS local router" in sys0:
            self.router_snapshots.append([dict(x) for x in messages])
            self._probe_index += 1
            if self._probe_index == 1:
                return '<CMD:read_vault_file({"path": ""})>'
            blob = "\n".join(
                str(m.get("content") or "") for m in messages
            )
            if self._probe_index == 2:
                if "Some tools failed." not in blob:
                    raise RuntimeError(
                        "[SmokeIR] probe 2 expected IR.2 failure hint in context; abort."
                    )
                return '<CMD:search_vault({"query": "ir5-smoke-degrade"})>'
            return "<PASS>"
        return self.final_text


def _joined_roles(snapshot: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{m.get('role', '')}: {m.get('content', '') or ''}" for m in snapshot
    )


async def main() -> int:
    """
    E2E agent loop (non-pytest): real tool execution, SSE bb-tool-* , multi-turn Router.

    1) Probe 1: read_vault_file with empty path → ARGS_INVALID + failure hint.
    2) Probe 2: only emitted if context contains ``Some tools failed.`` → **different**
       tool ``search_vault`` (graceful switch after reflection text).
    3) Probe 3: ``<PASS>`` → Final.
    Frontend 0.1s timer: run Astrocyte + Oligo on a real invoke; see docs III.B.3 终审.
    """
    mock = _SmokeIntentMockLLM()
    agent = ChimeraAgent(
        raw_messages=[
            {
                "role": "user",
                "content": "Find anything about smoke IR.5 intent recognition.",
            }
        ],
        system_core="You are BB (smoke).",
        skill_override=None,
        llm_client=mock,
        max_turns=5,
        allowed_tools=None,
    )

    chunks: list[str] = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    blob = "".join(chunks)
    if "event: bb-tool-start" not in blob:
        logger.error("[SmokeIR] missing bb-tool-start in stream")
        return 1
    if "event: bb-tool-done" not in blob:
        logger.error("[SmokeIR] missing bb-tool-done in stream")
        return 1
    if blob.count("event: bb-tool-done") < 2:
        logger.error("[SmokeIR] expected >=2 tool-done events (two tool turns)")
        return 1
    if '"started_at_ms"' not in blob:
        logger.error("[SmokeIR] bb-tool-start payload missing started_at_ms")
        return 1

    if len(mock.router_snapshots) < 3:
        logger.error("[SmokeIR] expected >=3 router probes, got %s", len(mock.router_snapshots))
        return 1

    second = _joined_roles(mock.router_snapshots[1])
    if "[SYSTEM TOOL RESULTS]" not in second:
        logger.error("[SmokeIR] second probe missing SYSTEM TOOL RESULTS")
        return 1
    if 'reason="ARGS_INVALID"' not in second and "ARGS_INVALID" not in second:
        logger.error("[SmokeIR] second probe missing ARGS_INVALID reason")
        return 1
    if "Some tools failed." not in second:
        logger.error("[SmokeIR] second probe missing failure reflection hint")
        return 1

    third = _joined_roles(mock.router_snapshots[2])
    if "search_vault" not in third:
        logger.error("[SmokeIR] probe 3 context should include second tool (search_vault) turn")
        return 1

    logger.info("[SmokeIR] ok: hint-conditioned tool switch + two bb-tool cycles.")
    logger.info(
        "[SmokeIR] UI timer: run Astrocyte against Oligo and watch ActiveToolTelemetry "
        "during a real tool call."
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    raise SystemExit(asyncio.run(main()))

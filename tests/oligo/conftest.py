# crucible_core/tests/oligo/conftest.py
from __future__ import annotations

from typing import Any


class MockLLMClient:
    """Records calls and returns configurable responses.

    Distinguishes router calls (first message contains "Chimera OS local router")
    from final calls to support testing the two-phase ReAct loop.
    """

    def __init__(
        self,
        probe_response: str = "",
        final_response: str = "Final answer.",
    ):
        self.calls: list[list[dict[str, Any]]] = []
        self.probe_response = probe_response
        self.final_response = final_response
        self.probe_call_count = 0
        self.final_call_count = 0

    async def generate_raw_text(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append(list(messages))
        sys_content = (messages[0].get("content", "") or "") if messages else ""
        if "Chimera OS local router" in sys_content:
            self.probe_call_count += 1
            return self.probe_response
        self.final_call_count += 1
        return self.final_response


# Factory — call mock_client() per test for fresh instance and test isolation.
def mock_client() -> MockLLMClient:
    return MockLLMClient()

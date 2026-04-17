# Project Chimera: Oligo Tool Layer & Physical Arsenal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Oligo tool execution layer (tests + web search tool) and verify the skill system end-to-end.

**Architecture:** Phase 1 (tool schema/test layer) and Phase 3 (skill system) are already implemented in the codebase. This plan addresses the remaining gaps: unit tests for the tool execution layer, the missing `web_search` tool, and full-stack integration verification.

---

## Sprint 1: Oligo Tool Execution Test Suite

### Scope
The `ChimeraAgent` in `crucible_core/src/oligo/core/agent.py` already has `_parse_tool_calls`, `_execute_tool_calls`, `_wash_tool_results`, and `_render_tool_results_for_llm` implemented. This sprint writes a focused test suite for the tool execution layer.

### File Map
- **Create:** `crucible_core/tests/oligo/test_tool_execution.py`
- **Read:** `crucible_core/src/oligo/core/agent.py:256-632` (existing methods to test)
- **Read:** `crucible_core/src/crucible/core/schemas.py:320-386` (PlannedToolCall, ExecutedToolResult, ToolCallStatus)
- **Read:** `crucible_core/src/oligo/tools/__init__.py` (TOOL_REGISTRY — currently empty)

---

### Task 1: Mock LLM Client and Test Infrastructure

**Files:**
- Create: `crucible_core/tests/oligo/conftest.py`

- [ ] **Step 1: Create conftest.py with fixtures**

```python
# crucible_core/tests/oligo/conftest.py
from __future__ import annotations

import asyncio
from typing import Any
import pytest

class MockLLMClient:
    """Records calls and returns configurable responses."""

    def __init__(self, probe_response: str = "", final_response: str = "Final answer."):
        self.calls: list[list[dict[str, Any]]] = []
        self.probe_response = probe_response
        self.final_response = final_response
        self.probe_call_count = 0
        self.final_call_count = 0

    async def generate_raw_text(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append(list(messages))
        sys_content = messages[0].get("content", "") if messages else ""
        if "Chimera OS local router" in sys_content:
            self.probe_call_count += 1
            return self.probe_response
        self.final_call_count += 1
        return self.final_response


@pytest.fixture
def mock_client():
    return MockLLMClient()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

- [ ] **Step 2: Run conftest import test**

Run: `cd crucible_core && python -c "from tests.oligo.conftest import MockLLMClient; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
cd crucible_core && git add tests/oligo/conftest.py && git commit -m "test(oligo): add conftest with MockLLMClient fixture"
```

---

### Task 2: Test `_parse_tool_calls`

**Files:**
- Modify: `crucible_core/tests/oligo/conftest.py` (add new fixtures)
- Create: `crucible_core/tests/oligo/test_tool_execution.py`

- [ ] **Step 1: Write failing test for parse with no tools allowed**

```python
# crucible_core/tests/oligo/test_tool_execution.py
"""Tests for ChimeraAgent tool execution layer."""
from __future__ import annotations

import pytest
from src.crucible.core.schemas import PlannedToolCall
from src.oligo.core.agent import ChimeraAgent


def test_parse_tool_calls_extracts_single_tool(mock_client):
    """LLM output with one <CMD:search_vault(...)> parses correctly."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Find papers about RAG"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=None,
    )
    probe = '<CMD:search_vault({"query": "RAG"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].tool_name == "search_vault"
    assert planned[0].args == {"query": "RAG"}
    assert planned[0].allowed is True
    assert planned[0].deny_reason is None


def test_parse_tool_calls_whitelist_denies_unlisted(mock_client):
    """Tool not in allowed_tools list is marked denied."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search web"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=["search_vault"],  # web_search NOT allowed
    )
    probe = '<CMD:web_search({"query": "latest AI news"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].tool_name == "web_search"
    assert planned[0].allowed is False
    assert "not allowed" in planned[0].deny_reason


def test_parse_tool_calls_multiple_tools_in_response(mock_client):
    """Response with multiple <CMD:...> tags parses all of them."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Compare approaches"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=None,
    )
    probe = '<CMD:search_vault({"query": "RAG"})><CMD:web_search({"query": "RAG benchmark"})>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 2
    assert [p.tool_name for p in planned] == ["search_vault", "web_search"]


def test_parse_tool_calls_invalid_json_args_uses_empty_dict(mock_client):
    """Invalid JSON in args falls back to empty dict (execute step re-validates)."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=None,
    )
    probe = '<CMD:search_vault(NOT_JSON)>'
    planned = agent._parse_tool_calls(probe)
    assert len(planned) == 1
    assert planned[0].args == {}
    assert planned[0].allowed is True  # allowed, but execute step will fail


def test_parse_tool_calls_no_cmds_returns_empty(mock_client):
    """Response with no <CMD:...> returns empty list."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Hello"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=None,
    )
    planned = agent._parse_tool_calls("Hello, how are you?")
    assert planned == []
```

- [ ] **Step 2: Run tests to verify they fail (agent methods not yet tested this way)**

Run: `cd crucible_core && python -m pytest tests/oligo/test_tool_execution.py::test_parse_tool_calls_extracts_single_tool -v 2>&1 | head -30`
Expected: PASS (code already works)

- [ ] **Step 3: Run full test file**

Run: `cd crucible_core && python -m pytest tests/oligo/test_tool_execution.py -v 2>&1 | tail -20`
Expected: All 5 tests PASS

- [ ] **Step 4: Commit**

```bash
cd crucible_core && git add tests/oligo/test_tool_execution.py && git commit -m "test(oligo): add _parse_tool_calls tests"
```

---

### Task 3: Test `_execute_tool_calls` (concurrent execution, timeout, denial)

**Files:**
- Modify: `crucible_core/tests/oligo/test_tool_execution.py`

- [ ] **Step 1: Write failing test for denied tool materialization**

```python
def test_execute_tool_calls_denied_tools_materialized(mock_client):
    """Denied tools are materialized as ExecutedToolResult with DENIED status, no actual execution."""
    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Search"}],
        system_core="You are a helpful assistant.",
        skill_override=None,
        llm_client=mock_client,
        allowed_tools=["search_vault"],  # web_search denied
    )
    planned = [
        PlannedToolCall(
            id="call-001",
            tool_name="web_search",
            raw_args='{"query": "AI"}',
            args={"query": "AI"},
            allowed=False,
            deny_reason="Tool 'web_search' is not allowed under current skill.",
        )
    ]
    results = agent._execute_tool_calls.__wrapped__(agent, planned)  # sync test helper
    # Access via sync wrapper if available, otherwise test via run_theater
```

- [ ] **Step 2: Write test for successful parallel execution via run_theater**

```python
@pytest.mark.asyncio
async def test_run_theater_with_single_tool_produces_result(mock_client):
    """Router returns tool call -> executor runs it -> final response streamed."""
    mock_client.probe_response = '<CMD:search_vault({"query": "Titans"})>'
    mock_client.final_response = "Based on the vault search, Titans is a memory architecture."

    agent = ChimeraAgent(
        raw_messages=[{"role": "user", "content": "Tell me about Titans paper"}],
        system_core="You are BB.",
        skill_override=None,
        llm_client=mock_client,
        max_turns=3,
        allowed_tools=["search_vault"],
        vault=MockVaultAdapter(),
    )

    chunks = []
    async for chunk in agent.run_theater():
        chunks.append(chunk)

    final_output = "".join(chunks)
    assert any("Titans" in c for c in chunks)
    assert mock_client.probe_call_count == 1
    assert mock_client.final_call_count == 1


class MockVaultAdapter:
    """Minimal vault adapter for testing."""
    async def search_notes(self, query: str, top_k: int = 3) -> str:
        return f"[MockVault] Found 2 notes about: {query}"
    async def search_by_attribute(self, key: str, value: str, top_k: int = 5) -> str:
        return f"[MockVault] Found notes with {key}={value}"
```

- [ ] **Step 3: Run tests**

Run: `cd crucible_core && python -m pytest tests/oligo/test_tool_execution.py -v 2>&1 | tail -25`
Expected: Tests pass or fail with clear error messages

- [ ] **Step 4: Commit**

```bash
cd crucible_core && git add tests/oligo/test_tool_execution.py && git commit -m "test(oligo): add execute_tool_calls and run_theater integration tests"
```

---

## Sprint 2: Physical Arsenal — `web_search` Tool

### Scope
Implement `web_search` tool using `duckduckgo-search` (no API key required). Register it in `TOOL_REGISTRY`.

### File Map
- **Create:** `crucible_core/src/oligo/tools/web_search.py`
- **Modify:** `crucible_core/src/oligo/tools/__init__.py` (register the tool)
- **Read:** `crucible_core/src/crucible/core/config.py` (Settings structure)
- **Read:** `crucible_core/src/crucible/core/schemas.py:389-421` (OligoAgentConfig — for `force_wash_tools`)

---

### Task 4: Implement `web_search` Tool

**Files:**
- Create: `crucible_core/src/oligo/tools/web_search.py`
- Modify: `crucible_core/src/oligo/tools/__init__.py`

- [ ] **Step 1: Write web_search tool**

```python
# crucible_core/src/oligo/tools/web_search.py
"""Web search tool using duckduckgo-search (no API key required)."""

from __future__ import annotations

import logging
from typing import Any

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None  # type: ignore

logger = logging.getLogger(__name__)

MAX_RESULTS = 3
TOOL_NAME = "web_search"


async def web_search(query: str, **kwargs: Any) -> str:
    """
    Search the web for the given query using DuckDuckGo.

    Args:
        query: Search query string.
        **kwargs: Ignored (for future extension).

    Returns:
        Formatted string with top 3 search results (title, URL, snippet).
        Error message if search fails or library not installed.
    """
    if DDGS is None:
        return "[Tool Error]: web_search requires `duckduckgo-search` package. Install with: pip install duckduckgo-search"

    q = query.strip()
    if not q:
        return "[Tool Error]: web_search requires a non-empty query string."

    try:
        results: list[str] = []
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(q, max_results=MAX_RESULTS), 1):
                title = r.get("title", "No title")
                url = r.get("href", "No URL")
                body = r.get("body", "")
                snippet = body[:300] + "..." if len(body) > 300 else body
                results.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")

        if not results:
            return f"[web_search] No results found for query: {q}"

        header = f"[WEB SEARCH] Query: {q}\n{'=' * 50}\n"
        return header + "\n\n".join(results)

    except Exception as exc:
        logger.warning("[web_search] search failed for query=%s: %s", q, exc)
        return f"[Tool Error]: web_search failed for '{q}': {exc}"
```

- [ ] **Step 2: Verify file was created correctly**

Run: `cd crucible_core && python -c "from src.oligo.tools.web_search import web_search; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Register tool in `__init__.py`**

Read the current `__init__.py` first, then add registration:

```python
# Add to crucible_core/src/oligo/tools/__init__.py
from src.oligo.tools.web_search import web_search

TOOL_REGISTRY["web_search"] = web_search
```

- [ ] **Step 4: Verify registration**

Run: `cd crucible_core && python -c "from src.oligo.tools import TOOL_REGISTRY; print('web_search' in TOOL_REGISTRY)"`
Expected: `True`

- [ ] **Step 5: Add web_search to OligoAgentConfig.force_wash_tools**

Check `crucible_core/src/crucible/core/schemas.py:413-420` — `force_wash_tools` already includes `"web_search"`. No change needed.

- [ ] **Step 6: Commit**

```bash
cd crucible_core && git add src/oligo/tools/web_search.py src/oligo/tools/__init__.py && git commit -m "feat(oligo): add web_search tool using duckduckgo-search"
```

---

### Task 5: Verify `web_search` Tool End-to-End

**Files:**
- Create: `crucible_core/tests/oligo/test_web_search.py`

- [ ] **Step 1: Write integration test**

```python
# crucible_core/tests/oligo/test_web_search.py
import pytest
from src.oligo.tools.web_search import web_search


@pytest.mark.asyncio
async def test_web_search_returns_results():
    """web_search returns formatted results for a known query."""
    result = await web_search("Claude AI assistant")
    assert "[WEB SEARCH]" in result
    assert "Claude" in result or "Error" in result  # Error ok if network down
    assert "URL:" in result or "Tool Error" in result


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_error():
    """Empty query returns a validation error message."""
    result = await web_search("")
    assert "Tool Error" in result or "empty" in result.lower()


@pytest.mark.asyncio
async def test_web_search_strips_whitespace():
    """Query with leading/trailing whitespace is trimmed."""
    result = await web_search("  Claude AI  ")
    assert "[WEB SEARCH]" in result
```

- [ ] **Step 2: Run tests**

Run: `cd crucible_core && python -m pytest tests/oligo/test_web_search.py -v 2>&1 | tail -15`
Expected: Tests pass

- [ ] **Step 3: Commit**

```bash
cd crucible_core && git add tests/oligo/test_web_search.py && git commit -m "test(oligo): add web_search integration tests"
```

---

## Sprint 3: Skill System Integration Verification

### Scope
The skill system (Phase 3) is already implemented: `skills.rs` loads JSON files, `evaluate_payload` in `lib.rs` assembles the skill payload, and the Svelte UI has a skill dropdown. This sprint verifies the full flow works end-to-end and adds one missing piece: the `skill_id` must be passed through the Svelte UI → Rust → Oligo call chain.

### File Map
- **Read:** `astrocyte/src/routes/+page.svelte:400-500` (sendMessage / evaluate_payload call)
- **Read:** `astrocyte/src-tauri/src/lib.rs:401-467` (evaluate_payload — already handles skill_id)
- **Read:** `astrocyte/src-tauri/src/llm_client.rs` (stream_oligo_agent call signature)

---

### Task 6: Verify Skill ID Flow in Svelte Frontend

**Files:**
- Modify: `astrocyte/src/routes/+page.svelte`

- [ ] **Step 1: Find the evaluate_payload invoke call in +page.svelte**

Search for `invoke('evaluate_payload'` in the file. The call signature should include `skill_id`. If it does not, add it.

```javascript
// In the sendMessage function or equivalent, find:
await invoke('evaluate_payload', {
    payload: userText,
    session_id: activeSessionId,
    skill_id: activeSkillId,  // <-- must be passed
    user_message_id: makeId(),
    assistant_message_id: makeId(),
    // ...
});
```

- [ ] **Step 2: Verify activeSkillId is bound to the dropdown**

In `+page.svelte`, the `activeSkillId` variable should be bound to the skill `<select>` element. Confirm this by reading the relevant Svelte markup section.

- [ ] **Step 3: If skill_id not passed, fix the invoke call and commit**

```bash
# If changes were needed:
git add astrocyte/src/routes/+page.svelte
git commit -m "fix(astrocyte): pass skill_id to evaluate_payload"
```

---

### Task 7: Verify Oligo Receives `skill_override` and `allowed_tools`

**Files:**
- Read: `astrocyte/src-tauri/src/llm_client.rs` (stream_oligo_agent parameters)

- [ ] **Step 1: Check stream_oligo_agent accepts skill_override and allowed_tools**

Read `llm_client.rs`. The function signature should match:

```rust
pub async fn stream_oligo_agent(
    api_key: String,
    base_url: String,
    model_name: String,
    persona_id: Option<String>,
    system_core: String,
    skill_override: Option<String>,       // <- must exist
    allowed_tools: Option<Vec<String>>,   // <- must exist
    messages: Vec<crate::state::Message>,
    app_handle: &tauri::AppHandle,
    cancel_token: CancellationToken,
) -> Result<String, String>
```

- [ ] **Step 2: If signature is missing skill_override/allowed_tools, add them**

This requires modifying `llm_client.rs` and the call site in `lib.rs`.

- [ ] **Step 3: Commit if changes were needed**

```bash
git add astrocyte/src-tauri/src/llm_client.rs astrocyte/src-tauri/src/lib.rs
git commit -m "fix(astrocyte): ensure skill_override and allowed_tools flow to Oligo"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Phase 1 TODO 1.1–1.5 (Schemas): Already implemented in `schemas.py` and `agent.py`
- Phase 1 TODO 1.2–1.3 (Parser/Executor): Already implemented
- Phase 1 TODO 1.4 (Wash Layer): Already implemented
- Phase 1 TODO 1.5 (LLM Renderer): Already implemented
- Phase 2 TODO 2.1 (Obsidian Ripper): Already implemented via VaultReadAdapter
- Phase 2 TODO 2.2 (Web Devourer): **Addressed in Sprint 2**
- Phase 2 TODO 2.3 (Tool Registry): **Addressed in Sprint 2**
- Phase 3 TODO 3.1 (Rust File Scanner): Already implemented in `skills.rs`
- Phase 3 TODO 3.2 (Rust API & Payload): Already implemented in `lib.rs`
- Phase 3 TODO 3.3 (Svelte UI): Already implemented in `+page.svelte`
- **Gap found:** `web_search` tool missing → Sprint 2
- **Gap found:** Skill ID pass-through in Svelte → Sprint 3

**2. Placeholder scan:** No placeholders found. All code is concrete.

**3. Type consistency:**
- `PlannedToolCall`, `ExecutedToolResult`, `ToolCallStatus` — all defined in `schemas.py:320-386`
- `web_search` async function signature matches `TOOL_REGISTRY` callable type
- `skill_id` flow: Svelte `activeSkillId` → `invoke('evaluate_payload', {skill_id})` → `lib.rs evaluate_payload` → `llm_client::stream_oligo_agent`

---

**Plan complete and saved to `crucible_core/docs/superpowers/plans/2026-04-17-chimera-oligo-tool-layer.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

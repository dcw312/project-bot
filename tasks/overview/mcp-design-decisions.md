# MCP Migration — Assumptions and Design Decisions

**Date:** 2026-04-05
**Branch:** main
**Relates to:** `tasks/overview/mcp-migration-plan.md`

This document records every non-obvious choice made during the migration from the `lfm2`/`/api/generate` architecture to the `qwen3.5:9b`/`/api/chat` tool-calling architecture. It is intended as a reference when things behave unexpectedly or when the design needs to evolve.

---

## 1. "MCP" means Ollama function calling, not the Anthropic MCP protocol

The architecture review uses "MCP (Model Context Protocol)" as shorthand for the pattern of giving the model on-demand data access via tools. In practice the implementation uses **Ollama's native `/api/chat` tool calling**, which follows the OpenAI function-calling wire format.

This is not the Anthropic MCP protocol (which uses JSON-RPC over stdio/SSE and requires a separate MCP server process). Choosing Ollama's built-in tool calling keeps the stack simple: no extra process, no new dependencies, same Ollama API.

**What this means going forward:** If the model is ever swapped for one accessed via the Anthropic API or a cloud endpoint that supports real MCP, the `chat()` loop would need to be rewritten. The tool handler interface (`dict[str, callable]`) is abstract enough that the service layer would not change.

---

## 2. Tool handlers are passed as callbacks, not imported

`ollama_client.py` does not import from `services.py`. Instead, `process_message()` builds a dict of closures bound to the current user and passes it into `ollama_client.chat()`.

```python
# services.py
tool_handlers = {
    "get_projects": lambda **_: mcp_get_projects(user),
    "get_tasks": lambda project_name, **_: mcp_get_tasks(user, project_name),
    "search_tasks": lambda query, **_: mcp_search_tasks(user, query),
}
raw_response, elapsed_ms = ollama_client.chat(message_text, tool_handlers)
```

**Why:** `services.py` already imports `ollama_client`. A reverse import would create a circular dependency. The callback approach also makes `ollama_client` independently testable — tests pass stub handlers without touching Django ORM.

**Assumption:** The model will only request tools that are in `tool_handlers`. Requesting anything else raises `ToolCallError`. There is no dynamic tool registration; the three data tools are the complete set.

---

## 3. Tool results must be JSON strings (not dicts)

Ollama's `/api/chat` protocol requires the `content` field in a `tool` role message to be a **string**. All three `mcp_*` functions return `json.dumps(...)` rather than raw Python dicts.

This is a protocol-level requirement, not a style choice. Passing a dict would cause Ollama to reject or silently mishandle the message. `tests_mcp.MCPToolFunctionTests.test_mcp_tool_results_are_strings` enforces this.

---

## 4. Only read-only tools — actions still flow through the JSON response

The model fetches data via tools but **executes** actions through the existing JSON response format (`{"action": ..., "data": ..., "message": ...}`). The `handle_llm_action()` dispatcher in `services.py` is untouched.

**Why not make create/update/delete into tools as well?**

- Keeps the action surface auditable: every write goes through one well-tested code path.
- The existing `parse_llm_response()` → `handle_llm_action()` pipeline already handles errors, `PermissionError`, and `ValueError` consistently. Duplicating that in individual tool handlers would be error-prone.
- Data-fetch tools are idempotent and safe to retry; write tools would not be.

This may evolve in a future iteration if the model is upgraded to something where a richer agentic loop (multi-step plan-and-act) is desirable.

---

## 5. The `**_` catch-all in tool handler lambdas

```python
"get_projects": lambda **_: mcp_get_projects(user),
```

The `**_` discards any keyword arguments the model passes. This guards against the model sending unexpected extra parameters (e.g. `get_projects(user_id=123)`) that would otherwise cause a `TypeError` inside the lambda.

**Assumption:** Extra arguments from the model are benign noise to be silently discarded. If a future tool requires strict argument validation, replace the lambda with a named function that validates explicitly.

---

## 6. Project name matching uses `icontains` + shortest-name disambiguation

`mcp_get_tasks` resolves project names with `name__icontains` (case-insensitive contains) rather than an exact match. When multiple projects match, the one with the shortest name is selected.

```python
project = min(projects, key=lambda p: len(p.name))
```

**Why:** The model may not reproduce the exact project name from memory. `icontains` allows "website" to match "Website Redesign". Shortest-name disambiguation is a simple heuristic: "Website" is more specific than "Website Redesign Tasks Archive".

**Risk:** If two projects have similarly short names that both contain the query string (e.g. "Work" and "Work 2"), the wrong one may be selected silently. A better long-term fix would be to return all matches and let the model or the user clarify. Accepted as a known limitation for v1.

---

## 7. `build_context_summary()` is retained but no longer called

`build_context_summary()` is left in `services.py` after the migration. It is not called from `process_message()` but has not been deleted.

**Why:** Keeping it allows a one-line revert of Step 4 (`context_summary = build_context_summary(user)`) if `qwen3.5:9b` proves unreliable. It also means any code paths that reference it (e.g. a potential admin debug view) do not break.

**When to delete it:** After the migration has been running in production without rollback for a reasonable period. At that point, remove `build_context_summary()` and its callers in a clean-up commit.

---

## 8. `elapsed_ms` now covers the full tool-call loop

In the previous architecture, `elapsed_ms` measured a single HTTP request to `/api/generate`. After the migration it measures the **total wall time** from before the first `/api/chat` request to after the final one, including all tool round-trips.

**Why this is the right measure:** The Discord user waits for the full loop, not just the first request. Recording total latency gives meaningful data about user experience.

**Implication for `MessageLog`:** `elapsed_ms` values will be higher than before for queries that require tool calls (typically 2–4× the single-call time). This is expected, not a regression.

---

## 9. No loop iteration limit

The `while True` loop in `chat()` has no explicit cap on the number of tool-call iterations. The only safeguard against infinite loops is the 120-second per-request HTTP timeout.

**Why accepted:** In practice, the model converges in 1–3 iterations. An infinite loop would require the model to repeatedly call tools without ever producing a final response — pathological behaviour that the timeout will eventually catch.

**If this becomes a problem:** Add a counter and raise `ToolCallError` after `MAX_ITERATIONS` (e.g. 10). This is a one-line change to `chat()`.

---

## 10. System prompt changes: no `## Current context` section

The `qwen3.5` system prompt removes the `## Current context` placeholder that `lfm2` used. Because context is now delivered via tool results, there is nothing to inject into the system prompt at message time.

The prompt also adds explicit guidance on when *not* to call tools:
```
For update_task and add_task where the user supplies an explicit task ID or
project name, you do not need to call tools first.
```

**Assumption:** `qwen3.5:9b` will follow this guidance and avoid unnecessary tool calls for simple explicit-reference requests ("mark task 5 as done"). If it does not — if it always calls `get_tasks` before every update — latency will be higher than necessary but correctness will be unaffected. The `tests_llm.py` suite will reveal this empirically once a live Ollama instance is available.

---

## 11. `validate_ollama_startup()` is unchanged

The startup check (`GET /api/tags`, model presence check, system prompt load) continues to work without modification because `PROMPT_DIR` is derived from `OLLAMA_MODEL`:

```python
PROMPT_DIR = Path(settings.BASE_DIR) / 'prompts' / OLLAMA_MODEL.split(':')[0]
```

Setting `OLLAMA_MODEL=qwen3.5:9b` automatically resolves `PROMPT_DIR` to `prompts/qwen3.5/`. The `:9b` tag is stripped. **Assumption:** The model tag (`:9b`) is never part of the directory name — only the base name matters for prompt organisation.

---

## 12. `tests_mcp.py` tests the loop, not the model

`tests_mcp.ToolCallLoopTests` mocks `requests.post` entirely. They test that `ollama_client.chat()` correctly:
- Sends the right HTTP payload to the right endpoint
- Parses tool calls from responses
- Calls the correct handler with the correct arguments
- Appends results in the correct role
- Loops until no tool calls remain
- Raises the right exceptions on errors

They do **not** test that `qwen3.5:9b` will call the right tools for a given user message. That is the job of `tests_llm.py` (live Ollama required). The separation is intentional: loop mechanics can be validated in CI without a GPU.

---

## 13. `MCPToolFunctionTests` uses `SimpleTestCase`, not `TestCase`

`SimpleTestCase` skips database setup entirely. The `mcp_*` service functions are tested with fully mocked ORM objects. This is appropriate because:

1. The functions perform only reads — no migrations or fixtures are needed.
2. `SimpleTestCase` runs faster and can run in any environment.
3. The mock-based approach verifies the serialisation logic in isolation.

Full database-backed tests of `mcp_get_tasks` (e.g. verifying `icontains` matching against real data) would live in `bot/tests.py` alongside the other ORM tests and are not yet written.

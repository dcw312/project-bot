# LLM Architecture Review

**Date:** 2026-04-04

---

## How the current system works

Every message sent in Discord triggers the following pipeline in `services.process_message()`:

1. Look up the user and check authorisation.
2. Call `build_context_summary()` — queries all projects the user is a member of, then all open (`todo`/`doing`) tasks for each project, and serialises everything into a plain-text block.
3. Concatenate the system prompt + context block + user message into a single raw prompt and POST it to Ollama (`/api/generate`).
4. Parse the JSON response (`action`, `data`, `message`).
5. Dispatch to the appropriate service function (`create_project`, `add_task`, `update_task`, `list_tasks`, `no_op`).
6. Write a `MessageLog` row.

The five supported actions and their data shapes are defined in `prompts/lfm2/system.txt` and validated in `ollama_client.parse_llm_response()`.

---

## The scalability problem

### Context grows with the user's workload

`build_context_summary()` dumps **every open task across every project** into the prompt on every single message, regardless of what the user is asking about. A user with 5 projects and 50 open tasks will produce a context block of ~2 KB before the user has even typed anything. At 10 projects and 200 tasks the context could easily exceed the model's effective attention window, causing degraded or empty responses — which is exactly what we observed with `lfm2` during development.

### The LLM is doing two jobs at once

The model is simultaneously asked to:
- **Understand intent** — what action does the user want?
- **Resolve references** — which task is "that bug I mentioned"? which project does this belong to?

Reference resolution requires the full data to be in the prompt. Intent classification does not. Mixing them means the prompt must always contain everything, even for simple requests like "mark task 3 as done" where task 3's description is irrelevant.

### Every message pays the full cost

A `no_op` conversational reply ("thanks!") triggers the same full context build and LLM call as a complex task creation. There is no pre-filter.

---

## Alternative approaches

### Option A — Intent-first, data-second (two-stage pipeline)

Split the LLM's job into two stages:

**Stage 1 — Intent classification (no context needed)**
Send only the user message to the LLM with a minimal prompt asking it to identify the action type and any explicit parameters (project name, task ID, status, etc.).

**Stage 2 — Execution (no LLM needed)**
The bot resolves any fuzzy references using the structured parameters from stage 1, executes the service function, and returns the result directly.

Context is only fetched if stage 1 returns an action that requires it (e.g. `list_tasks`). For `add_task` with an explicit project name, no context fetch is needed at all.

**Trade-off:** Two LLM calls for ambiguous messages; one for explicit ones. Harder to handle genuinely contextual requests ("add it to the same project as last time").

---

### Option B — Structured command parsing (LLM as NLP layer only)

Treat the LLM as a pure natural-language-to-command translator. It receives the user message and a fixed schema, and returns a structured command. It never sees task data.

```
User:  "add a high priority task to fix the login bug in the website project"
LLM →  { "action": "add_task", "title": "Fix login bug", "project_name": "website", "priority": 1 }
```

The bot resolves `"website"` to the actual `Project` record using fuzzy matching (e.g. case-insensitive `LIKE`). If resolution is ambiguous, the bot asks the user to clarify.

Task IDs are always supplied explicitly by the user ("mark task 3 as done") and never inferred from context.

**Trade-off:** Loses the ability to handle implicit references ("mark the bug task as done"). The user must be more explicit, but the system becomes fully deterministic and the prompt stays small and fixed-size regardless of how much data exists.

---

### Option C — MCP (Model Context Protocol) tool calls

Expose the Django data as MCP tools that the LLM can call on demand rather than receiving everything upfront:

- `get_projects()` → returns the user's project list
- `get_tasks(project_name)` → returns open tasks for a project
- `search_tasks(query)` → full-text search across task titles

The LLM decides which tools to call based on the user's message, fetches only what it needs, then returns the action.

This is the most capable approach — it handles implicit references without bloating every prompt — but requires a model that supports tool/function calling reliably, and adds latency for each tool invocation round-trip.

**Trade-off:** `lfm2` does not support tool calling. This would require switching to a more capable model (e.g. `llama3`, `mistral`, or a cloud model via API). Local tool-calling models are larger and slower.

---

### Option D — Channel-scoped context only

A simpler incremental improvement: instead of dumping all projects and tasks into context, only include tasks for the project linked to the current Discord channel (via `discord_channel_id`). Unscoped channels fall back to the Miscellaneous project only.

This caps context size at one project's worth of tasks regardless of how many projects the user has. Users work in a project-specific channel; cross-project queries use a dedicated command.

**Trade-off:** Requires users to link channels to projects. Multi-project queries become harder. Does not solve the problem for users with a single large project.

---

## Summary comparison

| Approach | Context size | Model requirements | Implementation effort | Handles implicit refs |
|---|---|---|---|---|
| Current | All open tasks, always | Basic completion | Done | Yes (badly at scale) |
| A — Two-stage | Zero for simple, partial for complex | Basic completion | Medium | Partially |
| B — NLP layer only | Zero (fixed prompt) | Basic completion | Low | No |
| C — MCP tool calls | On demand | Tool calling required | High | Yes |
| D — Channel-scoped | One project | Basic completion | Low | Within project only |

**Recommended starting point:** Option B or D are low-effort and work with `lfm2`. Option C is the right long-term direction if the model is upgraded.

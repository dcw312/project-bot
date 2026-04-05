import json
import os
import re
import time
from pathlib import Path

import requests
from django.conf import settings


OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ['OLLAMA_MODEL']
PROMPT_DIR = Path(settings.BASE_DIR) / 'prompts' / OLLAMA_MODEL.split(':')[0]
SYSTEM_PROMPT_PATH = PROMPT_DIR / 'system.txt'

_system_prompt = None

SUPPORTED_ACTIONS = {'create_project', 'add_task', 'update_task', 'list_tasks', 'no_op'}

# Data tools: called by the model to fetch information on demand.
_DATA_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_projects",
            "description": "Return the list of projects the user is a member of.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Return open tasks for a named project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "The name of the project to fetch tasks for.",
                    },
                },
                "required": ["project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tasks",
            "description": "Search task titles across all the user's projects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword or phrase to search for in task titles.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# Action tools: the model calls exactly one to complete each request.
# chat() converts the tool call into the standard JSON action format so the
# rest of the pipeline (parse_llm_response, handle_llm_action) is unchanged.
_ACTION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Create a new project, optionally with starter tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"title": {"type": "string"}}},
                    },
                    "message": {"type": "string", "description": "Confirmation to show the user."},
                },
                "required": ["name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a task to a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "integer", "description": "1 (highest) to 5 (lowest), default 3"},
                    "due_date": {"type": "string", "description": "ISO8601 datetime, optional"},
                    "deadline_type": {"type": "string", "description": "hard or soft, default soft"},
                    "message": {"type": "string", "description": "Confirmation to show the user."},
                },
                "required": ["project_name", "title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Update one or more fields of an existing task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "fields": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "description": "todo, doing, or done"},
                            "priority": {"type": "integer"},
                            "title": {"type": "string"},
                            "due_date": {"type": "string"},
                            "deadline_type": {"type": "string"},
                        },
                    },
                    "message": {"type": "string", "description": "Confirmation to show the user."},
                },
                "required": ["task_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List the user's tasks, optionally filtered by project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Optional project filter."},
                    "message": {"type": "string", "description": "Intro message shown before the task list."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_op",
            "description": "No task or project action needed — reply conversationally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Conversational reply to show the user."},
                },
                "required": ["message"],
            },
        },
    },
]

TOOL_DEFINITIONS = _DATA_TOOL_DEFINITIONS + _ACTION_TOOL_DEFINITIONS


class OllamaUnavailableError(Exception):
    pass


class LLMResponseParseError(Exception):
    pass


class ToolCallError(Exception):
    """Raised when the model requests an unknown tool or a tool handler raises."""
    pass


def validate_ollama_startup():
    global _system_prompt

    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt not found: {SYSTEM_PROMPT_PATH}")

    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        response.raise_for_status()
        tags_data = response.json()
    except requests.RequestException as e:
        raise OllamaUnavailableError(f"Cannot reach Ollama at {OLLAMA_BASE_URL}: {e}")

    available_models = [m['name'] for m in tags_data.get('models', [])]
    if OLLAMA_MODEL not in available_models:
        raise RuntimeError(
            f"Model '{OLLAMA_MODEL}' not found in Ollama. Available: {available_models}"
        )

    _system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding='utf-8')


def build_messages(user_message: str) -> list[dict]:
    return [
        {"role": "system", "content": _system_prompt},
        {"role": "user", "content": user_message},
    ]


def chat(user_message: str, tool_handlers: dict) -> tuple[dict, int, list[dict]]:
    """
    Run the tool-calling loop against Ollama /api/chat.

    tool_handlers: mapping of data tool name -> callable returning a JSON string.
                   Action tool names (SUPPORTED_ACTIONS) are handled internally
                   and must NOT be included in tool_handlers.

    The loop runs until either:
    - The model calls an action tool → arguments are converted to the standard
      {"action", "data", "message"} JSON string and returned.
    - The model returns a non-empty text response (fallback path) → returned as-is.

    Returns ({"message": {"content": "<JSON or text>"}}, elapsed_ms, tool_trace).

    tool_trace is a list of dicts recording every tool call made during the loop:
      {"tool": name, "arguments": {...}, "result": "<JSON string or null>"}
    Data tools carry the result string; action tools carry null (they are the
    final action, not a data fetch).
    """
    messages = build_messages(user_message)
    start = time.monotonic()
    nudge_sent = False
    tool_trace: list[dict] = []

    while True:
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "tools": TOOL_DEFINITIONS,
                    "stream": False,
                },
                timeout=120,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise OllamaUnavailableError(f"Ollama request failed: {e}")

        data = response.json()
        assistant_message = data.get("message", {})
        tool_calls = assistant_message.get("tool_calls")

        if not tool_calls:
            content = assistant_message.get("content", "")
            # Strip think tags to check if there is usable content
            actual = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if not actual and not nudge_sent:
                # Model returned empty content (thinking may be in <think> tags).
                # Append the turn and nudge once to get the action tool call.
                nudge_sent = True
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "Please call an action tool to complete the request."})
                continue
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"message": {"content": content}}, elapsed_ms, tool_trace

        # Append the assistant's tool-call turn to the conversation
        messages.append({
            "role": "assistant",
            "content": assistant_message.get("content", ""),
            "tool_calls": tool_calls,
        })

        # Execute each requested tool; action tools terminate the loop immediately
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments", {})

            # Action tool: convert arguments to the standard JSON action format
            if name in SUPPORTED_ACTIONS:
                args = dict(arguments) if isinstance(arguments, dict) else {}
                reply = args.pop("message", "")
                tool_trace.append({"tool": name, "arguments": dict(arguments) if isinstance(arguments, dict) else {}, "result": None})
                action_content = json.dumps({"action": name, "data": args, "message": reply})
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return {"message": {"content": action_content}}, elapsed_ms, tool_trace

            # Data tool: execute handler and feed result back
            handler = tool_handlers.get(name)
            if handler is None:
                raise ToolCallError(f"Unknown tool requested by model: '{name}'")

            try:
                result = handler(**arguments)
            except Exception as e:
                raise ToolCallError(f"Tool '{name}' raised an error: {e}")

            tool_trace.append({"tool": name, "arguments": arguments, "result": result})
            messages.append({"role": "tool", "content": result})


def parse_llm_response(raw_content: str) -> dict:
    stripped = raw_content.strip()

    if not stripped:
        raise LLMResponseParseError("LLM returned an empty response.")

    # Strip <think>...</think> blocks emitted by reasoning models (e.g. Qwen 3)
    stripped = re.sub(r'<think>.*?</think>', '', stripped, flags=re.DOTALL).strip()

    # Extract JSON from a code fence if present anywhere in the response.
    # The model sometimes emits reasoning prose before the fence.
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(f"Failed to parse LLM JSON response: {e}\nContent: {raw_content!r}")

    for key in ('action', 'data'):
        if key not in parsed:
            raise LLMResponseParseError(f"LLM response missing required key '{key}': {parsed}")

    # 'message' is required by the schema but default gracefully so a missing
    # field never blocks a user-facing response.
    if 'message' not in parsed:
        parsed['message'] = ''

    if parsed['action'] not in SUPPORTED_ACTIONS:
        raise LLMResponseParseError(
            f"Unsupported action '{parsed['action']}'. Must be one of: {SUPPORTED_ACTIONS}"
        )

    return parsed

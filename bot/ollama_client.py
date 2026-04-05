import json
import os
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

TOOL_DEFINITIONS = [
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


def chat(user_message: str, tool_handlers: dict) -> tuple[dict, int]:
    """
    Run the tool-calling loop against Ollama /api/chat.

    tool_handlers: mapping of tool name -> callable that accepts keyword
                   arguments and returns a JSON string.

    Loops until the model stops issuing tool_calls, then returns
    ({"message": {"content": "<JSON action string>"}}, elapsed_ms).
    """
    messages = build_messages(user_message)
    start = time.monotonic()

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
            # No tool calls — this is the final response
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"message": {"content": assistant_message.get("content", "")}}, elapsed_ms

        # Append the assistant's tool-call turn to the conversation
        messages.append({
            "role": "assistant",
            "content": assistant_message.get("content", ""),
            "tool_calls": tool_calls,
        })

        # Execute each requested tool and append results
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments", {})

            handler = tool_handlers.get(name)
            if handler is None:
                raise ToolCallError(f"Unknown tool requested by model: '{name}'")

            try:
                result = handler(**arguments)
            except Exception as e:
                raise ToolCallError(f"Tool '{name}' raised an error: {e}")

            messages.append({"role": "tool", "content": result})


def parse_llm_response(raw_content: str) -> dict:
    stripped = raw_content.strip()
    if stripped.startswith('```'):
        lines = stripped.splitlines()
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        stripped = '\n'.join(lines).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(f"Failed to parse LLM JSON response: {e}\nContent: {raw_content!r}")

    for key in ('action', 'data', 'message'):
        if key not in parsed:
            raise LLMResponseParseError(f"LLM response missing required key '{key}': {parsed}")

    if parsed['action'] not in SUPPORTED_ACTIONS:
        raise LLMResponseParseError(
            f"Unsupported action '{parsed['action']}'. Must be one of: {SUPPORTED_ACTIONS}"
        )

    return parsed

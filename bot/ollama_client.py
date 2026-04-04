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


class OllamaUnavailableError(Exception):
    pass


class LLMResponseParseError(Exception):
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


def build_messages(user_message: str, context_summary: str) -> list[dict]:
    system_content = (
        _system_prompt
        + "\n\n## Current context\n"
        + context_summary
        + '\n\nUnderstood? Reply only with: {"action":"no_op","data":{},"message":"Ready."}'
    )
    return [
        {"role": "user", "content": system_content},
        {"role": "assistant", "content": '{"action":"no_op","data":{},"message":"Ready."}'},
        {"role": "user", "content": user_message},
    ]


def chat(user_message: str, context_summary: str) -> tuple[dict, int]:
    messages = build_messages(user_message, context_summary)
    start = time.monotonic()
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise OllamaUnavailableError(f"Ollama chat request failed: {e}")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return response.json(), elapsed_ms


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

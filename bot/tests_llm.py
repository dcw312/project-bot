"""
Integration tests for LLM prompt/response behaviour.

These tests call Ollama directly and are intentionally slow (~seconds each).
Run them with:
    venv/bin/python manage.py test bot.tests_llm --verbosity=2

They are kept separate from bot/tests.py so the normal test suite stays fast.
Tests are skipped automatically if Ollama is unreachable.
"""

import requests
from django.test import TestCase

from bot.ollama_client import (
    LLMResponseParseError,
    OllamaUnavailableError,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    validate_ollama_startup,
    chat,
    parse_llm_response,
)


def _ollama_available() -> bool:
    try:
        requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return True
    except requests.RequestException:
        return False


SAMPLE_CONTEXT = (
    "User: david\n\n"
    "Project: Miscellaneous\n"
    "  - [todo] (id=1) Buy groceries (priority=3)\n\n"
    "Project: Website Redesign\n"
    "  - [todo] (id=2) Write homepage copy (priority=2)\n"
    "  - [doing] (id=3) Design mockups (priority=1)\n"
)


class LLMResponseTests(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _ollama_available():
            return
        validate_ollama_startup()

    def setUp(self):
        if not _ollama_available():
            self.skipTest("Ollama is not running")

    def _ask(self, message: str) -> dict:
        raw_response, _ = chat(message, SAMPLE_CONTEXT)
        raw_content = raw_response["message"]["content"]
        return parse_llm_response(raw_content)

    # --- list_tasks ---

    def test_list_my_tasks(self):
        result = self._ask("list my tasks")
        self.assertEqual(result["action"], "list_tasks")

    def test_list_tasks_for_project(self):
        result = self._ask("show my tasks for Website Redesign")
        self.assertEqual(result["action"], "list_tasks")
        self.assertIn("Website Redesign", result["data"].get("project_name", ""))

    # --- create_project ---

    def test_create_project(self):
        result = self._ask("create a project called Mobile App")
        self.assertEqual(result["action"], "create_project")
        self.assertIn("Mobile App", result["data"].get("name", ""))

    def test_create_project_with_description(self):
        result = self._ask("start a new project called API Refactor for cleaning up the backend")
        self.assertEqual(result["action"], "create_project")

    # --- add_task ---

    def test_add_task(self):
        result = self._ask("add a task to write unit tests")
        self.assertEqual(result["action"], "add_task")
        self.assertTrue(result["data"].get("title"))

    def test_add_task_to_project(self):
        result = self._ask("add a task called Deploy to staging to the Website Redesign project")
        self.assertEqual(result["action"], "add_task")
        self.assertIn("Website Redesign", result["data"].get("project_name", ""))

    def test_add_task_with_priority(self):
        result = self._ask("add an urgent task to fix the login bug, high priority")
        self.assertEqual(result["action"], "add_task")
        priority = result["data"].get("priority", 3)
        self.assertLessEqual(priority, 2)

    # --- update_task ---

    def test_mark_task_done(self):
        result = self._ask("mark task 1 as done")
        self.assertEqual(result["action"], "update_task")
        self.assertEqual(result["data"].get("task_id"), 1)
        self.assertEqual(result["data"].get("fields", {}).get("status"), "done")

    def test_mark_task_in_progress(self):
        result = self._ask("mark task 2 as in progress")
        self.assertEqual(result["action"], "update_task")
        self.assertEqual(result["data"].get("task_id"), 2)
        self.assertEqual(result["data"].get("fields", {}).get("status"), "doing")

    def test_update_task_priority(self):
        result = self._ask("change task 3 to priority 1")
        self.assertEqual(result["action"], "update_task")
        self.assertEqual(result["data"].get("task_id"), 3)

    # --- no_op ---

    def test_greeting_is_no_op(self):
        result = self._ask("hello")
        self.assertEqual(result["action"], "no_op")

    def test_conversational_is_no_op(self):
        result = self._ask("how are you doing today?")
        self.assertEqual(result["action"], "no_op")

    # --- response format ---

    def test_response_always_has_message(self):
        result = self._ask("list my tasks")
        self.assertIn("message", result)
        self.assertIsInstance(result["message"], str)
        self.assertTrue(len(result["message"]) > 0)

    def test_response_action_is_valid(self):
        from bot.ollama_client import SUPPORTED_ACTIONS
        result = self._ask("what should I work on next?")
        self.assertIn(result["action"], SUPPORTED_ACTIONS)

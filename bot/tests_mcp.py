"""
MCP tool-calling loop integration tests.

These tests exercise the full tool-calling loop in ollama_client.chat()
without a live Ollama instance. HTTP calls are intercepted with
unittest.mock.patch so every path through the loop can be tested
deterministically and quickly.

Run with:
    venv/bin/python manage.py test bot.tests_mcp --verbosity=2

What is covered:
  1. No-tool path — model returns JSON action immediately, no tool calls.
  2. Single tool call — model calls get_projects once, then returns action.
  3. Multi-tool call sequence — model calls get_projects then get_tasks in
     separate turns before returning the final action.
  4. Multiple tool calls in one turn — model batches two calls in one response.
  5. Tool result is passed back — asserts the tool return value appears in the
     second request's message list.
  6. Unknown tool raises ToolCallError.
  7. Tool handler exception is wrapped in ToolCallError.
  8. Ollama HTTP error raises OllamaUnavailableError.
  9. Final response with empty tool_calls list treated as no-tool (edge case).
 10. parse_llm_response integration — chat() + parse_llm_response() together
     produce a valid action dict on the no-tool path.
"""

import json
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from bot.ollama_client import (
    LLMResponseParseError,
    OllamaUnavailableError,
    ToolCallError,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    TOOL_DEFINITIONS,
    chat,
    parse_llm_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(payload: dict) -> MagicMock:
    """Build a mock requests.Response that returns *payload* from .json()."""
    mock = MagicMock()
    mock.json.return_value = payload
    mock.raise_for_status.return_value = None
    return mock


def _tool_call_msg(name: str, arguments: dict) -> dict:
    """Build the assistant message dict that contains a tool call."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def _final_msg(action_json: str) -> dict:
    """Build the assistant message dict for the final JSON action response."""
    return {"role": "assistant", "content": action_json}


# Canonical action JSON strings used across multiple tests
_NO_OP_JSON = json.dumps({"action": "no_op", "data": {}, "message": "Hello!"})
_LIST_TASKS_JSON = json.dumps({"action": "list_tasks", "data": {}, "message": "Here are your tasks."})
_ADD_TASK_JSON = json.dumps({
    "action": "add_task",
    "data": {"project_name": "Website Redesign", "title": "Write tests"},
    "message": "Task added.",
})

# Stub tool handlers used in most tests
_PROJECTS_JSON = json.dumps([{"id": 1, "name": "Website Redesign", "description": ""}])
_TASKS_JSON = json.dumps({"project": "Website Redesign", "tasks": [{"id": 5, "title": "Write tests", "status": "todo", "priority": 2, "due_date": None}]})

STUB_HANDLERS = {
    "get_projects": lambda **_: _PROJECTS_JSON,
    "get_tasks": lambda project_name, **_: _TASKS_JSON,
    "search_tasks": lambda query, **_: json.dumps([]),
}


# ---------------------------------------------------------------------------
# Tests — ollama_client.chat() loop mechanics
# ---------------------------------------------------------------------------

@patch("bot.ollama_client._system_prompt", "You are a test assistant.")
class ToolCallLoopTests(SimpleTestCase):
    """
    Tests for the tool-calling loop mechanics in ollama_client.chat().

    _system_prompt is patched at module level so build_messages() does not
    fail with TypeError when it tries to concatenate None.
    """

    # ------------------------------------------------------------------
    # 1. No-tool path
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_no_tool_call_returns_final_response(self, mock_post):
        """Model returns JSON action on the first request — no tool calls."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        response, elapsed_ms = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        self.assertIsInstance(elapsed_ms, int)
        self.assertGreaterEqual(elapsed_ms, 0)
        mock_post.assert_called_once()

    # ------------------------------------------------------------------
    # 2. Single tool call
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_single_tool_call_then_final_response(self, mock_post):
        """Model calls get_projects once, then returns the final action."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        response, _ = chat("list my tasks", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _LIST_TASKS_JSON)
        self.assertEqual(mock_post.call_count, 2)

    # ------------------------------------------------------------------
    # 3. Multi-turn tool calls
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_multi_turn_tool_calls(self, mock_post):
        """Model calls get_projects then get_tasks in separate turns."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _tool_call_msg("get_tasks", {"project_name": "Website Redesign"})}),
            _make_response({"message": _final_msg(_ADD_TASK_JSON)}),
        ]

        response, _ = chat("add a task to Website Redesign", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _ADD_TASK_JSON)
        self.assertEqual(mock_post.call_count, 3)

    # ------------------------------------------------------------------
    # 4. Multiple tool calls batched in one turn
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_batched_tool_calls_in_one_turn(self, mock_post):
        """Model issues two tool calls in a single response turn."""
        batched_msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_projects", "arguments": {}}},
                {"function": {"name": "get_tasks", "arguments": {"project_name": "Website Redesign"}}},
            ],
        }
        mock_post.side_effect = [
            _make_response({"message": batched_msg}),
            _make_response({"message": _final_msg(_ADD_TASK_JSON)}),
        ]

        response, _ = chat("add a task to Website Redesign", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _ADD_TASK_JSON)
        self.assertEqual(mock_post.call_count, 2)

        # Second request messages should contain two tool result entries
        second_call_messages = mock_post.call_args_list[1][1]["json"]["messages"]
        tool_result_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_result_messages), 2)

    # ------------------------------------------------------------------
    # 5. Tool result is forwarded in subsequent request
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_tool_result_appears_in_next_request(self, mock_post):
        """The tool handler's return value is sent back to the model."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        chat("list my tasks", STUB_HANDLERS)

        second_request_messages = mock_post.call_args_list[1][1]["json"]["messages"]
        tool_result = next(m for m in second_request_messages if m.get("role") == "tool")
        self.assertEqual(tool_result["content"], _PROJECTS_JSON)

    # ------------------------------------------------------------------
    # 6. Unknown tool raises ToolCallError
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_unknown_tool_raises_tool_call_error(self, mock_post):
        """Model requests a tool that is not in tool_handlers."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("delete_all_tasks", {}),
        })

        with self.assertRaises(ToolCallError) as ctx:
            chat("delete everything", STUB_HANDLERS)

        self.assertIn("delete_all_tasks", str(ctx.exception))

    # ------------------------------------------------------------------
    # 7. Tool handler exception is wrapped in ToolCallError
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_tool_handler_exception_wrapped(self, mock_post):
        """An exception raised inside a tool handler becomes ToolCallError."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("get_projects", {}),
        })
        exploding_handlers = {
            "get_projects": lambda **_: (_ for _ in ()).throw(RuntimeError("DB is down")),
        }

        with self.assertRaises(ToolCallError) as ctx:
            chat("list my projects", exploding_handlers)

        self.assertIn("get_projects", str(ctx.exception))

    # ------------------------------------------------------------------
    # 8. Ollama HTTP error raises OllamaUnavailableError
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_http_error_raises_ollama_unavailable(self, mock_post):
        """A requests.RequestException on any iteration raises OllamaUnavailableError."""
        import requests as req
        mock_post.side_effect = req.ConnectionError("refused")

        with self.assertRaises(OllamaUnavailableError):
            chat("hello", STUB_HANDLERS)

    # ------------------------------------------------------------------
    # 9. Empty tool_calls list treated as final response (edge case)
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_empty_tool_calls_list_treated_as_final(self, mock_post):
        """tool_calls=[] (falsy) should be treated the same as tool_calls absent."""
        mock_post.return_value = _make_response({
            "message": {"role": "assistant", "content": _NO_OP_JSON, "tool_calls": []},
        })

        response, _ = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        mock_post.assert_called_once()

    # ------------------------------------------------------------------
    # 10. chat() + parse_llm_response() round-trip
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_chat_and_parse_round_trip(self, mock_post):
        """Full round-trip: chat() followed by parse_llm_response() yields a valid dict."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        raw_response, _ = chat("hello", STUB_HANDLERS)
        parsed = parse_llm_response(raw_response["message"]["content"])

        self.assertEqual(parsed["action"], "no_op")
        self.assertEqual(parsed["data"], {})
        self.assertEqual(parsed["message"], "Hello!")

    # ------------------------------------------------------------------
    # 11. TOOL_DEFINITIONS are sent on every request
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_tool_definitions_sent_on_every_request(self, mock_post):
        """Each /api/chat POST includes the full TOOL_DEFINITIONS array."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        chat("list my tasks", STUB_HANDLERS)

        for call_kwargs in mock_post.call_args_list:
            sent_tools = call_kwargs[1]["json"]["tools"]
            self.assertEqual(sent_tools, TOOL_DEFINITIONS)

    # ------------------------------------------------------------------
    # 12. Correct Ollama endpoint is used
    # ------------------------------------------------------------------

    @patch("bot.ollama_client.requests.post")
    def test_uses_api_chat_endpoint(self, mock_post):
        """Requests must target /api/chat, not the old /api/generate."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        chat("hello", STUB_HANDLERS)

        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/api/chat"), f"Expected /api/chat, got: {url}")
        self.assertNotIn("/api/generate", url)


# ---------------------------------------------------------------------------
# Tests — mcp_* service functions (unit, no HTTP)
# ---------------------------------------------------------------------------

class MCPToolFunctionTests(SimpleTestCase):
    """
    Unit tests for the mcp_get_projects / mcp_get_tasks / mcp_search_tasks
    functions in services.py.

    These do not hit the database; they use mock objects so that the JSON
    serialisation logic can be validated without Django ORM setup.
    """

    def _make_user(self):
        user = MagicMock()
        user.username = "testuser"
        return user

    def test_mcp_get_projects_returns_json_list(self):
        """mcp_get_projects returns a JSON array of project dicts."""
        from bot.services import mcp_get_projects

        p1 = MagicMock()
        p1.id = 1
        p1.name = "Alpha"
        p1.description = "First project"

        p2 = MagicMock()
        p2.id = 2
        p2.name = "Beta"
        p2.description = None

        with patch("bot.services.get_user_projects", return_value=[p1, p2]):
            result = mcp_get_projects(self._make_user())

        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "Alpha")
        self.assertEqual(parsed[1]["description"], "")  # None -> ""

    def test_mcp_get_tasks_returns_json_object(self):
        """mcp_get_tasks returns {"project": name, "tasks": [...]}."""
        from bot.services import mcp_get_tasks

        task = MagicMock()
        task.id = 7
        task.title = "Fix the bug"
        task.status = "todo"
        task.priority = 2
        task.due_date = None

        project = MagicMock()
        project.id = 1
        project.name = "Alpha"

        # The queryset mock must support .exists() (bool check) and be
        # iterable for min(); configure both on the same MagicMock object.
        mock_project_qs = MagicMock()
        mock_project_qs.exists.return_value = True
        mock_project_qs.__iter__ = MagicMock(return_value=iter([project]))

        with (
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Project.objects") as mock_proj,
            patch("bot.services.Task.objects") as mock_task,
        ):
            mock_pm.filter.return_value.values_list.return_value = [1]
            mock_proj.filter.return_value = mock_project_qs
            mock_task.filter.return_value = [task]

            result = mcp_get_tasks(self._make_user(), "Alpha")

        parsed = json.loads(result)
        self.assertEqual(parsed["project"], "Alpha")
        self.assertEqual(len(parsed["tasks"]), 1)
        self.assertEqual(parsed["tasks"][0]["title"], "Fix the bug")

    def test_mcp_get_tasks_no_match_returns_error_json(self):
        """mcp_get_tasks returns {"error": ...} when no project matches."""
        from bot.services import mcp_get_tasks

        with (
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Project.objects") as mock_proj,
        ):
            mock_pm.filter.return_value.values_list.return_value = []
            mock_proj.filter.return_value.exists.return_value = False

            result = mcp_get_tasks(self._make_user(), "Nonexistent")

        parsed = json.loads(result)
        self.assertIn("error", parsed)
        self.assertIn("Nonexistent", parsed["error"])

    def test_mcp_search_tasks_returns_json_list(self):
        """mcp_search_tasks returns a JSON array of matching task dicts."""
        from bot.services import mcp_search_tasks

        task = MagicMock()
        task.id = 3
        task.title = "Write homepage copy"
        task.status = "todo"
        task.priority = 2
        task.project.name = "Website Redesign"

        with (
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Task.objects") as mock_task,
        ):
            mock_pm.filter.return_value.values_list.return_value = [2]
            mock_task.filter.return_value.select_related.return_value = [task]

            result = mcp_search_tasks(self._make_user(), "homepage")

        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "Write homepage copy")
        self.assertEqual(parsed[0]["project"], "Website Redesign")

    def test_mcp_search_tasks_empty_returns_empty_list(self):
        """mcp_search_tasks returns [] when no tasks match."""
        from bot.services import mcp_search_tasks

        with (
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Task.objects") as mock_task,
        ):
            mock_pm.filter.return_value.values_list.return_value = []
            mock_task.filter.return_value.select_related.return_value = []

            result = mcp_search_tasks(self._make_user(), "xyz")

        parsed = json.loads(result)
        self.assertEqual(parsed, [])

    def test_mcp_tool_results_are_strings(self):
        """All three MCP tools must return str (not dict), per Ollama protocol."""
        from bot.services import mcp_get_projects, mcp_get_tasks, mcp_search_tasks

        project = MagicMock()
        project.id = 1
        project.name = "Alpha"
        project.description = ""

        task = MagicMock()
        task.id = 1
        task.title = "A task"
        task.status = "todo"
        task.priority = 3
        task.due_date = None
        task.project.name = "Alpha"

        # Task queryset mock must support both direct iteration (mcp_get_tasks
        # iterates the filter result) and .select_related() (mcp_search_tasks).
        mock_task_qs = MagicMock()
        mock_task_qs.__iter__ = MagicMock(return_value=iter([task]))
        mock_task_qs.select_related.return_value = [task]

        mock_project_qs = MagicMock()
        mock_project_qs.exists.return_value = True
        mock_project_qs.__iter__ = MagicMock(return_value=iter([project]))

        with (
            patch("bot.services.get_user_projects", return_value=[project]),
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Project.objects") as mock_proj,
            patch("bot.services.Task.objects") as mock_task,
        ):
            mock_pm.filter.return_value.values_list.return_value = [1]
            mock_proj.filter.return_value = mock_project_qs
            mock_task.filter.return_value = mock_task_qs

            r1 = mcp_get_projects(self._make_user())
            r2 = mcp_get_tasks(self._make_user(), "Alpha")
            r3 = mcp_search_tasks(self._make_user(), "task")

        self.assertIsInstance(r1, str)
        self.assertIsInstance(r2, str)
        self.assertIsInstance(r3, str)

"""
MCP tool-calling loop integration tests.

These tests exercise the full tool-calling loop in ollama_client.chat()
without a live Ollama instance. HTTP calls are intercepted with
unittest.mock.patch so every path through the loop can be tested
deterministically and quickly.

Run with:
    venv/bin/python manage.py test bot.tests_mcp --verbosity=2

What is covered:

  Loop mechanics
  1.  No-tool path — model returns JSON action in content, no tool calls.
  2.  Single data tool call — model calls get_projects, then returns content.
  3.  Multi-turn data tool calls — get_projects then get_tasks, then content.
  4.  Multiple data tool calls batched in one turn.
  5.  Tool result forwarded in the next request body.
  6.  Unknown tool raises ToolCallError.
  7.  Tool handler exception is wrapped in ToolCallError.
  8.  Ollama HTTP error raises OllamaUnavailableError.
  9.  Empty tool_calls list (falsy) treated as no-tool path.
  10. chat() + parse_llm_response() round-trip on the no-tool path.
  11. TOOL_DEFINITIONS sent on every request.
  12. /api/chat endpoint used (not /api/generate).

  Action-tool path (model calls action as tool instead of returning JSON)
  13. Action tool call immediately returns formatted JSON response.
  14. Action tool in a batch with data tools returns immediately.
  15. message field is extracted from action tool arguments.

  Empty-content nudge
  16. Empty content triggers one nudge message, then succeeds.
  17. Content that is only <think> tags triggers the nudge.

  mcp_* service function unit tests
  18-23. mcp_get_projects, mcp_get_tasks, mcp_search_tasks JSON output.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from bot.ollama_client import (
    LLMResponseParseError,
    OllamaUnavailableError,
    ToolCallError,
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
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


def _final_msg(content: str) -> dict:
    return {"role": "assistant", "content": content}


# Canonical JSON strings
_NO_OP_JSON = json.dumps({"action": "no_op", "data": {}, "message": "Hello!"})
_LIST_TASKS_JSON = json.dumps({"action": "list_tasks", "data": {}, "message": "Here are your tasks."})
_ADD_TASK_JSON = json.dumps({
    "action": "add_task",
    "data": {"project_name": "Website Redesign", "title": "Write tests"},
    "message": "Task added.",
})

_PROJECTS_JSON = json.dumps([{"id": 1, "name": "Website Redesign", "description": ""}])
_TASKS_JSON = json.dumps({
    "project": "Website Redesign",
    "tasks": [{"id": 5, "title": "Write tests", "status": "todo", "priority": 2, "due_date": None}],
})

STUB_HANDLERS = {
    "get_projects": lambda **_: _PROJECTS_JSON,
    "get_tasks": lambda project_name, **_: _TASKS_JSON,
    "search_tasks": lambda query, **_: json.dumps([]),
}


# ---------------------------------------------------------------------------
# Tests — loop mechanics
# ---------------------------------------------------------------------------

@patch("bot.ollama_client._system_prompt", "You are a test assistant.")
class ToolCallLoopTests(SimpleTestCase):

    # 1. No-tool path
    @patch("bot.ollama_client.requests.post")
    def test_no_tool_call_returns_final_response(self, mock_post):
        """Model returns JSON action in content on the first request."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        response, elapsed_ms = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        self.assertIsInstance(elapsed_ms, int)
        mock_post.assert_called_once()

    # 2. Single data tool call
    @patch("bot.ollama_client.requests.post")
    def test_single_data_tool_call_then_final_response(self, mock_post):
        """Model calls get_projects once, then returns JSON in content."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        response, _ = chat("list my tasks", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _LIST_TASKS_JSON)
        self.assertEqual(mock_post.call_count, 2)

    # 3. Multi-turn data tool calls
    @patch("bot.ollama_client.requests.post")
    def test_multi_turn_data_tool_calls(self, mock_post):
        """Model calls get_projects then get_tasks in separate turns."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _tool_call_msg("get_tasks", {"project_name": "Website Redesign"})}),
            _make_response({"message": _final_msg(_ADD_TASK_JSON)}),
        ]

        response, _ = chat("add a task to Website Redesign", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _ADD_TASK_JSON)
        self.assertEqual(mock_post.call_count, 3)

    # 4. Multiple data tool calls batched in one turn
    @patch("bot.ollama_client.requests.post")
    def test_batched_data_tool_calls(self, mock_post):
        """Model issues two data tool calls in a single response turn."""
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

        response, _ = chat("add a task", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _ADD_TASK_JSON)
        second_messages = mock_post.call_args_list[1][1]["json"]["messages"]
        tool_results = [m for m in second_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_results), 2)

    # 5. Tool result forwarded in subsequent request
    @patch("bot.ollama_client.requests.post")
    def test_data_tool_result_forwarded(self, mock_post):
        """The data tool handler's return value appears in the next request."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        chat("list my tasks", STUB_HANDLERS)

        second_messages = mock_post.call_args_list[1][1]["json"]["messages"]
        tool_result = next(m for m in second_messages if m.get("role") == "tool")
        self.assertEqual(tool_result["content"], _PROJECTS_JSON)

    # 6. Unknown tool raises ToolCallError
    @patch("bot.ollama_client.requests.post")
    def test_unknown_tool_raises_tool_call_error(self, mock_post):
        """Model requests a tool not in tool_handlers and not an action."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("delete_all_tasks", {}),
        })

        with self.assertRaises(ToolCallError) as ctx:
            chat("delete everything", STUB_HANDLERS)

        self.assertIn("delete_all_tasks", str(ctx.exception))

    # 7. Tool handler exception wrapped
    @patch("bot.ollama_client.requests.post")
    def test_tool_handler_exception_wrapped(self, mock_post):
        """Exception from a data tool handler is wrapped in ToolCallError."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("get_projects", {}),
        })
        exploding = {"get_projects": lambda **_: (_ for _ in ()).throw(RuntimeError("DB down"))}

        with self.assertRaises(ToolCallError) as ctx:
            chat("list my projects", exploding)

        self.assertIn("get_projects", str(ctx.exception))

    # 8. Ollama HTTP error
    @patch("bot.ollama_client.requests.post")
    def test_http_error_raises_ollama_unavailable(self, mock_post):
        """A requests.RequestException raises OllamaUnavailableError."""
        import requests as req
        mock_post.side_effect = req.ConnectionError("refused")

        with self.assertRaises(OllamaUnavailableError):
            chat("hello", STUB_HANDLERS)

    # 9. Empty tool_calls list treated as no-tool path
    @patch("bot.ollama_client.requests.post")
    def test_empty_tool_calls_list_treated_as_final(self, mock_post):
        """tool_calls=[] (falsy) is treated the same as absent tool_calls."""
        mock_post.return_value = _make_response({
            "message": {"role": "assistant", "content": _NO_OP_JSON, "tool_calls": []},
        })

        response, _ = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        mock_post.assert_called_once()

    # 10. chat() + parse_llm_response() round-trip
    @patch("bot.ollama_client.requests.post")
    def test_chat_and_parse_round_trip(self, mock_post):
        """Full round-trip: chat() then parse_llm_response() yields valid dict."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        raw_response, _ = chat("hello", STUB_HANDLERS)
        parsed = parse_llm_response(raw_response["message"]["content"])

        self.assertEqual(parsed["action"], "no_op")
        self.assertEqual(parsed["data"], {})
        self.assertEqual(parsed["message"], "Hello!")

    # 11. TOOL_DEFINITIONS sent on every request
    @patch("bot.ollama_client.requests.post")
    def test_tool_definitions_sent_on_every_request(self, mock_post):
        """Each POST includes the full TOOL_DEFINITIONS array."""
        mock_post.side_effect = [
            _make_response({"message": _tool_call_msg("get_projects", {})}),
            _make_response({"message": _final_msg(_LIST_TASKS_JSON)}),
        ]

        chat("list my tasks", STUB_HANDLERS)

        for call_kwargs in mock_post.call_args_list:
            self.assertEqual(call_kwargs[1]["json"]["tools"], TOOL_DEFINITIONS)

    # 12. /api/chat endpoint used
    @patch("bot.ollama_client.requests.post")
    def test_uses_api_chat_endpoint(self, mock_post):
        """Requests must target /api/chat, not the old /api/generate."""
        mock_post.return_value = _make_response({"message": _final_msg(_NO_OP_JSON)})

        chat("hello", STUB_HANDLERS)

        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/api/chat"), f"Expected /api/chat, got: {url}")
        self.assertNotIn("/api/generate", url)

    # ------------------------------------------------------------------
    # Action-tool path
    # ------------------------------------------------------------------

    # 13. Action tool call returns formatted JSON immediately
    @patch("bot.ollama_client.requests.post")
    def test_action_tool_returns_json_response(self, mock_post):
        """Model calls an action tool; loop converts args to JSON and returns."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("add_task", {
                "project_name": "Website Redesign",
                "title": "Write tests",
                "message": "Task added.",
            }),
        })

        response, _ = chat("add a task", STUB_HANDLERS)
        parsed = parse_llm_response(response["message"]["content"])

        self.assertEqual(parsed["action"], "add_task")
        self.assertEqual(parsed["data"]["project_name"], "Website Redesign")
        self.assertEqual(parsed["data"]["title"], "Write tests")
        self.assertEqual(parsed["message"], "Task added.")
        # message must NOT appear in data
        self.assertNotIn("message", parsed["data"])
        mock_post.assert_called_once()

    # 14. Action tool in a batch terminates the loop immediately
    @patch("bot.ollama_client.requests.post")
    def test_action_tool_in_batch_returns_immediately(self, mock_post):
        """Action tool in same batch as data tool returns without extra request."""
        batched = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_projects", "arguments": {}}},
                {"function": {"name": "no_op", "arguments": {"message": "Hi there!"}}},
            ],
        }
        mock_post.return_value = _make_response({"message": batched})

        response, _ = chat("hello", STUB_HANDLERS)
        parsed = parse_llm_response(response["message"]["content"])

        self.assertEqual(parsed["action"], "no_op")
        self.assertEqual(parsed["message"], "Hi there!")
        mock_post.assert_called_once()

    # 15. message field extracted from action tool arguments
    @patch("bot.ollama_client.requests.post")
    def test_action_tool_message_extracted_from_data(self, mock_post):
        """The 'message' key from action args appears in JSON 'message', not 'data'."""
        mock_post.return_value = _make_response({
            "message": _tool_call_msg("update_task", {
                "task_id": 3,
                "fields": {"status": "done"},
                "message": "Marked as done.",
            }),
        })

        response, _ = chat("mark task 3 as done", STUB_HANDLERS)
        parsed = parse_llm_response(response["message"]["content"])

        self.assertEqual(parsed["action"], "update_task")
        self.assertEqual(parsed["data"]["task_id"], 3)
        self.assertEqual(parsed["data"]["fields"]["status"], "done")
        self.assertEqual(parsed["message"], "Marked as done.")
        self.assertNotIn("message", parsed["data"])

    # ------------------------------------------------------------------
    # Empty-content nudge
    # ------------------------------------------------------------------

    # 16. Empty content triggers nudge, then succeeds
    @patch("bot.ollama_client.requests.post")
    def test_empty_content_triggers_nudge_then_succeeds(self, mock_post):
        """Model returns empty content; nudge is sent; second attempt returns JSON."""
        mock_post.side_effect = [
            _make_response({"message": {"role": "assistant", "content": ""}}),
            _make_response({"message": _final_msg(_NO_OP_JSON)}),
        ]

        response, _ = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        self.assertEqual(mock_post.call_count, 2)
        # Verify the nudge was appended as a user message
        second_messages = mock_post.call_args_list[1][1]["json"]["messages"]
        last_user = next(
            m for m in reversed(second_messages) if m.get("role") == "user"
        )
        self.assertIn("action tool", last_user["content"])

    # 17. Content that is only <think> tags triggers the nudge
    @patch("bot.ollama_client.requests.post")
    def test_think_only_content_triggers_nudge(self, mock_post):
        """Content consisting only of <think> tags is treated as empty."""
        mock_post.side_effect = [
            _make_response({"message": {"role": "assistant", "content": "<think>reasoning here</think>"}}),
            _make_response({"message": _final_msg(_NO_OP_JSON)}),
        ]

        response, _ = chat("hello", STUB_HANDLERS)

        self.assertEqual(response["message"]["content"], _NO_OP_JSON)
        self.assertEqual(mock_post.call_count, 2)


# ---------------------------------------------------------------------------
# Tests — mcp_* service functions (unit, no HTTP)
# ---------------------------------------------------------------------------

class MCPToolFunctionTests(SimpleTestCase):

    def _make_user(self):
        user = MagicMock()
        user.username = "testuser"
        return user

    # 18. mcp_get_projects returns JSON list
    def test_mcp_get_projects_returns_json_list(self):
        from bot.services import mcp_get_projects

        p1 = MagicMock(); p1.id = 1; p1.name = "Alpha"; p1.description = "First"
        p2 = MagicMock(); p2.id = 2; p2.name = "Beta";  p2.description = None

        with patch("bot.services.get_user_projects", return_value=[p1, p2]):
            result = mcp_get_projects(self._make_user())

        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "Alpha")
        self.assertEqual(parsed[1]["description"], "")  # None → ""

    # 19. mcp_get_tasks returns project + task object
    def test_mcp_get_tasks_returns_json_object(self):
        from bot.services import mcp_get_tasks

        task = MagicMock()
        task.id = 7; task.title = "Fix the bug"; task.status = "todo"
        task.priority = 2; task.due_date = None

        project = MagicMock()
        project.id = 1; project.name = "Alpha"

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

    # 20. mcp_get_tasks no-match returns error JSON
    def test_mcp_get_tasks_no_match_returns_error_json(self):
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

    # 21. mcp_search_tasks returns matching task list
    def test_mcp_search_tasks_returns_json_list(self):
        from bot.services import mcp_search_tasks

        task = MagicMock()
        task.id = 3; task.title = "Write homepage copy"
        task.status = "todo"; task.priority = 2
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

    # 22. mcp_search_tasks empty returns []
    def test_mcp_search_tasks_empty_returns_empty_list(self):
        from bot.services import mcp_search_tasks

        with (
            patch("bot.services.ProjectMember.objects") as mock_pm,
            patch("bot.services.Task.objects") as mock_task,
        ):
            mock_pm.filter.return_value.values_list.return_value = []
            mock_task.filter.return_value.select_related.return_value = []

            result = mcp_search_tasks(self._make_user(), "xyz")

        self.assertEqual(json.loads(result), [])

    # 23. All three tools return str (Ollama protocol requirement)
    def test_mcp_tool_results_are_strings(self):
        from bot.services import mcp_get_projects, mcp_get_tasks, mcp_search_tasks

        project = MagicMock(); project.id = 1; project.name = "Alpha"; project.description = ""
        task = MagicMock()
        task.id = 1; task.title = "A task"; task.status = "todo"
        task.priority = 3; task.due_date = None; task.project.name = "Alpha"

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

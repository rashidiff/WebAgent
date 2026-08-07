import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from backend import agent, database
from backend.agent import SessionCoordinator, tool_signature
from backend.settings import Settings
from langchain_core.messages import AIMessage


class AgentCoreTests(unittest.TestCase):
    def test_format_dom_includes_rich_metadata(self):
        coordinator = SessionCoordinator(websocket=None)
        text = coordinator.format_dom_for_llm([
            {
                "id": 1,
                "tagName": "INPUT",
                "label": "Email",
                "ariaLabel": "Account email",
                "type": "email",
                "selector": "[data-agent-id=\"1\"]",
                "fingerprint": "abc123",
                "inViewport": True,
            }
        ])

        self.assertIn("label: \"Email\"", text)
        self.assertIn("aria-label: \"Account email\"", text)
        self.assertIn("fingerprint: abc123", text)

    def test_sensitive_action_requires_approval(self):
        coordinator = SessionCoordinator(websocket=None)
        coordinator.current_dom = [
            {
                "selector": "[data-agent-id=\"2\"]",
                "text": "Delete account",
                "tagName": "BUTTON",
            }
        ]

        reason = coordinator.get_approval_reason("click", "[data-agent-id=\"2\"]")

        self.assertIn("Sensitive browser action", reason)

    def test_rank_dom_prioritizes_goal_terms(self):
        coordinator = SessionCoordinator(websocket=None)
        coordinator.current_goal = "search account settings"

        ranked = coordinator.rank_dom_for_goal([
            {"text": "Home", "selector": "a"},
            {"text": "Account settings", "selector": "b"},
        ])

        self.assertEqual(ranked[0]["selector"], "b")

    def test_tool_signature_is_stable_for_arg_order(self):
        first = tool_signature("click_element", {"selector": "a", "value": "b"})
        second = tool_signature("click_element", {"value": "b", "selector": "a"})

        self.assertEqual(first, second)

    def test_final_response_without_tool_call_does_not_hit_step_limit(self):
        coordinator = SessionCoordinator(websocket=None)
        statuses = []

        async def fake_status(message):
            statuses.append(message)

        async def fake_log_message(role, content):
            return None

        async def fake_log_action(action, selector, value, status, detail=""):
            return None

        coordinator.send_status = fake_status
        coordinator.history.log_message = fake_log_message
        coordinator.history.log_action = fake_log_action

        class FakeLLM:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                return AIMessage(content="SUCCESS: Task completed immediately.")

        async def run():
            with patch.object(agent, "get_llm", return_value=FakeLLM()), patch.object(agent, "DEFAULT_MAX_AGENT_STEPS", 1):
                await agent.run_browser_agent(coordinator, "done", [])

        asyncio.run(run())
        self.assertIn("SUCCESS: Task completed immediately.", statuses)
        self.assertNotIn("ERROR: Reached maximum execution limit of 1 steps without completion.", statuses)

    def test_multiple_tool_calls_consume_step_budget_per_execution(self):
        coordinator = SessionCoordinator(websocket=None)
        statuses = []
        action_calls = []

        async def fake_status(message):
            statuses.append(message)

        async def fake_execute_action(action_name, selector=None, value=None):
            action_calls.append((action_name, selector, value))
            return "Success: Action executed. Current webpage interactive elements:\n[Empty page or no interactive elements found]"

        async def fake_log_message(role, content):
            return None

        async def fake_log_action(action, selector, value, status, detail=""):
            return None

        coordinator.send_status = fake_status
        coordinator.execute_action = fake_execute_action
        coordinator.history.log_message = fake_log_message
        coordinator.history.log_action = fake_log_action

        first_response = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "go_back", "arguments": "{}"}, "type": "function"},
                    {"id": "call-2", "function": {"name": "go_forward", "arguments": "{}"}, "type": "function"},
                ]
            },
        )
        final_response = AIMessage(content="SUCCESS: Done after two actions.")

        class FakeLLM:
            def __init__(self):
                self.responses = [first_response, final_response]

            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                return self.responses.pop(0)

        async def run():
            with patch.object(agent, "get_llm", return_value=FakeLLM()), patch.object(agent, "DEFAULT_MAX_AGENT_STEPS", 2):
                await agent.run_browser_agent(coordinator, "two actions", [])

        asyncio.run(run())
        self.assertEqual(action_calls, [("back", None, None), ("forward", None, None)])
        self.assertIn("PLAN: Step 1/2: go_back with {}. Verifying after execution.", statuses)
        self.assertIn("PLAN: Step 2/2: go_forward with {}. Verifying after execution.", statuses)
        self.assertIn("SUCCESS: Done after two actions.", statuses)

    def test_limit_message_uses_configured_step_budget(self):
        coordinator = SessionCoordinator(websocket=None)
        statuses = []

        async def fake_status(message):
            statuses.append(message)

        async def fake_log_message(role, content):
            return None

        async def fake_log_action(action, selector, value, status, detail=""):
            return None

        coordinator.send_status = fake_status
        coordinator.history.log_message = fake_log_message
        coordinator.history.log_action = fake_log_action

        repeated_tool_response = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "go_back", "arguments": "{}"}, "type": "function"},
                    {"id": "call-2", "function": {"name": "go_forward", "arguments": "{}"}, "type": "function"},
                ]
            },
        )

        class FakeLLM:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                return repeated_tool_response

        async def run():
            with patch.object(agent, "get_llm", return_value=FakeLLM()), patch.object(agent, "DEFAULT_MAX_AGENT_STEPS", 1):
                await agent.run_browser_agent(coordinator, "limit", [])

        asyncio.run(run())
        self.assertIn("PLAN: Step 1/1: go_back with {}. Verifying after execution.", statuses)
        self.assertIn("ERROR: Reached maximum execution limit of 1 steps without completion.", statuses)


class DatabaseTests(unittest.TestCase):
    def test_history_store_persists_session_message_and_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = os.path.join(tmp, "history.db")
            database.init_db()
            store = database.HistoryStore()

            async def run_history_flow():
                await store.start_session()
                await store.log_message("user", "hello")
                await store.log_action("click", "[data-agent-id=\"1\"]", None, "success")
                await store.end_session()

            asyncio.run(run_history_flow())
            history = database.get_session_history(store.session_id)

            self.assertEqual(history["messages"][0]["content"], "hello")
            self.assertEqual(history["actions"][0]["action"], "click")

    def test_list_sessions_supports_limit_and_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = os.path.join(tmp, "history.db")
            database.init_db()

            async def seed_sessions():
                for _ in range(3):
                    store = database.HistoryStore()
                    await store.start_session()
                    await store.end_session()

            asyncio.run(seed_sessions())
            page = database.list_sessions(limit=1, offset=1)

            self.assertEqual(len(page), 1)
            self.assertEqual(database.count_sessions(), 3)

    def test_get_session_history_includes_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = os.path.join(tmp, "history.db")
            database.init_db()
            store = database.HistoryStore()

            async def seed_history():
                await store.start_session()
                await store.log_message("user", "hello")
                await store.end_session()

            asyncio.run(seed_history())
            history = database.get_session_history(store.session_id)

            self.assertEqual(history["session_id"], store.session_id)


class SettingsTests(unittest.TestCase):
    def test_settings_split_cors_origins(self):
        settings = Settings(CORS_ALLOWED_ORIGINS="http://localhost:3000, https://example.com")

        self.assertEqual(
            settings.cors_origins,
            ["http://localhost:3000", "https://example.com"],
        )


if __name__ == "__main__":
    unittest.main()

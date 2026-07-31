import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
from agent import SessionCoordinator, tool_signature


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


if __name__ == "__main__":
    unittest.main()

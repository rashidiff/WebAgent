import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from backend import agent, database


class BudgetStatusTests(unittest.TestCase):
    def test_exhaustion_persists_before_terminal_ui_status(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(database, 'DB_PATH', str(Path(tmp) / 'history.db')):
            database.init_db()
            coordinator = agent.SessionCoordinator(websocket=None)
            coordinator.is_running = True
            actions = []
            statuses = []

            async def execute(action, selector=None, value=None):
                actions.append(action)
                return 'Success: Action executed.'

            async def status(message):
                statuses.append(message)
                if message.startswith('ERROR:'):
                    run = database.get_run(coordinator.current_run_id)
                    self.assertEqual(run['status'], 'error')
                    self.assertIsNotNone(run['ended_at'])
                    self.assertEqual(run['steps'][-1]['detail'], message)

            coordinator.execute_action = execute
            coordinator.send_status = status

            class Model:
                def bind_tools(self, tools):
                    return self

                async def ainvoke(self, messages):
                    return AIMessage(content='', tool_calls=[
                        {'name': 'go_back', 'args': {}, 'id': 'one'},
                        {'name': 'go_forward', 'args': {}, 'id': 'two'},
                    ])

            async def run():
                await coordinator.history.start_session()
                with patch.object(agent, 'get_llm', return_value=Model()), patch.object(agent, 'DEFAULT_MAX_AGENT_STEPS', 1):
                    await agent.run_browser_agent(coordinator, 'exercise budget', [])

            asyncio.run(run())
            saved = database.get_run(coordinator.current_run_id)
            terminal = saved['steps'][-1]
            self.assertEqual(terminal['event_type'], 'ERROR')
            self.assertEqual(terminal['metadata'], {'reason': 'budget_exhausted', 'max_steps': 1, 'executed_steps': 1})
            self.assertIn('limit of 1 steps', terminal['detail'])
            self.assertEqual(actions, ['back'])
            self.assertFalse(coordinator.is_running)
            self.assertEqual(statuses[-1], terminal['detail'])
            self.assertEqual(database.get_session_history(coordinator.history.session_id)['messages'][-1]['content'], terminal['detail'])


if __name__ == '__main__':
    unittest.main()

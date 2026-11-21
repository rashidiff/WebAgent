import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend import database, main
from backend.agent import SessionCoordinator
from backend.schemas import ActionResultEvent


class ScreenshotHandoffTests(unittest.TestCase):
    def test_extension_websocket_payload_is_saved_and_exported(self):
        root = Path(__file__).resolve().parents[2]
        raw = subprocess.check_output(['node', 'extension/tests/screenshots.test.cjs', '--payload'], cwd=root, text=True)
        packet = ActionResultEvent.model_validate_json(raw).model_dump()
        with tempfile.TemporaryDirectory() as tmp, patch.object(database, 'DB_PATH', str(Path(tmp) / 'history.db')):
            database.init_db()
            coordinator = SessionCoordinator(websocket=None)
            coordinator.current_run_id = 'r'

            class Socket:
                async def send_json(self, event):
                    result = dict(packet, action_id=event['action_id'], capability_token=event['capability_token'])
                    await coordinator.response_queue.put(result)

            async def run():
                await coordinator.history.start_session()
                database.create_run('r', 'synthetic screenshots', coordinator.history.session_id)
                coordinator.websocket = Socket()
                self.assertIn('Success:', await coordinator.execute_action('get_text'))

            asyncio.run(run())
            saved = database.get_run('r')
            step = saved['steps'][-1]
            self.assertEqual(step['screenshot_before'], packet['screenshot_before'])
            self.assertEqual(step['screenshot_after'], packet['screenshot_after'])
            html = main.render_run_html(saved)
            self.assertEqual(html.count(packet['screenshot_before']), 2)
            self.assertIn('Before', html)
            self.assertIn('After', html)
            self.assertLess(len(raw.encode('utf-8')), main.settings.max_websocket_message_bytes)


if __name__ == '__main__':
    unittest.main()

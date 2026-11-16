import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import launcher
from backend.settings import Settings


class LauncherTests(unittest.TestCase):
    def settings(self, **values):
        return Settings(_env_file=None, **dict({'LLM_PROVIDER': 'gemini', 'HOST': '127.0.0.1', 'PORT': 8000, 'AUTH_REQUIRED': False, 'GEMINI_API_KEY': 'test-key', 'GOOGLE_API_KEY': '', 'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': '', 'DEEPSEEK_API_KEY': '', 'LLM_MODEL_NAME': 'test-model'}, **values))

    def test_missing_and_placeholder_provider_keys(self):
        for provider in ('gemini', 'openai', 'anthropic', 'deepseek'):
            settings = self.settings(LLM_PROVIDER=provider, GEMINI_API_KEY='', GOOGLE_API_KEY='')
            self.assertTrue(launcher.configuration_errors(settings))
        self.assertTrue(launcher.configuration_errors(self.settings(GEMINI_API_KEY='your_key_here')))
        self.assertFalse(launcher.configuration_errors(self.settings(GEMINI_API_KEY='', GOOGLE_API_KEY='test-key')))

    def test_security_and_port_validation(self):
        self.assertTrue(launcher.configuration_errors(self.settings(PORT=0)))
        self.assertTrue(launcher.configuration_errors(self.settings(AUTH_REQUIRED=True)))
        self.assertTrue(launcher.configuration_errors(self.settings(HOST='0.0.0.0')))

    def test_repeat_configuration_preserves_file_and_skips_editor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / '.env'
            content = 'LLM_PROVIDER=gemini\nLLM_MODEL_NAME=test\nGEMINI_API_KEY=test-key\n'
            path.write_text(content)
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {}, clear=True), patch.object(launcher.subprocess, 'run') as editor:
                self.assertEqual(launcher.configure().gemini_api_key, 'test-key')
                editor.assert_not_called()
                self.assertEqual(path.read_text(), content)

    def test_first_run_edits_template_then_reloads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env.example').write_text('LLM_PROVIDER=gemini\nGEMINI_API_KEY=\n')
            def edit(*args, **kwargs):
                (root / '.env').write_text('LLM_PROVIDER=gemini\nLLM_MODEL_NAME=test\nGEMINI_API_KEY=test-key\n')
            with patch.object(launcher, 'ROOT', root), patch.dict(os.environ, {}, clear=True), patch.object(launcher.subprocess, 'run', side_effect=edit) as editor, patch('builtins.input', return_value=''):
                self.assertEqual(launcher.configure().gemini_api_key, 'test-key')
                editor.assert_called_once()

    def test_occupied_port_does_not_start_server(self):
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with patch.object(launcher.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(RuntimeError, 'Cannot bind'):
                    launcher.launch(self.settings(PORT=listener.getsockname()[1]), True)
                start.assert_not_called()

    def test_startup_failure_is_reported(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(launcher.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'before becoming ready'):
                launcher.launch(self.settings(PORT=0), True)


if __name__ == '__main__':
    unittest.main()

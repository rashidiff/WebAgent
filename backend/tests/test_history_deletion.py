from contextlib import closing
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import database as db


class HistoryDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'existing.db')
        self.patcher = patch.object(db, 'DB_PATH', self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        db.init_db()
        self.first = self.seed('first')
        self.second = self.seed('second')
        db.create_run('standalone', 'keep')

    def seed(self, name):
        store = db.HistoryStore()
        async def populate():
            await store.start_session()
            await store.log_message('user', name)
            await store.log_action('click', 'button', None, 'success')
        asyncio.run(populate())
        db.create_run(name, name, store.session_id)
        db.add_run_step(name, 'ACTION', 'click')
        workflow = db.create_workflow(name, name, [{'action': 'click'}], name)
        return store.session_id, workflow['id']

    def assert_removed(self, name, item):
        session, workflow = item
        self.assertIsNone(db.get_run(name))
        self.assertEqual(db.get_session_history(session)['messages'], [])
        self.assertEqual(db.get_session_history(session)['actions'], [])
        saved = db.get_workflow(workflow)
        self.assertIsNone(saved['source_run_id'])
        self.assertEqual(saved['steps'], [{'action': 'click'}])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM run_steps WHERE run_id=?', (name,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_delete_one_on_reopened_existing_schema(self):
        db.init_db()  # Existing databases use the same FK policy; no schema migration needed.
        self.assertTrue(db.delete_session(self.first[0]))
        self.assert_removed('first', self.first)
        self.assertIsNotNone(db.get_run('second'))
        self.assertIsNotNone(db.get_run('standalone'))
        self.assertFalse(db.delete_session(self.first[0]))

    def test_clear_all_preserves_workflows_and_standalone_runs(self):
        self.assertEqual(db.clear_sessions(), 2)
        self.assert_removed('first', self.first)
        self.assert_removed('second', self.second)
        self.assertIsNotNone(db.get_run('standalone'))
        self.assertEqual(db.clear_sessions(), 0)

    def test_failed_deletion_rolls_back_entire_transaction(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        for operation in (lambda: db.delete_session(self.first[0]), db.clear_sessions):
            with self.assertRaises(sqlite3.IntegrityError):
                operation()
            self.assertIsNotNone(db.get_run('first'))
            self.assertEqual(len(db.get_run('first')['steps']), 1)
            self.assertEqual(db.get_workflow(self.first[1])['source_run_id'], 'first')
            self.assertEqual(len(db.get_session_history(self.first[0])['messages']), 1)


if __name__ == '__main__':
    unittest.main()

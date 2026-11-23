import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import store


class JournalRecovery(unittest.TestCase):
    def test_append_preserves_torn_bytes_and_remains_readable_after_restart(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, SCANGOAL_DATA_DIR=folder):
            path = Path(folder, 'messages.jsonl')
            base = {'message_id': 1, 'text': 'A'}
            store.append_versions([base])
            path.write_bytes(path.read_bytes() + b'{"message_id":2,\xc3')
            damaged = path.read_bytes()
            self.assertEqual(store.load_seen(path)[1], 1)
            new = {'message_id': 2, 'text': 'B'}
            self.assertEqual(store.append_versions([new]), 1)
            self.assertTrue(path.read_bytes().startswith(damaged))
            self.assertEqual([row['text'] for _, row in store.message_rows(path)], ['A', 'B'])
            self.assertEqual(store.append_versions([new]), 0)
            self.assertEqual(store.load_seen(path)[1], 2)

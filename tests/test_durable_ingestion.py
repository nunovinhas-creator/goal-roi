import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scangoal import durable_journal, ingestion, reader, store
from scangoal.publication import Publisher


class DiskJournal:
    """The simulated external service lives outside either runner's disk."""

    def __init__(self, path, mode):
        self.path, self.mode = path, mode

    def read(self, key):
        state = json.loads(self.path.read_bytes()) if self.path.exists() else {}
        return tuple(state.get(key, (0, None)))

    def write(self, key, revision, value):
        state = json.loads(self.path.read_bytes()) if self.path.exists() else {}
        assert state.get(key, [0])[0] == revision
        if self.mode == 'unavailable' and value.get('phase') == 'persisted':
            raise durable_journal.DurabilityError('simulated journal outage')
        state[key] = (revision + 1, value)
        pending = self.path.with_suffix('.tmp')
        pending.write_text(json.dumps(state))
        pending.replace(self.path)
        if self.mode == 'persisted' and key == 'ingestion' and value['phase'] == 'persisted':
            os.kill(os.getpid(), signal.SIGKILL)
        return revision + 1


class Telegram:
    def __init__(self, mode):
        self.mode = mode

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def get_entity(self, entity):
        return entity

    async def iter_messages(self, entity, limit, min_id=0, offset_id=999):
        assert self.mode != 'no-telegram', 'a durable receipt should be recovered without Telegram'
        latest = (103, 'available after interruption') if self.mode == 'available' else (102, 'transient original edit')
        for mid, text in (latest, (101, 'previously published')):
            if min_id < mid < offset_id:
                yield SimpleNamespace(id=mid, raw_text=text, date=datetime.now(timezone.utc), out=False)
                if self.mode == 'receiving':
                    os.kill(os.getpid(), signal.SIGKILL)


def child(mode, ledger_path):
    with patch.object(durable_journal, 'configured', return_value=DiskJournal(ledger_path, mode)), \
         patch.object(reader, 'make_client', return_value=Telegram(mode)):
        try:
            result = asyncio.run(reader.collect())
        except durable_journal.DurabilityError as exc:
            print('SCANGOAL_DURABILITY_FAILED', exc)
            return 3
        if mode in ('available', 'no-telegram'):
            Publisher().publish()
        return result


class DurableIngestionTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote, self.lost = self.temp / 'origin.git', self.temp / 'runner-lost'
        self.second, self.external = self.temp / 'runner-new', self.temp / 'journal.json'
        self.git(self.temp, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.temp, 'clone', str(self.remote), str(self.lost))
        (self.lost / 'data').mkdir()
        stamp = datetime.now(timezone.utc).isoformat()
        old = {'message_id': 101, 'text': 'previously published', 'collected_at_utc': stamp, 'date_utc': stamp}
        with patch.dict(os.environ, SCANGOAL_DATA_DIR=str(self.lost / 'data')):
            store.append_versions([old])
        self.old_state = {'last_run_utc': stamp}
        (self.lost / 'data/state.json').write_text(json.dumps(self.old_state))
        self.git(self.lost, 'add', 'data')
        self.git(self.lost, 'commit', '-m', 'seed')
        self.git(self.lost, 'push', 'origin', 'main')

    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                               'user.email=test@example.invalid', *args], check=True, text=True,
                              capture_output=True, timeout=10).stdout.strip()

    def run_reader(self, runner, mode):
        return subprocess.run([sys.executable, __file__, mode, str(self.external)], cwd=runner,
                              env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                                       SCANGOAL_DATA_DIR=str(runner / 'data'), TG_SCANGOAL='synthetic',
                                       GOAL_ROI_PUBLICATION_DIR=str(runner / '.spool'),
                                       GITHUB_STEP_SUMMARY=str(self.temp / 'step-summary')),
                              text=True, capture_output=True, timeout=30)

    def fresh_runner(self):
        shutil.rmtree(self.lost)
        self.git(self.temp, 'clone', str(self.remote), str(self.second))

    def published(self, name):
        return self.git(self.remote, 'show', 'main:data/' + name)

    def test_capture_survives_sigkill_before_local_append_and_source_disappears(self):
        self.assertEqual(self.run_reader(self.lost, 'persisted').returncode, -signal.SIGKILL)
        self.assertEqual(json.loads(self.published('messages.jsonl'))['message_id'], 101)
        self.fresh_runner()
        result = self.run_reader(self.second, 'no-telegram')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in self.published('messages.jsonl').splitlines()]
        self.assertEqual([(row['message_id'], row['text']) for row in rows],
                         [(101, 'previously published'), (102, 'transient original edit')])
        self.assertEqual(DiskJournal(self.external, '').read('ingestion')[1]['phase'], 'confirmed')

    def test_interrupted_unrecorded_version_persists_a_gap_and_recovers_available_messages(self):
        self.assertEqual(self.run_reader(self.lost, 'receiving').returncode, -signal.SIGKILL)
        unconfirmed = DiskJournal(self.external, '').read('ingestion')[1]
        self.assertEqual(unconfirmed['phase'], 'receiving')
        self.fresh_runner()
        result = self.run_reader(self.second, 'available')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in self.published('messages.jsonl').splitlines()]
        self.assertEqual([row['message_id'] for row in rows], [101, 103])
        marker = json.loads(self.published(ingestion.CHECKPOINT))
        self.assertEqual([(gap['id'], gap['reason'], gap['status']) for gap in marker['gaps']],
                         [(unconfirmed['id'], 'interrupted_read', 'source_history_unverified')])
        self.assertIn('SCANGOAL_INGESTION_GAP', result.stdout)
        self.assertIn(unconfirmed['id'], (self.temp / 'step-summary').read_text())

    def test_unavailable_durable_storage_leaves_local_ingestion_progress_unchanged(self):
        result = self.run_reader(self.lost, 'unavailable')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('SCANGOAL_DURABILITY_FAILED', result.stdout)
        self.assertEqual(self.old_state, json.loads((self.lost / 'data/state.json').read_bytes()))
        self.assertEqual(json.loads((self.lost / 'data/messages.jsonl').read_bytes())['message_id'], 101)
        self.assertFalse((self.lost / 'data' / ingestion.CHECKPOINT).exists())


if __name__ == '__main__':
    sys.exit(child(sys.argv[1], Path(sys.argv[2])))

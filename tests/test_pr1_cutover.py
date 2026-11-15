"""Offline tests for scripts/pr1_cutover.py on synthetic git repositories (no real data, network or sender)."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scangoal import notify

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pr1_cutover', ROOT / 'scripts' / 'pr1_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)

NOW = datetime.fromisoformat('2026-11-15T10:00:00+00:00')   # before the 21:00 UTC summary


def legacy_state():
    return {'hello': True, 'statuses': dict({f'h{i}': 'promissora' for i in range(33)}, a0='abandonada'),
            'watch_sent': ['2026-11-14T20:00:00+00:00|A — B|Over 2.5'], 'sent_total': 259,
            'summary_day': '2026-11-14', 'last_run_utc': '2026-11-15T08:33:20+00:00',
            'regime': {'OPEN_GAME': 'estável'}, 'custom_setting': {'preserve': True}}


def legacy_report():
    return {'generated_at_utc': '2026-11-15T08:33:20+00:00',
            'brain': {'status_list': dict(legacy_state()['statuses']), 'counts': {'promissora': 33}}}


def eligible_report(**extra):
    rep = {'generated_at_utc': NOW.isoformat(),
           'brain': {'evidence_scope': 'eligible_bets_only', 'hypotheses': 40,
                     'status_list': dict({f'h{i}': 'promissora' for i in range(8)}, a0='abandonada'),
                     'counts': {'promissora': 8, 'abandonada': 1}, 'exploratory': {'counts': {'promissora': 33}},
                     'eligibility': {'total': 10, 'eligible': 8, 'excluded': 2}, 'watch': [],
                     'walk_forward': {'followed': {'bets': 1}, 'skipped': {'bets': 2}}},
           'operational': {'scope': 'eligible_bets_only', 'regime': []}}
    rep.update(extra)
    return rep


def build_ok(csv_path=None, register=False):
    assert csv_path is None and register is False
    return eligible_report()


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@invalid',
                    '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, text=True)


def rev(root, ref='HEAD'):
    return subprocess.run(['git', '-C', str(root), 'rev-parse', ref], check=True, capture_output=True, text=True).stdout.strip()


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + '\n',
                    encoding='utf-8')


@unittest.skipUnless(shutil.which('git'), 'git required')
class Cutover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q')
        write(self.root, 'data/.gitkeep', '')
        write(self.root, 'data/notify_state.json', legacy_state())
        write(self.root, 'data/report.json', legacy_report())
        write(self.root, 'data/hypotheses.json', {'h0': {'family': 'Jogo aberto', 'conditions': [], 'source': 'gerador'}})
        write(self.root, 'data/messages.jsonl', '{"message_id": 1}\n')
        write(self.root, 'docs/report.json', legacy_report())
        write(self.root, 'docs/bets.csv', 'data;resultado\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'data: collect')
        self.main = rev(self.root)
        write(self.root, 'scangoal_code.txt', 'PR1 code\n')    # integration commit: code only, same data
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'merge PR1')
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop('SCANGOAL_DATA_DIR', None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def files(self):
        return cut.digests(cut.contents(self.root))

    def refused(self, message, build=build_ok, frozen=None):
        before = self.files()
        with self.assertRaises(cut.CutoverError) as ctx:
            cut.cutover(self.root, frozen or self.main, NOW, build)
        self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.files(), before, 'a refused cutover must leave every file byte-identical')

    def test_cutover_33_to_8_silent_idempotent_and_preserving_history(self):
        result = cut.cutover(self.root, self.main, NOW, build_ok)
        self.assertTrue(result['ok'], result['checks'])
        self.assertFalse(result['restored'])
        self.assertEqual(result['frozen_main_sha'], self.main)
        self.assertEqual(result['checks']['first_call'], 'NOTIFY_STATUS_MIGRATED')
        self.assertEqual(result['checks']['second_call'], 'NOTIFY_STATUS_UNCHANGED')
        self.assertEqual(result['checks']['next_cycle_status_transitions'], 0)
        self.assertEqual(result['reference'], {'previous': 34, 'baseline': 9, 'silenced_transitions': 25})
        self.assertEqual(result['changed_files'], ['data/notify_state.json', 'data/report.json', 'docs/report.json'])
        state = json.loads((self.root / 'data/notify_state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['statuses'], eligible_report()['brain']['status_list'])
        marker = state['migrations'][notify.STATUS_MIGRATION]
        self.assertEqual(marker['previous_statuses'], legacy_state()['statuses'])
        for key, value in legacy_state().items():
            if key != 'statuses':
                self.assertEqual(state[key], value, key)
        self.assertNotIn('operational_regime', state)   # seeded silently by the first real cycle, not by the cutover
        self.assertEqual(json.loads((self.root / 'data/report.json').read_text(encoding='utf-8')), eligible_report())
        self.assertEqual((self.root / 'docs/report.json').read_bytes(), (self.root / 'data/report.json').read_bytes())
        # The next ordinary cycle on the persisted state sends nothing for the historical reclassification.
        self.assertEqual(notify.messages(eligible_report(), copy.deepcopy(state), NOW), [])

    def test_rerun_after_committed_cutover_cannot_reset_baseline(self):
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        git(self.root, 'commit', '-q', '-am', 'data: cutover')
        self.refused('migration marker already present', frozen='HEAD')

    def test_uncommitted_or_untracked_data_refused(self):
        write(self.root, 'data/state.json', {'last_run_utc': 'x'})
        self.refused('uncommitted changes')
        (self.root / 'data/state.json').unlink()
        write(self.root, 'docs/report.json', {'stale': True})
        self.refused('uncommitted changes')

    def test_stale_frozen_snapshot_refused(self):
        # A newer collector commit after the SHA the operator froze: never cut over on the older snapshot.
        state = legacy_state()
        state['sent_total'] += 1
        write(self.root, 'data/notify_state.json', state)
        git(self.root, 'commit', '-q', '-am', 'data: collect (newer)')
        self.refused('data differs from frozen main')

    def test_frozen_commit_must_be_an_ancestor(self):
        write(self.root, 'data/messages.jsonl', '{"message_id": 2}\n')
        git(self.root, 'commit', '-q', '-am', 'data: collect (future)')
        future = rev(self.root)
        git(self.root, 'checkout', '-q', 'HEAD~1')
        self.refused('not an ancestor', frozen=future)
        self.refused('unknown frozen main commit', frozen='0' * 40)

    def test_corrupt_or_missing_state_refused(self):
        write(self.root, 'data/notify_state.json', '{"statuses": ')
        git(self.root, 'commit', '-q', '-am', 'corrupt')
        self.refused('missing or invalid', frozen='HEAD')
        (self.root / 'data/notify_state.json').unlink()
        git(self.root, 'commit', '-q', '-am', 'missing')
        self.refused('missing or invalid', frozen='HEAD')

    def test_report_without_eligible_scope_refused_and_nothing_written(self):
        self.refused('no valid eligible status_list', build=lambda csv_path=None, register=False: legacy_report())
        bad = eligible_report()
        bad['brain']['status_list']['h0'] = 'excelente'
        self.refused('no valid eligible status_list', build=lambda csv_path=None, register=False: bad)

    def test_network_attempt_aborts_and_restores(self):
        def build_net(csv_path=None, register=False):
            (self.root / 'data/report.json').write_text('partial', encoding='utf-8')
            socket.create_connection(('192.0.2.1', 9), timeout=1)
            return eligible_report()
        self.refused('network access attempted', build=build_net)

    def test_failed_post_check_restores_every_file(self):
        def build_register(csv_path=None, register=False):
            write(self.root, 'data/hypotheses.json', {'h0': {}, 'new': {}})   # a build that grows the registry
            write(self.root, 'data/extra.json', {'unexpected': True})
            return eligible_report()
        before = self.files()
        result = cut.cutover(self.root, self.main, NOW, build_register)
        self.assertFalse(result['ok'])
        self.assertTrue(result['restored'])
        self.assertFalse(result['checks']['registry_unchanged'])
        self.assertFalse(result['checks']['only_allowed_files_changed'])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.root / 'data/extra.json').exists())

    def test_data_dir_pointing_elsewhere_refused(self):
        os.environ['SCANGOAL_DATA_DIR'] = str(self.root / 'elsewhere')
        self.refused('outside the checkout')

    def test_cli_usage_and_refusal_exit_codes(self):
        with patch('builtins.print'):
            self.assertEqual(cut.main([]), 2)
            with patch.object(cut, 'ROOT', self.root):
                write(self.root, 'data/state.json', {})
                self.assertEqual(cut.main([self.main]), 1)


if __name__ == '__main__':
    unittest.main()

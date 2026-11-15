"""Regressões offline de scripts/post_cutover_check.py (verificação de leitura da fase D do corte da PR 3)."""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import post_cutover_check as pcc  # noqa: E402

MARKERS = {'eligible_statuses_v1': {'at': '2026-11-13T10:00:00+00:00'},
           'status_rules:independent_games_hysteresis_v1': {'at': '2026-11-14T10:00:00+00:00'},
           'status_rules:chronological_eprocess_v3': {'at': '2026-11-16T02:30:00+00:00'}}
STATUS_LIST = {'h1': 'promissora', 'h2': 'abandonada'}


def state(sent_total=10, watch=3, statuses=None, migrations=None):
    return {'migrations': copy.deepcopy(MARKERS if migrations is None else migrations), 'sent_total': sent_total,
            'watch_sent': ['w%d' % i for i in range(watch)],
            'statuses': dict(statuses if statuses is not None else dict(STATUS_LIST, h3='em observação'))}


def report(status_list=None, rules='chronological_eprocess_v3', validation=None):
    return {'brain': {'status_rules': rules, 'status_list': dict(STATUS_LIST if status_list is None else status_list),
                      'validation': {'state': 'not_activated', 'certified': [], 'ledger_valid': False} if validation is None else validation}}


class PostCutoverCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        (self.root / 'data').mkdir()
        self.before = self.commit(state(), report())

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, notify_state, rep, ledger=False):
        (self.root / 'data' / 'notify_state.json').write_text(json.dumps(notify_state), encoding='utf-8')
        (self.root / 'data' / 'report.json').write_text(json.dumps(rep), encoding='utf-8')
        led = self.root / 'data' / 'certification_ledger.json'
        if ledger:
            led.write_text('{}', encoding='utf-8')
        elif led.exists():
            led.unlink()
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'c')
        return self.git('rev-parse', 'HEAD')

    def run_check(self, after):
        out = io.StringIO()
        with redirect_stdout(out):
            code = pcc.main([self.before, after], root=self.root)
        return code, json.loads(out.getvalue())

    def failed(self, after):
        code, res = self.run_check(after)
        self.assertEqual(code, 1)
        self.assertFalse(res['ok'])
        return [k for k, v in res['checks'].items() if not v]

    def test_normal_run_passes_and_reports_real_changes(self):
        sl = dict(STATUS_LIST, h2='em observação')
        sl.pop('h2')
        after = self.commit(state(sent_total=12, watch=4, statuses=dict(sl, h2='em observação')), report(status_list=sl))
        code, res = self.run_check(after)
        self.assertEqual(code, 0, res)
        self.assertEqual(res['sent_in_run'], 2)
        self.assertEqual(res['status_list_changes'], {'h2': ['abandonada', None]})

    def test_identical_commits_pass(self):
        code, res = self.run_check(self.before)
        self.assertEqual(code, 0, res)
        self.assertEqual(res['status_list_changes'], {})

    def test_marker_changed_or_missing_fails(self):
        changed = copy.deepcopy(MARKERS)
        changed['status_rules:independent_games_hysteresis_v1'] = {'at': 'rewritten'}
        self.assertEqual(self.failed(self.commit(state(migrations=changed), report())), ['markers_unchanged'])
        missing = {k: v for k, v in MARKERS.items() if k != 'status_rules:chronological_eprocess_v3'}
        self.assertEqual(self.failed(self.commit(state(migrations=missing), report())), ['markers_unchanged'])

    def test_delivery_history_must_not_shrink(self):
        self.assertEqual(self.failed(self.commit(state(sent_total=9), report())), ['delivery_monotonic'])
        self.assertEqual(self.failed(self.commit(state(watch=2), report())), ['delivery_monotonic'])

    def test_old_rules_fail(self):
        self.assertEqual(self.failed(self.commit(state(), report(rules='independent_games_hysteresis_v1'))), ['status_rules'])

    def test_any_sign_of_certification_fails(self):
        for validation in ({'state': 'active'}, {'state': 'not_activated', 'certified': ['h1']},
                           {'state': 'not_activated', 'ledger_valid': True}, {}):
            with self.subTest(validation=validation):
                self.assertIn('certification_off', self.failed(self.commit(state(), report(validation=validation))))
        self.assertEqual(self.failed(self.commit(state(), report(), ledger=True)), ['certification_off'])

    def test_validated_status_fails(self):
        sl = dict(STATUS_LIST, h1='forte')
        failed = self.failed(self.commit(state(statuses=sl), report(status_list=sl)))
        self.assertEqual(failed, ['no_validated_statuses'])

    def test_telegram_reference_must_follow_the_report(self):
        sl = dict(STATUS_LIST, h1='abandonada')
        self.assertEqual(self.failed(self.commit(state(), report(status_list=sl))), ['statuses_follow_report'])

    def test_unreadable_files_and_bad_usage_exit_2(self):
        (self.root / 'data' / 'report.json').write_text('{not json', encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'broken')
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(pcc.main([self.before, 'HEAD'], root=self.root), 2)
            self.assertEqual(pcc.main([self.before, 'no-such-commit'], root=self.root), 2)
            self.assertEqual(pcc.main([self.before], root=self.root), 2)

    def test_read_only(self):
        files = {p: p.read_bytes() for p in (self.root / 'data').iterdir()}
        head, status = self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain')
        self.run_check(self.before)
        self.assertEqual({p: p.read_bytes() for p in (self.root / 'data').iterdir()}, files)
        self.assertEqual((self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain')), (head, status))


if __name__ == '__main__':
    unittest.main()

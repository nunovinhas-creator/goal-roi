"""Offline tests for scripts/pr2_cutover.py on synthetic git repositories (no real data, network or sender)."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch

from scangoal import brain, notify

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pr2_cutover', ROOT / 'scripts' / 'pr2_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)

NOW = datetime.fromisoformat('2026-11-16T03:00:00+00:00')   # inside the low-activity window, before the 21:00 summary
PR1_STATUSES = dict({'k%d' % i: 'promissora' for i in range(15)}, **{'a%d' % i: 'abandonada' for i in range(5)})
PR2_STATUSES = dict({'k%d' % i: 'promissora' for i in range(11)}, n0='promissora', n1='promissora')   # 15/5 -> 13/0


def production_state():
    """notify_state as left by PR1 in production: PR1 marker, PR1 statuses, delivery history, operational regime."""
    return {'hello': True, 'statuses': dict(PR1_STATUSES), 'watch_sent': ['2026-11-15T09:52:04+00:00|Raj Pracha — VRN Muangnon|Over 6'],
            'sent_total': 269, 'summary_day': '2026-11-14', 'last_run_utc': '2026-11-15T10:08:47+00:00',
            'regime': {'OPEN_GAME': 'estável'}, 'operational_regime': {'OPEN_GAME': 'estável', 'POLYMARKET': 'poucos dados'},
            'migrations': {notify.STATUS_MIGRATION: {'at_utc': '2026-11-15T09:27:59+00:00', 'previous_statuses': {'x': 'promissora'},
                                                     'baseline_statuses': {'k0': 'promissora'}, 'report_generated_at_utc': '2026-11-15T09:28:04+00:00'}}}


def pr1_report():
    return {'generated_at_utc': '2026-11-15T10:08:47+00:00', 'brain': {'evidence_scope': 'eligible_bets_only', 'status_list': dict(PR1_STATUSES)},
            'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]}}


def pr2_report(statuses=None, **brain_extra):
    rep = {'generated_at_utc': NOW.isoformat(),
           'brain': dict({'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES, 'hypotheses': 30, 'tested': 20,
                          'status_list': dict(PR2_STATUSES if statuses is None else statuses), 'counts': {'promissora': 13},
                          'exploratory': {'counts': {'promissora': 48}}, 'eligibility': {'eligible': 10, 'total': 12},
                          'walk_forward': {'followed': {}, 'skipped': {}}, 'watch': []}, **brain_extra),
           'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]}}
    return rep


def build_ok(csv_path=None, register=False):
    assert csv_path is None
    if register:   # like the production report step: grows the registry in whatever data dir is active
        path = Path(os.environ['SCANGOAL_DATA_DIR']) / 'hypotheses.json'
        reg = json.loads(path.read_text(encoding='utf-8'))
        reg['grown-by-register-true'] = {'family': 'Jogo aberto', 'conditions': []}
        path.write_text(json.dumps(reg), encoding='utf-8')
    return pr2_report()


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@invalid', '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, text=True)


def rev(root, ref='HEAD'):
    return subprocess.run(['git', '-C', str(root), 'rev-parse', ref], check=True, capture_output=True, text=True).stdout.strip()


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')


def kinds(messages):
    return {k: sum(1 for m in messages if cut.kind(m) == k) for k in {cut.kind(m) for m in messages}}


@unittest.skipUnless(shutil.which('git'), 'git required')
class PR2Cutover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q')
        write(self.root, 'data/.gitkeep', '')
        write(self.root, 'data/notify_state.json', production_state())
        write(self.root, 'data/report.json', pr1_report())
        write(self.root, 'data/hypotheses.json', {'k0': {'family': 'Jogo aberto', 'conditions': [], 'source': 'gerador'}})
        write(self.root, 'data/messages.jsonl', '{"message_id": 1}\n')
        write(self.root, 'docs/report.json', pr1_report())
        write(self.root, 'docs/bets.csv', 'data;resultado\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'data: collect (main with PR1)')
        self.main = rev(self.root)
        write(self.root, 'scangoal_code.txt', 'PR2 code\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'merge PR2')
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop('SCANGOAL_DATA_DIR', None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def files(self):
        return cut.digests(cut.contents(self.root))

    def state(self):
        return json.loads((self.root / 'data/notify_state.json').read_text(encoding='utf-8'))

    def refused(self, message, build=build_ok, frozen=None):
        before = self.files()
        with self.assertRaises(cut.CutoverError) as ctx:
            cut.cutover(self.root, frozen or self.main, NOW, build)
        self.assertIn(message, str(ctx.exception))
        self.assertEqual(self.files(), before, 'a refused cutover must leave every file byte-identical')
        return ctx.exception

    def commit_state(self, state):
        write(self.root, 'data/notify_state.json', state)
        git(self.root, 'commit', '-q', '-am', 'state')
        return rev(self.root)

    # --- success -------------------------------------------------------------------------------------------------
    def test_cutover_rebaselines_silently_and_preserves_pr1_marker_and_history(self):
        result = cut.cutover(self.root, self.main, NOW, build_ok)
        self.assertTrue(result['ok'], result['checks'])
        self.assertEqual(result['migration_key'], 'status_rules:independent_games_hysteresis_v1')
        self.assertEqual(result['reference'], {'previous': 20, 'baseline': 13, 'silenced_transitions': 11})
        self.assertEqual(result['changed_files'], ['data/notify_state.json', 'data/report.json', 'docs/report.json'])
        self.assertEqual(result['next_cycle_simulated_messages'], {'register_false': {}, 'register_true': {}})
        state, before = self.state(), production_state()
        self.assertEqual(state['migrations'][notify.STATUS_MIGRATION], before['migrations'][notify.STATUS_MIGRATION])
        self.assertEqual(sorted(state['migrations']), sorted([notify.STATUS_MIGRATION, cut.PR2_KEY]))
        marker = state['migrations'][cut.PR2_KEY]
        self.assertEqual((marker['previous_statuses'], marker['baseline_statuses'], state['statuses']), (PR1_STATUSES, PR2_STATUSES, PR2_STATUSES))
        for k, v in before.items():
            if k not in ('statuses', 'migrations'):
                self.assertEqual(state[k], v, k)
        # register=True ran on a copy: the checkout registry never grew
        self.assertNotIn('grown-by-register-true', (self.root / 'data/hypotheses.json').read_text(encoding='utf-8'))
        self.assertEqual(result['register_true']['registry_size_after_on_copy'], 2)

    def test_retroactive_transitions_are_silenced_and_legitimate_changes_still_notified(self):
        without = notify.messages(pr2_report(), dict(production_state(), migrations=dict(production_state()['migrations'], **{cut.PR2_KEY: {}})), NOW)
        self.assertEqual(kinds(without), {'status_transition': 11})                    # what would be sent without the migration
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        state = self.state()
        self.assertEqual(notify.messages(pr2_report(), copy.deepcopy(state), NOW), [])   # next cycle: nothing historical
        later = dict(PR2_STATUSES, n2='promissora')
        later.pop('k0')
        msgs = notify.messages(pr2_report(later), state, NOW)
        self.assertEqual(sorted(msgs), sorted(['Hipótese passou a promissora:\nn2', 'Hipótese voltou a "em observação":\nk0']))
        self.assertEqual(notify.messages(pr2_report(later), state, NOW), [])            # notified once

    # --- guards --------------------------------------------------------------------------------------------------
    def test_rerun_after_committed_cutover_cannot_reset_the_reference(self):
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        write(self.root, 'data/report.json', pr1_report())          # isolate the marker guard from the frozen-report guard
        write(self.root, 'docs/report.json', pr1_report())
        git(self.root, 'commit', '-q', '-am', 'data: cutover (report kept PR1-format for this test)')
        self.refused('already present', frozen='HEAD')

    def test_pr1_marker_is_required(self):
        state = production_state()
        state.pop('migrations')
        self.refused('PR1 migration marker', frozen=self.commit_state(state))

    def test_stale_dirty_or_unrelated_snapshots_are_refused(self):
        state = production_state()
        state['sent_total'] += 1
        write(self.root, 'data/notify_state.json', state)
        self.refused('uncommitted changes')
        git(self.root, 'commit', '-q', '-am', 'data: collect (newer)')
        self.refused('data differs from frozen main')
        self.refused('unknown frozen main commit', frozen='0' * 40)

    def test_frozen_report_must_be_pr1_format(self):
        write(self.root, 'data/report.json', pr2_report())
        write(self.root, 'docs/report.json', pr2_report())
        git(self.root, 'commit', '-q', '-am', 'data: written by PR2 code')
        self.refused('already carries status_rules', frozen='HEAD')
        write(self.root, 'data/report.json', {'brain': {'status_list': {}}})
        write(self.root, 'docs/report.json', {'brain': {'status_list': {}}})
        git(self.root, 'commit', '-q', '-am', 'data: pre-PR1 report')
        self.refused('integrate PR1 first', frozen='HEAD')

    def test_report_without_the_new_rules_or_eligible_blocks_is_refused(self):
        for label, rep, reason in (('PR1-format', pr1_report(), 'is not produced under status rules'),
                                   ('wrong rules', pr2_report(status_rules='other_rules_v9'), 'is not produced under status rules'),
                                   ('no operational', dict(pr2_report(), operational={}), 'no eligible operational block'),
                                   ('bad status', pr2_report({'k0': 'excelente'}), 'no valid eligible status_list')):
            with self.subTest(label):
                exc = self.refused(reason, build=lambda csv_path=None, register=False, rep=rep: rep)
                self.assertIn('register=False report', str(exc))

    def test_faulty_migration_touching_pr1_marker_or_delivery_history_is_rolled_back(self):
        real = cut.notify.migrate_file
        faults = {'pr1_marker_preserved': lambda st: st['migrations'][notify.STATUS_MIGRATION].update(baseline_statuses={}),
                  'delivery_fields_preserved': lambda st: st.update(sent_total=st['sent_total'] + 1),
                  'no_unexpected_fields': lambda st: st.update(unexpected_field=True)}
        for check, fault in faults.items():
            with self.subTest(check):
                def faulty(now=None, fault=fault):
                    changed = real(now)
                    path = self.root / 'data/notify_state.json'
                    st = json.loads(path.read_text(encoding='utf-8'))
                    fault(st)
                    path.write_text(json.dumps(st, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
                    return changed
                before = self.files()
                with patch.object(cut.notify, 'migrate_file', faulty), redirect_stdout(io.StringIO()):
                    result = cut.cutover(self.root, self.main, NOW, build_ok)
                self.assertFalse(result['ok'])
                self.assertFalse(result['checks'][check], check)
                self.assertTrue(result['restored'])
                self.assertEqual(self.files(), before)

    def test_notify_and_brain_must_agree_on_the_migration_key(self):
        with patch.object(cut.notify, 'migration_key', lambda report: 'status_rules:something_else'):
            self.refused('maps to migration key status_rules:something_else')

    def test_any_status_transition_in_the_simulated_next_cycle_fails_and_restores(self):
        before = self.files()
        leak = ['Hipótese passou a promissora:\nk0']
        for label, side in (('register=False', [leak, []]), ('register=True', [[], leak])):
            with self.subTest(label), patch.object(cut.notify, 'messages', side_effect=side):
                result = cut.cutover(self.root, self.main, NOW, build_ok)
                self.assertFalse(result['ok'])
                self.assertTrue(result['restored'])
                self.assertEqual(self.files(), before)
                self.assertEqual(result['checks']['next_cycle_status_transitions'] + result['checks']['next_cycle_status_transitions_register_true'], 1)

    def test_register_true_divergence_refused_before_any_write(self):
        def build_diverging(csv_path=None, register=False):
            build_ok(csv_path, register)
            return pr2_report(dict(PR2_STATUSES, newly_registered='promissora')) if register else pr2_report()
        self.refused('register=True status_list differs', build=build_diverging)
        self.assertNotIn('grown-by-register-true', (self.root / 'data/hypotheses.json').read_text(encoding='utf-8'))

    def test_register_true_build_must_not_write_into_the_checkout(self):
        def build_leaking(csv_path=None, register=False):
            if register:
                write(self.root, 'data/hypotheses.json', {'leaked': True})
            return pr2_report()
        exc = self.refused('wrote into the checkout', build=build_leaking)
        self.assertIsInstance(exc, cut.CutoverAborted)

    # --- partial writes and restore ------------------------------------------------------------------------------
    def test_exceptions_after_writes_restore_byte_for_byte(self):
        original = cut.contents(self.root)
        for stage, target in (('read_migrated_state', 'json'), ('simulate_next_cycle', 'messages'), ('post_checks', 'contents')):
            with self.subTest(stage):
                seen = {}
                real_restore = cut.pr1.restore

                def spy(root, orig, real_restore=real_restore):
                    seen.setdefault('at_restore', cut.contents(root))
                    return real_restore(root, orig)

                if target == 'json':
                    class Injected:
                        dumps = staticmethod(json.dumps)

                        @staticmethod
                        def loads(s, *a, **k):
                            text = s.decode() if isinstance(s, bytes) else s
                            if cut.PR2_KEY in text:
                                raise ValueError('injected failure reading the migrated state')
                            return json.loads(s, *a, **k)
                    inject = patch.object(cut, 'json', Injected)
                elif target == 'messages':
                    inject = patch.object(cut.notify, 'messages', side_effect=RuntimeError('injected failure simulating'))
                else:
                    calls, real = {'n': 0}, cut.contents

                    def flaky(root, real=real, calls=calls):
                        calls['n'] += 1
                        if calls['n'] == 3:   # 1: snapshot; 2: register=True check; 3: post-checks
                            raise OSError('injected failure in post-checks')
                        return real(root)
                    inject = patch.object(cut, 'contents', flaky)
                with patch.object(cut, 'restore', spy), inject:
                    with self.assertRaises(cut.CutoverAborted) as ctx:
                        cut.cutover(self.root, self.main, NOW, build_ok)
                self.assertEqual(ctx.exception.stage, stage)
                self.assertIn(cut.PR2_KEY.encode(), seen['at_restore']['data/notify_state.json'], 'migration had been written')
                self.assertEqual(cut.contents(self.root), original)
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])   # still re-runnable after the aborts

    def test_restore_failure_is_reported_distinctly_with_exit_code_3(self):
        with patch.object(cut.notify, 'messages', side_effect=RuntimeError('injected')), \
                patch.object(Path, 'write_bytes', side_effect=OSError('disk')):
            with self.assertRaises(cut.RestoreFailed) as ctx:
                cut.cutover(self.root, self.main, NOW, build_ok)
        self.assertNotIsInstance(ctx.exception, cut.CutoverError)
        self.assertEqual(ctx.exception.stage, 'simulate_next_cycle')
        git(self.root, 'checkout', '--', 'data', 'docs')
        with patch.object(cut, 'ROOT', self.root), patch.object(cut.report, 'build', build_ok), \
                patch.object(cut.notify, 'messages', side_effect=RuntimeError('injected')):
            with redirect_stdout(io.StringIO()) as buf:
                code = cut.main([self.main])
            out = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual((code, out['restored'], out['stage']), (1, True, 'simulate_next_cycle'))
            with patch.object(Path, 'write_bytes', side_effect=OSError('disk')), redirect_stdout(io.StringIO()) as buf:
                code = cut.main([self.main])
            out = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual((code, out['restore_failed'], out['trigger']), (3, True, 'RuntimeError'))
        self.assertEqual(cut.main([]), 2)

    def test_network_attempt_aborts_and_restores(self):
        def build_net(csv_path=None, register=False):
            (self.root / 'data/report.json').write_text('partial', encoding='utf-8')
            socket.create_connection(('192.0.2.1', 9), timeout=1)
            return pr2_report()
        self.refused('network access attempted', build=build_net)

    # --- old code after the cutover --------------------------------------------------------------------------------
    def test_old_code_running_after_cutover_would_send_false_transitions(self):
        """Why the collectors must be paused and no pre-merge run may write afterwards (documented, not preventable here)."""
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        state = self.state()
        old_run = notify.messages(pr1_report(), state, NOW)          # a PR1-format report: PR1 key present, no re-migration
        self.assertEqual(kinds(old_run), {'status_transition': 11})
        self.assertEqual(state['statuses'], PR1_STATUSES)              # the PR2 reference was overwritten
        next_new = notify.messages(pr2_report(), state, NOW)        # PR2 key already present: no silent re-baseline
        self.assertEqual(kinds(next_new), {'status_transition': 11})


if __name__ == '__main__':
    unittest.main()

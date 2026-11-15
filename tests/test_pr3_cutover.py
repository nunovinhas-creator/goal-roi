"""Offline tests for scripts/pr3_cutover.py on synthetic git repositories (no real data, network or sender)."""
import copy
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scangoal import brain, notify

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pr3_cutover', ROOT / 'scripts' / 'pr3_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)

NOW = datetime.fromisoformat('2026-11-17T03:00:00+00:00')   # low-activity window, before the 21:00 summary
PR2_STATUSES = dict({'k%d' % i: 'promissora' for i in range(11)}, n0='promissora', n1='promissora')   # 13 promising (PR 2)
PR3_STATUSES = {'k%d' % i: 'promissora' for i in range(11)}                                          # 11 promising (PR 3)


def production_state():
    """notify_state as left by PR2 in production: PR1 and PR2 markers, PR2 statuses, delivery history, regime."""
    return {'hello': True, 'statuses': dict(PR2_STATUSES), 'watch_sent': ['2026-11-15T19:52:04+00:00|A — B|Over 6'],
            'sent_total': 301, 'summary_day': '2026-11-16', 'last_run_utc': '2026-11-16T20:08:47+00:00',
            'regime': {'OPEN_GAME': 'estável'}, 'operational_regime': {'OPEN_GAME': 'estável', 'POLYMARKET': 'poucos dados'},
            'migrations': {notify.STATUS_MIGRATION: {'at_utc': '2026-11-15T09:27:59+00:00', 'baseline_statuses': {'k0': 'promissora'}},
                           cut.PR2_KEY: {'at_utc': '2026-11-15T12:04:00+00:00', 'baseline_statuses': dict(PR2_STATUSES)}}}


def pr2_report():
    return {'generated_at_utc': '2026-11-16T20:08:47+00:00',
            'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': cut.PR2_RULES, 'status_list': dict(PR2_STATUSES)},
            'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]}}


def pr3_report(statuses=None, validation=None, **brain_extra):
    return {'generated_at_utc': NOW.isoformat(),
            'brain': dict({'evidence_scope': 'eligible_bets_only', 'status_rules': cut.PR3_RULES, 'hypotheses': 30, 'tested': 20,
                           'status_list': dict(PR3_STATUSES if statuses is None else statuses), 'counts': {'promissora': 11},
                           'exploratory': {'counts': {'promissora': 57}}, 'eligibility': {'eligible': 10, 'total': 12},
                           'walk_forward': {'followed': {}, 'skipped': {}}, 'watch': [],
                           'validation': validation or {'method': notify.VALIDATION_METHOD, 'state': 'not_activated', 'ledger_valid': False,
                                                        'certified': [], 'candidates': []}}, **brain_extra),
            'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]}}


def build_ok(csv_path=None, register=False):
    assert csv_path is None
    if register:   # like the production report step: grows the registry in whatever data dir is active
        path = Path(os.environ['SCANGOAL_DATA_DIR']) / 'hypotheses.json'
        reg = json.loads(path.read_text(encoding='utf-8'))
        reg['grown-by-register-true'] = {'family': 'Jogo aberto', 'conditions': []}
        path.write_text(json.dumps(reg), encoding='utf-8')
    return pr3_report()


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
class PR3Cutover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q')
        write(self.root, 'data/notify_state.json', production_state())
        write(self.root, 'data/report.json', pr2_report())
        write(self.root, 'data/hypotheses.json', {'k0': {'family': 'Jogo aberto', 'conditions': [], 'source': 'gerador'}})
        write(self.root, 'data/messages.jsonl', '{"message_id": 1}\n')
        write(self.root, 'docs/report.json', pr2_report())
        write(self.root, 'docs/bets.csv', 'data;resultado\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'data: collect (main with PR2)')
        self.main = rev(self.root)
        write(self.root, 'scangoal_code.txt', 'PR3 code\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'merge PR3')
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

    def commit_all(self, message='data'):
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', message)
        return rev(self.root)

    # --- success ---------------------------------------------------------------------------------------------------
    def test_cutover_rebaselines_silently_and_preserves_both_markers_and_history(self):
        result = cut.cutover(self.root, self.main, NOW, build_ok)
        self.assertTrue(result['ok'], result['checks'])
        self.assertEqual(result['migration_key'], 'status_rules:chronological_eprocess_v3')
        self.assertEqual(result['reference'], {'previous': 13, 'baseline': 11, 'silenced_transitions': 2})
        self.assertEqual(result['changed_files'], ['data/notify_state.json', 'data/report.json', 'docs/report.json'])
        self.assertEqual(result['simulated_cycles'], {'cycle1': {}, 'cycle2': {}})
        self.assertEqual(result['brain']['validation_state'], 'not_activated')
        state, before = self.state(), production_state()
        for key in (notify.STATUS_MIGRATION, cut.PR2_KEY):
            self.assertEqual(state['migrations'][key], before['migrations'][key], key)
        self.assertEqual(sorted(state['migrations']), sorted([notify.STATUS_MIGRATION, cut.PR2_KEY, cut.PR3_KEY]))
        marker = state['migrations'][cut.PR3_KEY]
        self.assertEqual((marker['previous_statuses'], marker['baseline_statuses'], state['statuses']), (PR2_STATUSES, PR3_STATUSES, PR3_STATUSES))
        for k, v in before.items():
            if k not in ('statuses', 'migrations'):
                self.assertEqual(state[k], v, k)
        self.assertFalse((self.root / cut.LEDGER).exists())
        self.assertNotIn('grown-by-register-true', (self.root / 'data/hypotheses.json').read_text(encoding='utf-8'))

    def test_two_cycles_after_cutover_send_nothing_historical_and_later_changes_are_notified_once(self):
        without = notify.messages(pr3_report(), dict(production_state(), migrations=dict(production_state()['migrations'], **{cut.PR3_KEY: {}})), NOW)
        self.assertEqual(kinds(without), {'status_transition': 2})                       # what would be sent without the migration
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        state = self.state()
        self.assertEqual(notify.messages(pr3_report(), state, NOW), [])                  # cycle 1
        self.assertEqual(notify.messages(pr3_report(), state, NOW), [])                  # cycle 2
        later = dict(PR3_STATUSES, n2='promissora')
        later.pop('k0')
        msgs = notify.messages(pr3_report(later), state, NOW)
        self.assertEqual(sorted(msgs), sorted(['Hipótese passou a promissora:\nn2', 'Hipótese voltou a "em observação":\nk0']))
        self.assertEqual(notify.messages(pr3_report(later), state, NOW), [])

    def test_rerun_is_idempotent_after_the_committed_cutover(self):
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        head = self.commit_all('data: PR3 cutover')
        before = self.files()
        result = cut.cutover(self.root, head, NOW, build_ok)
        self.assertEqual((result['ok'], result['already_applied'], result['changed_files']), (True, True, []))
        self.assertEqual(self.files(), before)
        state = self.state()                                                             # incoherent marker: refused, not reapplied
        state['statuses'] = {'x': 'promissora'}
        write(self.root, 'data/notify_state.json', state)
        self.refused('not a coherent PR3 cutover', frozen=self.commit_all())

    # --- guards ----------------------------------------------------------------------------------------------------
    def test_markers_of_pr1_and_pr2_are_required(self):
        for key in (notify.STATUS_MIGRATION, cut.PR2_KEY):
            with self.subTest(key):
                state = production_state()
                state['migrations'].pop(key)
                write(self.root, 'data/notify_state.json', state)
                self.refused('migration marker', frozen=self.commit_all())
                write(self.root, 'data/notify_state.json', production_state())
                self.commit_all()

    def test_frozen_report_must_be_pr2_format(self):
        write(self.root, 'data/report.json', pr3_report())
        write(self.root, 'docs/report.json', pr3_report())
        self.refused('a PR3-code writer has run', frozen=self.commit_all())
        rep = pr2_report()
        rep['brain'].pop('status_rules')
        write(self.root, 'data/report.json', rep)
        write(self.root, 'docs/report.json', rep)
        self.refused('integrate PR2 first', frozen=self.commit_all())

    def test_any_certification_ledger_is_refused(self):
        write(self.root, cut.LEDGER, {'method': 'x'})
        self.refused('certification ledger exists at the frozen main', frozen=self.commit_all())

    def test_built_report_with_activated_or_validated_certification_is_refused(self):
        cases = (('active', pr3_report(validation={'state': 'active', 'ledger_valid': True, 'certified': []}), 'certification state active'),
                 ('certified', pr3_report(validation={'state': 'not_activated', 'certified': ['k0']}), 'must not activate certification'),
                 ('forte', pr3_report(dict(PR3_STATUSES, k0='forte')), 'validated status without certification'),
                 ('pr2 rules', pr2_report(), 'is not produced under status rules'))
        for label, rep, reason in cases:
            with self.subTest(label):
                self.refused(reason, build=lambda csv_path=None, register=False, rep=rep: rep)

    def test_register_true_creating_a_ledger_is_refused(self):
        def build_ledger(csv_path=None, register=False):
            if register:
                write(Path(os.environ['SCANGOAL_DATA_DIR']).parent, cut.LEDGER, {'created': True})
            return pr3_report()
        self.refused('created a certification ledger', build=build_ledger)

    def test_stale_dirty_or_unrelated_snapshots_are_refused(self):
        state = production_state()
        state['sent_total'] += 1
        write(self.root, 'data/notify_state.json', state)
        self.refused('uncommitted changes')
        self.commit_all('data: collect (newer)')
        self.refused('data differs from frozen main')
        self.refused('unknown frozen main commit', frozen='0' * 40)

    def test_pinned_rules_must_match_the_code(self):
        with patch.object(cut.brain, 'STATUS_RULES', 'other_rules_v9'):
            self.refused('pinned to chronological_eprocess_v3')
        self.assertEqual(brain.STATUS_RULES, cut.PR3_RULES)

    # --- failures during the run: rollback and re-run --------------------------------------------------------------
    def test_validated_message_or_transition_in_either_simulated_cycle_fails_and_restores(self):
        before = self.files()
        leaks = (['Hipótese passou a promissora:\nk0'], ['🟢 ALERTA: tática validada (forte)\nA — B'], ['✅ Tática validada, passou a forte:\nk0'])
        for leak in leaks:
            for side in ([leak, []], [[], leak]):
                with self.subTest(leak=leak[0][:12], cycle=1 if side[0] else 2), patch.object(cut.notify, 'messages', side_effect=side):
                    result = cut.cutover(self.root, self.main, NOW, build_ok)
                    self.assertEqual((result['ok'], result['restored']), (False, True))
                    self.assertEqual(self.files(), before)

    def test_interrupted_execution_restores_byte_for_byte_and_can_be_rerun(self):
        original = cut.contents(self.root)
        for exc in (KeyboardInterrupt(), RuntimeError('injected'), OSError('disk full')):
            with self.subTest(type(exc).__name__), patch.object(cut.notify, 'messages', side_effect=exc):
                with self.assertRaises(type(exc) if isinstance(exc, KeyboardInterrupt) else cut.CutoverAborted):
                    cut.cutover(self.root, self.main, NOW, build_ok)
                self.assertEqual(cut.contents(self.root), original)
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])

    def test_network_attempt_aborts_and_restores(self):
        def build_net(csv_path=None, register=False):
            (self.root / 'data/report.json').write_text('partial', encoding='utf-8')
            socket.create_connection(('192.0.2.1', 9), timeout=1)
            return pr3_report()
        self.refused('network access attempted', build=build_net)

    def test_restore_failure_is_reported_with_exit_code_3(self):
        with patch.object(cut, 'ROOT', self.root), patch.object(cut.report, 'build', build_ok), \
                patch.object(cut.notify, 'messages', side_effect=RuntimeError('injected')), \
                patch.object(Path, 'write_bytes', side_effect=OSError('disk')), redirect_stdout(io.StringIO()) as buf:
            code = cut.main([self.main])
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual((code, out['restore_failed']), (3, True))
        git(self.root, 'checkout', '--', 'data', 'docs')
        self.assertEqual(cut.main([]), 2)

    # --- push preflight: concurrent commits, linear history --------------------------------------------------------
    def test_push_preflight_detects_a_moved_main_and_non_fast_forward(self):
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        self.commit_all('data: PR3 cutover')
        git(self.root, 'branch', '-f', 'remote-main', self.main)
        self.assertEqual(cut.push_preflight(self.root, 'remote-main', self.main), [])
        git(self.root, 'checkout', '-q', '-b', 'collector', self.main)                     # a collector commit lands on main
        write(self.root, 'data/messages.jsonl', '{"message_id": 1}\n{"message_id": 2}\n')
        moved = self.commit_all('data: collect (concurrent)')
        git(self.root, 'checkout', '-q', '-')
        problems = cut.push_preflight(self.root, moved, self.main)
        self.assertTrue(any('moved from the frozen commit' in p for p in problems))
        self.assertTrue(any('fast-forward' in p for p in problems))
        self.assertTrue(cut.push_preflight(self.root, 'no-such-ref', self.main))

    def test_old_code_running_after_cutover_would_send_false_transitions(self):
        """Why every pre-merge writer must be stopped: a PR2-format report after the cutover overwrites the reference."""
        self.assertTrue(cut.cutover(self.root, self.main, NOW, build_ok)['ok'])
        state = self.state()
        old_run = notify.messages(pr2_report(), state, NOW)                               # PR2 key present: no re-migration
        self.assertEqual(kinds(old_run), {'status_transition': 2})
        self.assertEqual(state['statuses'], PR2_STATUSES)
        self.assertEqual(kinds(notify.messages(pr3_report(), state, NOW)), {'status_transition': 2})


if __name__ == '__main__':
    unittest.main()

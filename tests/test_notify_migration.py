"""Silent eligibility migration with persistent delivery history."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

from pr5_fixtures import body, changes, evening, present
from scangoal import notify
import test_scangoal as fixtures

NOW = datetime.fromisoformat('2026-11-14T12:00:00+00:00')


def legacy_state():
    return {'hello': True, 'statuses': {f'h{i}': 'promissora' for i in range(33)},
            'watch_sent': ['already-sent'], 'sent_total': 204, 'summary_day': '2026-11-13',
            'last_run_utc': '2026-11-14T11:00:00+00:00', 'regime': {'OPEN_GAME': 'estável'},
            'custom_setting': {'preserve': True}}


def eligible_report():
    return {'generated_at_utc': NOW.isoformat(), 'brain': {'evidence_scope': 'eligible_bets_only',
            'status_list': {f'h{i}': 'promissora' for i in range(8)},
            # PR 3: validated statuses are only announced with a certificate of the accepted method (fixture declares it).
            'validation': {'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': ['h0', 'h1']}}}


class NotifyMigration(unittest.TestCase):
    def test_33_to_8_silent_and_idempotent_with_history(self):
        state, rep = legacy_state(), eligible_report()
        before = copy.deepcopy(state)
        self.assertEqual(notify.messages(rep, state, NOW), [])
        self.assertEqual(len(state['statuses']), 8)
        self.assertEqual(state['migrations'][notify.STATUS_MIGRATION]['previous_statuses'], before['statuses'])
        for key in set(before) - {'statuses'}:
            self.assertEqual(state[key], before[key])
        migrated = copy.deepcopy(state)
        self.assertEqual(notify.messages(rep, state, NOW), [])
        self.assertEqual(state, migrated)
        self.assertFalse(notify.migrate_status_reference(rep, state, NOW))
        # No references into the supplied report: callers may reuse/mutate it.
        rep['brain']['status_list']['h0'] = 'abandonada'
        self.assertEqual(state['statuses']['h0'], 'promissora')
        self.assertEqual(state['migrations'][notify.STATUS_MIGRATION]['baseline_statuses']['h0'], 'promissora')

    def test_explicit_observation_and_abandonment_are_silent_at_boundary(self):
        rep, state = eligible_report(), legacy_state()
        rep['brain']['status_list'].update({'h8': 'em observação', 'h9': 'abandonada'})
        self.assertEqual(notify.messages(rep, state, NOW), [])
        self.assertEqual(state['statuses'], rep['brain']['status_list'])

    def test_genuine_subsequent_changes_are_not_silenced(self):
        rep, state = eligible_report(), legacy_state()
        notify.messages(rep, state, NOW)
        new = rep['brain']['status_list']
        new.pop('h0')  # Actual return to observation after migration.
        new['h1'] = 'forte'
        new['h2'] = 'abandonada'
        new['new'] = 'promissora'
        # PR 5: only what the report shows is announced; a bare absence is "unknown" and h1 needs its own certificate.
        self.assertEqual(changes(notify.messages(copy.deepcopy(rep), copy.deepcopy(state), evening(NOW))), [])
        present(rep, certified=['h0', 'h1'], observed=['h0'])
        self.assertEqual(notify.messages(rep, copy.deepcopy(state), NOW), [])           # never during the day
        messages = notify.messages(rep, state, evening(NOW))
        self.assertEqual(len(messages), 1)                                                # one consolidated summary
        self.assertEqual(sorted(changes(messages)), sorted([
            ('h0', 'Estado: Em observação (antes: promissora)'), ('h1', '✅ Tática validada, passou a forte (antes: promissora): h1'),
            ('h2', 'Estado: Abandonada (antes: promissora)'), ('new', 'Estado: Promissora (antes: em observação)')]))
        self.assertEqual(changes(notify.messages(rep, state, evening(NOW + timedelta(days=1)))), [])

    def test_watch_and_daily_summary_keep_working(self):
        rep = fixtures.Notify().report()
        rep['brain']['evidence_scope'] = 'eligible_bets_only'
        state = legacy_state()
        at = evening(NOW)
        rep['brain']['watch'][0]['date_utc'] = (at - timedelta(minutes=5)).isoformat()   # P1 17/11/2026: só sinais atuais
        messages = notify.messages(rep, state, at)
        self.assertEqual(len(messages), 2)
        self.assertTrue(any(m.startswith('A seguir:') for m in messages))
        self.assertTrue(any(m.startswith('Resumo das táticas — 14/11') for m in messages))
        self.assertTrue(all(notify.guard_claims(m, rep) for m in messages))           # no positive claim in either
        self.assertIn('Estado científico:', next(m for m in messages if m.startswith('Resumo')))
        self.assertIn('already-sent', state['watch_sent'])
        self.assertEqual(notify.messages(rep, state, at), [])

    def test_missing_scope_does_not_migrate_or_overwrite_reference(self):
        for brain in ({}, {'status_list': {}}, {'evidence_scope': 'all_signals', 'status_list': {}},
                      {'evidence_scope': 'eligible_bets_only'}):
            state = legacy_state()
            old = copy.deepcopy(state)
            self.assertEqual(notify.messages({'brain': brain}, state, NOW), [])
            self.assertEqual(state, old)
        state, rep = legacy_state(), eligible_report()
        notify.messages(rep, state, NOW)
        old = copy.deepcopy(state)
        notify.messages({'brain': {'status_list': {'h0': 'forte'}}}, state, NOW)
        self.assertEqual(state, old)

    def files(self, directory, history=False):
        # history=True: a state the PR 5 notifier accepts (P2-R): production history carries at least the PR 1 marker.
        path = Path(directory) / 'notify_state.json'
        state = legacy_state()
        if history:
            state['migrations'] = {notify.STATUS_MIGRATION: {'at_utc': '2026-11-14T11:00:00+00:00', 'baseline_statuses': {}}}
        path.write_text(json.dumps(state))
        (Path(directory) / 'report.json').write_text(json.dumps(eligible_report()))
        return path

    def test_offline_migration_persists_once_without_sender_or_counters(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}), patch.object(notify, 'send', side_effect=AssertionError('no sender')):
            path = self.files(d)
            self.assertTrue(notify.migrate_file(NOW))
            saved = path.read_bytes()
            self.assertFalse(notify.migrate_file(NOW))
            self.assertEqual(path.read_bytes(), saved)
            with patch.object(notify.sys, 'argv', ['notify', '--migrate-statuses']), patch.object(notify, 'run', side_effect=AssertionError('no delivery')):
                notify.main()
            self.assertEqual(path.read_bytes(), saved)
            state = json.loads(saved)
            for k in ('sent_total', 'watch_sent', 'last_run_utc', 'summary_day'):
                self.assertEqual(state[k], legacy_state()[k])
            rep = eligible_report()
            rep['brain']['status_list']['h0'] = 'forte'
            self.assertEqual(notify.messages(present(rep, certified=['h0', 'h1']), copy.deepcopy(state), NOW), [])
            out = notify.messages(present(rep, certified=['h0', 'h1']), state, evening(NOW))
            self.assertEqual((len(out), changes(out)), (1, [('h0', '✅ Tática validada, passou a forte (antes: promissora): h0')]))

    def test_send_failure_cannot_undo_migration_or_consume_delivery(self):
        # PR 5 (at most once): the intent is persisted BEFORE the send; an unknown sender error makes the greeting UNCERTAIN,
        # it is never resent and never counted as delivered. The migration persisted before the send stays.
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}):
            path = self.files(d, history=True)
            state = json.loads(path.read_text()); state['hello'] = False; path.write_text(json.dumps(state))
            def fail(*args):
                persisted = json.loads(path.read_text())
                self.assertIn(notify.FORMAT_MIGRATION, persisted['migrations'])      # PR 5: the silent migration persisted first
                self.assertEqual(persisted['deliveries']['hello']['state'], 'in_flight')
                raise RuntimeError('simulated sender failure')
            env = {'TG_BOT_TOKEN': 'fake', 'TG_CHAT_ID': 'fake'}
            notify.run(fail, NOW, env)
            persisted = json.loads(path.read_text())
            self.assertEqual((persisted['deliveries']['hello']['state'], persisted['sent_total']), ('uncertain', 204))
            sent = []
            notify.run(lambda *args: sent.append(args[-1]), NOW, env)
            self.assertEqual(sent, [])      # never the greeting again, never 25 historical transitions
            notify.run(lambda *args: sent.append(args[-1]), NOW, env)
            self.assertEqual(sent, [])

    def test_failed_atomic_save_leaves_state_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}):
            path = self.files(d, history=True); original = path.read_bytes(); sent = []
            with patch.object(notify.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    notify.run(lambda *args: sent.append(args), NOW, {'TG_BOT_TOKEN': 'fake', 'TG_CHAT_ID': 'fake'})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(sent, [])
            self.assertEqual(list(Path(d).glob('.notify-state-*')), [])

    def test_corrupt_state_is_not_replaced_with_empty_history(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}):
            path = self.files(d); path.write_text('{invalid'); sent = []
            with self.assertRaises(ValueError):
                notify.run(lambda *args: sent.append(args), NOW, {'TG_BOT_TOKEN': 'fake', 'TG_CHAT_ID': 'fake'})
            self.assertEqual(path.read_text(), '{invalid')
            self.assertEqual(sent, [])

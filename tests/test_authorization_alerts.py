"""Offline authorization alert regressions. Git pushes only to a temporary bare repo."""
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from telethon.errors import SessionRevokedError

from scangoal import authorization_alerts as alerts
from scangoal import authorization_events as events
from scangoal import authorization_status as status
from scangoal import notify, reader, watch
from test_telegram_diagnostics import Client

NOW = datetime(2026, 11, 27, 18, 14, tzinfo=timezone.utc)
REVOCATION = 'SessionRevokedError'
ROOT = Path(__file__).resolve().parents[1]


class GitAlerts(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.origin = self.root / 'origin'
        self.git('init', '--bare', '-q', str(self.origin))
        self.checkout = self.clone('first')
        self.storage = alerts.GitState(self.checkout)
        self.bootstrap = self.seed(alerts.initial_state())
        self.time = NOW
        self.sender = Mock()
        self.alerts = self.worker(self.storage, self.sender)

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()

    def clone(self, name):
        path = self.root / name
        self.git('clone', '-q', str(self.origin), str(path))
        return path

    def seed(self, state):
        command = ['git', '--git-dir', str(self.origin)]
        def run(*args, payload=None):
            return subprocess.run([*command, *args], input=payload, text=True, check=True,
                                  capture_output=True).stdout.strip()
        blob = run('hash-object', '-w', '--stdin', payload=state if isinstance(state, str) else json.dumps(state))
        tree = run('mktree', payload=f'100644 blob {blob}\tincident.json\n')
        tip = run('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                  'commit-tree', tree, '-m', 'authorised synthetic bootstrap')
        for ref in (alerts.REF, alerts.WITNESS):
            run('update-ref', ref, tip)
        return tip

    def test_missing_ref_never_initializes_or_sends(self):
        for ref in (alerts.REF, alerts.WITNESS):
            self.git('--git-dir', str(self.origin), 'update-ref', '-d', ref)
            with self.assertRaises(alerts.StateUnavailable):
                self.detect()
        with self.assertRaises(alerts.StateUnavailable):
            self.storage.write('', alerts.initial_state())
        self.assertEqual(self.git('--git-dir', str(self.origin), 'for-each-ref'), '')
        self.sender.assert_not_called()

    def test_invalid_remote_record_blocks_send_before_reservation(self):
        state = alerts.initial_state()
        incident = 'e' * 32
        state.update(active=incident, incidents={incident: {
            'status': 'reserved', 'error': REVOCATION, 'detected_at': self.time.isoformat()}})
        # Inject directly into both remote refs, bypassing the production writer.
        for invalid in (state, json.dumps(alerts.initial_state()).replace('"version": 1', '"version": 1, "version": 1')):
            with self.subTest(invalid=invalid):
                self.seed(invalid)
                with self.assertRaises(alerts.StateUnavailable):
                    self.detect()
        self.sender.assert_not_called()

    def test_deleted_and_stale_restored_ref_cannot_repeat_a_confirmed_attempt(self):
        incident, state = self.detect()
        self.alerts.deliver(incident, state)
        confirmed_tip, _ = self.storage.read()
        self.git('--git-dir', str(self.origin), 'update-ref', '-d', alerts.REF, confirmed_tip)
        self.assertFalse(self.storage.write(confirmed_tip, state))
        first = self.worker(alerts.GitState(self.clone('after-deletion')), self.sender)
        with self.assertRaises(alerts.StateUnavailable):
            self.detect(first)
        self.git('--git-dir', str(self.origin), 'update-ref', alerts.REF, self.bootstrap)
        second = self.worker(alerts.GitState(self.clone('after-stale-restore')), self.sender)
        with self.assertRaises(alerts.StateUnavailable):
            self.detect(second)
        self.sender.assert_called_once()
        self.git('--git-dir', str(self.origin), 'update-ref', alerts.REF, confirmed_tip, self.bootstrap)
        _, recovered = self.detect(second)
        second.deliver(incident, recovered)
        self.sender.assert_called_once()

    def test_stale_witness_blocks_read_and_partial_write(self):
        incident, state = self.detect()
        self.alerts.deliver(incident, state)
        tip, state = self.storage.read()
        self.git('--git-dir', str(self.origin), 'update-ref', alerts.WITNESS, self.bootstrap, tip)
        with self.assertRaises(alerts.StateUnavailable):
            self.storage.read()
        self.assertFalse(self.storage.write(tip, state))
        self.assertEqual(self.git('--git-dir', str(self.origin), 'rev-parse', alerts.REF), tip)
        self.sender.assert_called_once()

    def test_protected_refs_allow_atomic_advances_and_forbid_delete_and_rewind(self):
        hook = self.origin / 'hooks/pre-receive'
        hook.write_text("""#!/bin/sh
while read -r old new ref; do
  case "$ref" in
    refs/heads/goal-roi-telegram-auth-alert-state|refs/heads/goal-roi-telegram-auth-alert-witness)
      [ "$new" != 0000000000000000000000000000000000000000 ] || exit 1
      [ "$old" != 0000000000000000000000000000000000000000 ] || exit 1
      git merge-base --is-ancestor "$old" "$new" || exit 1
      ;;
  esac
done
""")
        hook.chmod(0o755)
        incident, state = self.detect()
        self.alerts.deliver(incident, state)
        tip, _ = self.storage.read()
        for ref in (alerts.REF, alerts.WITNESS):
            for refspec in (':' + ref, self.bootstrap + ':' + ref):
                result = subprocess.run(['git', '-C', str(self.checkout), 'push', '--force', 'origin', refspec],
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.git('--git-dir', str(self.origin), 'rev-parse', ref), tip)
        self.sender.assert_called_once()
        fixture = ROOT / 'ops/authorization-alert'
        exclusion = set((fixture / 'branch-cleanup-exclusions.txt').read_text().splitlines())
        policy = json.loads((fixture / 'ruleset.json').read_text())
        self.assertEqual(exclusion, {alerts.REF, alerts.WITNESS})
        self.assertEqual(set(policy['conditions']['ref_name']['include']), exclusion)
        self.assertEqual({r['type'] for r in policy['rules']}, {'creation', 'deletion', 'non_fast_forward'})
        self.assertEqual(policy['bypass_actors'], [])

    def test_restarted_worker_mentions_abandoned_without_resending(self):
        incident, state = self.detect()
        # Durable claim followed by SIGKILL: outcome is absent.
        def claim(current):
            current['incidents'][incident].update(status='attempting', attempt_id='a'*32,
                                                 reserved_at=self.time.isoformat(), claimed_at=self.time.isoformat())
            return True
        self.storage.update(claim)
        spool = self.root / 'spool'
        spool.mkdir()
        (spool / 'stop').touch()
        service = self.worker(alerts.GitState(self.clone('restarted')), self.sender)
        alerts.run(spool, service)
        snapshot = json.loads((spool / 'status.json').read_text())
        self.assertEqual(snapshot['current'], ['abandoned', incident])
        self.sender.assert_not_called()

    def worker(self, storage, sender):
        return alerts.Alerts(storage, 'synthetic', 'synthetic', sender, lambda: self.time.isoformat())

    def detect(self, service=None):
        service = service or self.alerts
        state = service.detect(self.time.isoformat(), REVOCATION)
        return state['active'], state

    def test_same_incident_after_48_hours_and_fresh_checkout(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        self.time += timedelta(hours=48)
        other = self.worker(alerts.GitState(self.clone('restart')), self.sender)
        again, state = self.detect(other)
        self.assertEqual(again, first)
        other.deliver(again, state)
        self.sender.assert_called_once()
        self.assertEqual(self.storage.read()[1]['incidents'][first]['status'], 'confirmed')

    def test_recovery_requires_matching_token_and_new_revocation_gets_new_id(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        self.time += timedelta(minutes=1)
        self.alerts.recover('a' * 32, self.time.isoformat())
        self.assertEqual(self.storage.read()[1]['active'], first)
        self.alerts.recover(first, self.time.isoformat())
        self.assertIsNone(self.storage.read()[1]['active'])
        self.assertIsNone(self.alerts.detect(NOW.isoformat(), REVOCATION)['active'])
        self.time += timedelta(minutes=1)
        second, state = self.detect()
        self.alerts.recover(first, self.time.isoformat())
        self.assertNotEqual(first, second)
        self.alerts.deliver(second, state)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(len(self.storage.read()[1]['incidents']), 2)

    def test_outcome_after_concurrent_authenticated_recovery_is_preserved(self):
        first, state = self.detect()
        def recovered_during_send(*_args):
            self.time += timedelta(seconds=1)
            self.alerts.recover(first, self.time.isoformat())
            self.time += timedelta(seconds=1)
            self.detect()
        self.sender.side_effect = recovered_during_send
        result = self.alerts.deliver(first, state)
        self.assertNotEqual(first, result['active'])
        self.assertEqual(result['incidents'][first]['status'], 'confirmed')
        self.assertIn('recovered_at', result['incidents'][first])

    def test_ref_unavailable_and_failed_reservation_prevent_send(self):
        with patch.object(self.storage, 'read', side_effect=alerts.StateUnavailable):
            with self.assertRaises(alerts.StateUnavailable):
                self.detect()
        incident, state = self.detect()
        with patch.object(self.storage, 'write', return_value=False):
            with self.assertRaises(alerts.StateUnavailable):
                self.alerts.deliver(incident, state)
        self.sender.assert_not_called()
        self.assertEqual(self.storage.read()[1]['incidents'][incident]['status'], 'detected')
        self.alerts.deliver(incident, state)
        self.sender.assert_called_once()

    def test_confirmation_publication_failure_can_be_retried_without_resend(self):
        incident, state = self.detect()
        real_write = self.storage.write
        def fail_confirmation(tip, current):
            if current['incidents'][incident]['status'] == 'confirmed':
                return False
            return real_write(tip, current)
        with patch.object(self.storage, 'write', side_effect=fail_confirmation):
            with self.assertRaises(alerts.StateUnavailable):
                self.alerts.deliver(incident, state)
        value = self.storage.read()[1]
        self.assertEqual(value['incidents'][incident]['status'], 'attempting')
        other = self.worker(alerts.GitState(self.clone('fresh')), self.sender)
        other.deliver(incident, value)
        self.alerts.flush_outcome()
        self.sender.assert_called_once()
        self.assertEqual(self.storage.read()[1]['incidents'][incident]['status'], 'confirmed')

    def test_lost_reply_and_known_rejection_remain_distinct_and_are_not_retried(self):
        for failure, expected in ((notify.DeliveryUncertain('synthetic'), 'uncertain'),
                                  (notify.DeliveryFailed('synthetic'), 'rejected')):
            with self.subTest(status=expected):
                self.time += timedelta(minutes=1)
                incident, state = self.detect()
                self.sender.side_effect = failure
                result = self.alerts.deliver(incident, state)
                self.assertEqual(result['incidents'][incident]['status'], expected)
                before = self.sender.call_count
                self.alerts.deliver(incident, result)
                self.assertEqual(self.sender.call_count, before)
                self.alerts.recover(incident, self.time.isoformat())

    def test_bot_unconfigured_leaves_incident_eligible_for_later_attempt(self):
        incident, state = self.detect()
        unconfigured = alerts.Alerts(self.storage, sender=self.sender, clock=lambda: self.time.isoformat())
        unconfigured.deliver(incident, state)
        self.sender.assert_not_called()
        self.alerts.deliver(incident, self.storage.read()[1])
        self.sender.assert_called_once()

    def test_cancellation_after_attempt_claim_is_uncertain(self):
        incident, state = self.detect()
        self.sender.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.alerts.deliver(incident, state)
        state = self.storage.read()[1]
        self.assertEqual(state['incidents'][incident]['status'], 'uncertain')
        self.alerts.deliver(incident, state)
        self.sender.assert_called_once()

    def test_independent_dispatch_failure_still_alerts(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        with patch.dict(os.environ, SCANGOAL_DATA_DIR=str(self.root / 'data')):
            env = {'TG_BOT_TOKEN': 'synthetic', 'TG_CHAT_ID': 'synthetic', 'NEXT_COLLECTION_FAILED': '1'}
            self.assertTrue(notify.failure(self.sender, self.time, env))
            self.assertFalse(notify.failure(self.sender, self.time, env))
        self.assertEqual(self.sender.call_count, 2)
        self.assertIn('perdeu autorização Telegram', self.sender.call_args_list[0].args[-1])
        self.assertIn('falhou às', self.sender.call_args_list[1].args[-1])

    def test_competing_processes_only_one_sends(self):
        targets = (self.checkout, self.clone('concurrent'))
        sent = self.root / 'sent'
        script = """
from pathlib import Path
import sys
from scangoal.authorization_alerts import Alerts, GitState
checkout, sent, at = sys.argv[1:]
def sender(*args):
    with open(sent, 'a') as out:
        out.write('attempt\\n')
worker = Alerts(GitState(Path(checkout)), 'synthetic', 'synthetic', sender)
state = worker.detect(at, 'SessionRevokedError')
worker.deliver(state['active'], state)
"""
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        with contextlib.ExitStack() as stack:
            processes = [stack.enter_context(subprocess.Popen(
                [sys.executable, '-c', script, str(target), str(sent), NOW.isoformat()],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)) for target in targets]
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(sent.read_text().splitlines(), ['attempt'])
        self.assertEqual(len(self.storage.read()[1]['incidents']), 1)


class DetectionHandoff(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_AUTH_ALERT_DIR': str(self.dir),
                                                 'GOAL_ROI_TELEGRAM_AUTH_ALERTS': 'on',
                                                 'SCANGOAL_DATA_DIR': str(self.dir / 'data'), 'TG_SCANGOAL': 'synthetic'},
                                     clear=True))

    def queued(self):
        return [json.loads(path.read_text()) for path in sorted(self.dir.glob('*.event'))]

    def test_default_off_and_invalid_values_leave_existing_diagnosis(self):
        for flag in (None, '', 'off', 'On', ' on', 'invalid'):
            with self.subTest(flag=flag), patch.dict(os.environ), patch.object(alerts, 'run') as worker:
                os.environ['GITHUB_REF'] = 'refs/heads/main'
                if flag is None:
                    os.environ.pop('GOAL_ROI_TELEGRAM_AUTH_ALERTS', None)
                else:
                    os.environ['GOAL_ROI_TELEGRAM_AUTH_ALERTS'] = flag
                marker = self.dir / 'github-output'
                marker.unlink(missing_ok=True)
                os.environ['GITHUB_OUTPUT'] = str(marker)
                with patch.object(reader, 'make_client', return_value=Client('connect', SessionRevokedError(request=None))):
                    with self.assertRaises(SessionRevokedError):
                        asyncio.run(reader.collect())
                self.assertEqual(marker.read_text(), 'authorization=lost\n')
                alerts.main()
                worker.assert_not_called()
                self.assertEqual(self.queued(), [])
                self.assertEqual(status.summarize(self.dir), [('disabled', '')])
                self.assertFalse((self.dir / 'stop').exists())

    def test_worker_launch_shell_uses_case_sensitive_gate(self):
        workflow = (ROOT / '.github/workflows/collect.yml').read_text()
        script = workflow.split('- name: Start Telegram authorization alert worker', 1)[1].split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines() if line.startswith('          '))
        with tempfile.TemporaryDirectory() as folder:
            worker_dir = Path(folder) / 'worker'
            for flag in ('', 'off', 'ON', ' on', 'invalid'):
                result = subprocess.run(['/bin/bash', '-e', '-c', script],
                                        env={'PATH': '/usr/bin:/bin', 'SCANGOAL_AUTH_ALERT_DIR': str(worker_dir),
                                             'GOAL_ROI_TELEGRAM_AUTH_ALERTS': flag}, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(worker_dir.exists())

    def test_summary_reports_all_states_and_keeps_degraded_outcomes_after_recovery(self):
        incident = 'a' * 32
        (self.dir / 'pid').write_text(str(os.getpid()))
        (self.dir / 'stopped').touch()
        output = self.dir / 'summary'
        with patch.dict(os.environ, GITHUB_STEP_SUMMARY=str(output)):
            reporter = status.Reporter(self.dir)
            for code in status.LABELS:
                if code == 'disabled':
                    continue
                reporter(code, incident)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    status.main()
                self.assertIn(status.LABELS[code], stdout.getvalue())
                self.assertIn(status.LABELS[code], output.read_text())
                if code in status.WARNINGS:
                    self.assertIn('::warning', stdout.getvalue())
            reporter('healthy')
            self.assertIn(('rejected', incident), status.summarize(self.dir))
            self.assertIn(('uncertain', incident), status.summarize(self.dir))
            self.assertIn(('abandoned', incident), status.summarize(self.dir))

    def test_summary_for_unavailable_and_stalled_worker_never_waits(self):
        (self.dir / 'pid').write_text(str(os.getpid()))
        status.Reporter(self.dir)('state_unavailable')
        events.loss(SessionRevokedError(request=None))
        start = time.monotonic()
        with patch('time.sleep', side_effect=AssertionError('unexpected drain wait')):
            notices = status.summarize(self.dir)
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue((self.dir / 'stop').exists())
        self.assertIn(('state_unavailable', ''), notices)
        self.assertIn(('worker_interrupted', ''), notices)
        workflow = (ROOT / '.github/workflows/collect.yml').read_text()
        self.assertIn('timeout-minutes: 18', workflow)
        self.assertNotRegex(workflow.split('Report Telegram authorization alert worker outcome')[1], r'(?:timeout|sleep|while)')
        self.assertLess(workflow.index('Start the next collection'), workflow.index('Report Telegram authorization alert worker outcome'))

    def test_summary_reports_worker_exit_even_with_live_pid_and_healthy_snapshot(self):
        (self.dir / 'pid').write_text(str(os.getpid()))
        status.Reporter(self.dir)('healthy')
        (self.dir / 'exited').touch()
        self.assertEqual(status.summarize(self.dir), [('healthy', ''), ('worker_interrupted', '')])

    def test_reader_connect_and_disconnect_emit_without_bot_credentials(self):
        for phase in ('connect', 'disconnect_error', 'background_on_disconnect'):
            failure = SessionRevokedError(request=None)
            client = Client('connect', failure) if phase == 'connect' else Client(**{phase: failure})
            with self.subTest(phase=phase), patch.object(reader, 'make_client', return_value=client):
                with self.assertRaises(SessionRevokedError) as raised:
                    asyncio.run(reader.collect())
                self.assertIs(raised.exception, failure)
                self.assertEqual(self.queued()[-1]['kind'], 'loss')
        self.assertEqual(len(self.queued()), 3)

    def test_reader_backfill_handoff_precedes_disconnect_and_preserves_receipt(self):
        store = self.dir / 'data'
        store.mkdir()
        (store / 'collection_windows.json').write_text(json.dumps({'full_high_id': 41, 'intervals': [
            {'after_id': 1, 'before_id': 10, 'cursor': 10, 'status': 'pending'}]}))
        client = Client('recover_page', SessionRevokedError(request=None))
        original_disconnect = client.disconnect
        async def close():
            self.assertEqual(self.queued()[-1]['kind'], 'loss')
            await original_disconnect()
        with patch.object(reader, 'make_client', return_value=client), patch.object(client, 'disconnect', close):
            with self.assertRaises(SessionRevokedError):
                asyncio.run(reader.collect())
        self.assertEqual(json.loads((store / 'state.json').read_text())['read'], 1)
        self.assertEqual(json.loads((store / 'collection_windows.json').read_text())['intervals'][0]['cursor'], 10)

    def test_watch_all_four_rounds_handoff_is_at_detection_before_final_refresh(self):
        for ordinal in range(1, 5):
            with self.subTest(ordinal=ordinal):
                for path in self.dir.glob('*.event'):
                    path.unlink()
                clock = [0]
                detections = []
                readings = 0
                async def sleep(seconds):
                    clock[0] += seconds
                async def read():
                    nonlocal readings
                    readings += 1
                    if readings == ordinal:
                        raise SessionRevokedError(request=None)
                    return 0
                def stamp():
                    detections.append(clock[0])
                    return NOW.isoformat()
                def refresh():
                    self.assertTrue(self.queued())
                with patch.object(events, 'utc_now', stamp), self.assertRaises(SessionRevokedError):
                    asyncio.run(watch.rounds(read, refresh, sleep=sleep, clock=lambda: clock[0]))
                self.assertEqual(detections, [120 * ordinal])
                self.assertEqual(clock[0], 120 * ordinal)

    def test_recovery_event_requires_token_before_authenticated_read(self):
        incident = 'f' * 32
        with patch.object(reader, 'make_client', return_value=Client()):
            asyncio.run(reader.collect())
        self.assertEqual(self.queued(), [])
        events.write_atomic(self.dir / 'incident', incident)
        with patch.object(reader, 'make_client', return_value=Client()):
            asyncio.run(reader.collect())
        self.assertEqual(self.queued()[0]['incident'], incident)
        self.assertEqual(self.queued()[0]['kind'], 'recovery')
        with patch.object(reader, 'make_client', return_value=Client()), patch.object(reader, 'RECENT', 0):
            asyncio.run(reader.collect())
        self.assertEqual(len(self.queued()), 1)

    def test_worker_drains_events_without_publication_or_dispatch_dependencies(self):
        events.loss(SessionRevokedError(request=None))
        (self.dir / 'stop').touch()
        storage = MemoryState()
        clock = VirtualClock()
        sender = Mock(side_effect=lambda *_: clock.advance(3))
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            alerts.run(self.dir, alerts.Alerts(storage, 'synthetic', 'synthetic', sender, clock.stamp), pause=clock.advance)
        sender.assert_called_once()
        self.assertTrue((self.dir / 'stopped').exists())
        self.assertEqual(self.queued(), [])
        self.assertIn('AUTH_ALERT_CONFIRMED', captured.getvalue())


class VirtualClock:
    def __init__(self):
        self.time = NOW

    def advance(self, seconds):
        self.time += timedelta(seconds=seconds)

    def stamp(self):
        return self.time.isoformat()


class MemoryState:
    def __init__(self):
        self.state = alerts.initial_state()

    def read(self):
        return '', self.state

    def update(self, change):
        change(self.state)
        return self.state

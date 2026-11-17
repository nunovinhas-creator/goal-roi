"""Silent failures (audit P1): a real failure of BSD, Highlightly, Telegram, the fast reads or the launch of the next
collection must not look like a successful run, and must not stop the collection, lose data or resend anything.

Exit codes of the entry points:  0 = done / skipped by the minimum interval / not configured / nothing to settle;
FAILED_EXIT (3) = real failure, already recorded. The workflow tolerates 3 with `continue-on-error` on those steps.
Everything is offline: urlopen, the Telegram sender, git and `gh` are replaced. Nothing is sent, no quota is used."""
import asyncio
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import test_scangoal as base
from test_telegram_presentation import CHAT, ENV, NOW, TOKEN, Data, base_state, consistent, entry, migrated, report
from scangoal import notify, results_bsd, results_hl, watch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'collect.yml'
BSD_NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


def exit_code(fn):
    out = io.StringIO()
    with redirect_stdout(out):
        try:
            fn()
        except SystemExit as exc:
            return exc.code, out.getvalue()
    raise AssertionError('entry point did not exit')


def steps():
    """{step name: its lines}, in workflow order."""
    out, name = {}, None
    for line in WORKFLOW.read_text(encoding='utf-8').split('\n'):
        if line.startswith('      - name: '):
            name = line[len('      - name: '):]
            out[name] = []
        elif name:
            out[name].append(line)
    return out


def http_error(code):
    return HTTPError('https://example.invalid', code, 'error', {}, None)


# ---------------------------------------------------------------------------------------------------------------------
# Problem A: BSD and Highlightly

class Sources(unittest.TestCase):
    setUp, tearDown, ev = base.BSD.setUp, base.BSD.tearDown, base.BSD.ev

    def path(self, name):
        return Path(self.dir.name) / name

    def main(self, module, env, urlopen):
        """The real entry point (main -> run -> request_json) at a fixed time, with urlopen replaced."""
        real = module.run
        with patch.dict(os.environ, env), patch.object(module, 'run', lambda: real(now=BSD_NOW)), \
                patch.object(module, 'urlopen', urlopen) as opener:
            code, out = exit_code(module.main)
        return code, out, opener

    def test_bsd_http_error_is_signalled_and_saved_results_are_kept(self):
        saved = {'results': {'old-match': {'ft': [1, 0], 'bsd_id': 1}}, 'near_misses': {}, 'stats': {},
                 'last_run_utc': (BSD_NOW - timedelta(hours=3)).isoformat()}
        self.path('results_bsd.json').write_text(json.dumps(saved), encoding='utf-8')
        before = self.path('results_bsd.json').read_bytes()
        for status in (500, 503, 429, 401):
            with self.subTest(status=status):
                code, out, opener = self.main(results_bsd, {'BSD_API_KEY': 'k'}, unittest.mock.Mock(side_effect=http_error(status)))
                self.assertEqual(code, results_bsd.FAILED_EXIT)
                self.assertEqual(out.strip(), 'BSD_FAILED HTTP %d' % status)
                self.assertEqual(opener.call_count, 1)                                  # one request, no retry
                self.assertEqual(self.path('results_bsd.json').read_bytes(), before)    # nothing lost, nothing rewritten
        code, out, _ = self.main(results_bsd, {'BSD_API_KEY': 'k'}, unittest.mock.Mock(side_effect=URLError('down')))
        self.assertEqual((code, out.strip()), (results_bsd.FAILED_EXIT, 'BSD_FAILED unavailable or invalid response'))

    def test_highlightly_http_error_is_signalled_and_saved_results_are_kept(self):
        saved = {'results': {'old-match': {'ft': [2, 2]}}, 'near_misses': {}, 'stats': {},
                 'last_run_utc': (BSD_NOW - timedelta(hours=3)).isoformat()}
        self.path('results_hl.json').write_text(json.dumps(saved), encoding='utf-8')
        before = self.path('results_hl.json').read_bytes()
        code, out, opener = self.main(results_hl, {'HIGHLIGHTLY_KEY': 'k'}, unittest.mock.Mock(side_effect=http_error(502)))
        self.assertEqual((code, out.strip(), opener.call_count), (results_hl.FAILED_EXIT, 'HL_FAILED HTTP 502', 1))
        self.assertEqual(self.path('results_hl.json').read_bytes(), before)

    def test_skipped_by_minimum_interval_is_not_a_failure_and_makes_no_request(self):
        for module, name, key in ((results_bsd, 'results_bsd.json', 'BSD_API_KEY'), (results_hl, 'results_hl.json', 'HIGHLIGHTLY_KEY')):
            with self.subTest(module=module.__name__):
                self.path(name).write_text(json.dumps({'results': {}, 'near_misses': {}, 'stats': {},
                                                       'last_run_utc': (BSD_NOW - timedelta(minutes=10)).isoformat()}))
                code, out, opener = self.main(module, {key: 'k'}, unittest.mock.Mock(side_effect=AssertionError('no request')))
                self.assertEqual(code, 0)
                self.assertTrue(out.strip().endswith('_SKIPPED_TOO_SOON'))
                opener.assert_not_called()

    def test_not_configured_is_not_a_failure(self):
        for module, key, word in ((results_bsd, 'BSD_API_KEY', 'BSD_NOT_CONFIGURED'), (results_hl, 'HIGHLIGHTLY_KEY', 'HL_NOT_CONFIGURED')):
            with self.subTest(module=module.__name__):
                code, out, opener = self.main(module, {key: ''}, unittest.mock.Mock(side_effect=AssertionError('no request')))
                self.assertEqual((code, out.strip()), (0, word))
                opener.assert_not_called()

    def test_result_not_available_yet_is_not_a_failure(self):
        class Reply:
            def __init__(self, body):
                self.body, self.headers = json.dumps(body).encode(), {}
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, n):
                return self.body[:n]
        code, out, _ = self.main(results_bsd, {'BSD_API_KEY': 'k'}, lambda req, timeout: Reply({'results': [], 'next': None, 'count': 0}))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('BSD_OK '))
        self.assertEqual(json.loads(self.path('results_bsd.json').read_text())['results'], {})
        code, out, _ = self.main(results_hl, {'HIGHLIGHTLY_KEY': 'k'}, lambda req, timeout: Reply({'data': [], 'pagination': {'totalCount': 0}}))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('HL_OK '))


# ---------------------------------------------------------------------------------------------------------------------
# Problem B: Telegram

class Telegram(unittest.TestCase):
    def data(self):
        return Data(self, migrated(base_state()), report(watch=[entry('m1'), entry('m2', minutes=-2), entry('m3', minutes=-4)]))

    def main(self, d, urlopen=None, env=ENV, publish=None):
        """The real CLI (main -> run_checked -> run -> send). The real `send` runs against a replaced urlopen."""
        real = notify.run
        before = {p.name: p.read_bytes() for p in d.path.iterdir() if p.name == 'report.json'}
        opener = unittest.mock.Mock(side_effect=urlopen or AssertionError('no network'))
        with ExitStack() as stack:
            stack.enter_context(patch.object(notify, 'run', lambda: real(now=NOW, env=env)))
            stack.enter_context(patch.object(notify, 'urlopen', opener))
            stack.enter_context(patch.object(notify.sys, 'argv', ['notify']))
            if publish is not None:
                stack.enter_context(patch.object(notify.GitPublisher, 'for_store', classmethod(lambda cls, store, branch: publish)))
            code, out = exit_code(notify.main)
        self.assertEqual({p.name: p.read_bytes() for p in d.path.iterdir() if p.name == 'report.json'}, before)   # report untouched
        notify.check_integrity(d.state(), d.log())
        consistent(self, d)
        return code, out, opener

    def ok_reply(self):
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, n):
                return b'{"ok": true}'
        return lambda req, timeout: Reply()

    def test_success_and_nothing_to_send_exit_zero(self):
        d = self.data()
        code, out, opener = self.main(d, self.ok_reply())
        self.assertEqual((code, opener.call_count), (0, 3))
        self.assertIn('NOTIFY_OK sent 3', out)
        code, out, opener = self.main(d, AssertionError('nothing left to send'))
        self.assertEqual((code, opener.call_count), (0, 0))

    def test_not_configured_exits_zero(self):
        d = self.data()
        code, out, opener = self.main(d, env={})
        self.assertEqual((code, out.strip()), (0, 'NOTIFY_NOT_CONFIGURED'))
        opener.assert_not_called()

    def test_failure_before_sending_is_signalled_and_never_resent(self):
        for error in (http_error(400), http_error(403), URLError(socket.gaierror('no dns')), URLError(ConnectionRefusedError())):
            with self.subTest(error=error):
                d = self.data()
                code, out, opener = self.main(d, error)
                self.assertEqual(code, notify.FAILED_EXIT)
                self.assertIn('NOTIFY_DELIVERY_FAILED', out)
                self.assertIn('NOTIFY_INCOMPLETE {"failed": 1}', out)
                self.assertIn('NOTIFY_FAILED DeliveryIncomplete', out)
                self.assertEqual(opener.call_count, 1)                    # the run stops at the first failure
                self.assertEqual(d.state()['delivery_counts'], {'failed': 1})
                code, out, opener = self.main(d, self.ok_reply())          # next run: the other two once, the failed one never
                self.assertEqual((code, opener.call_count), (0, 2))
                self.assertEqual(d.state()['delivery_counts'], {'failed': 1, 'delivered': 2})

    def test_uncertain_answer_is_signalled_and_never_resent(self):
        for error in (http_error(502), URLError(socket.timeout('timed out')), TimeoutError()):
            with self.subTest(error=error):
                d = self.data()
                code, out, opener = self.main(d, error)
                self.assertEqual(code, notify.FAILED_EXIT)
                self.assertIn('NOTIFY_INCOMPLETE {"uncertain": 1}', out)
                self.assertEqual(d.state()['delivery_counts'], {'uncertain': 1})
                code, _, opener = self.main(d, self.ok_reply())
                self.assertEqual((code, opener.call_count), (0, 2))
                self.assertEqual(d.state()['delivery_counts'], {'uncertain': 1, 'delivered': 2})

    def test_intent_not_published_is_signalled_and_nothing_is_sent(self):
        d = self.data()
        env = dict(ENV, NOTIFY_DURABLE='git', GITHUB_REF_NAME='main')
        code, out, opener = self.main(d, AssertionError('must not send'), env=env, publish=lambda key: False)
        self.assertEqual(code, notify.FAILED_EXIT)
        self.assertIn('NOTIFY_PUBLISH_FAILED', out)
        self.assertIn('NOTIFY_INCOMPLETE {"withdrawn": 1}', out)
        opener.assert_not_called()
        self.assertEqual([r['state'] for r in d.state()['deliveries'].values()], ['pending'] * 3)
        code, out, opener = self.main(d, self.ok_reply(), env=env, publish=lambda key: True)
        self.assertEqual((code, opener.call_count), (0, 3))                # withdrawn notice sent once, later

    def test_state_errors_and_misconfiguration_are_signalled(self):
        d = self.data()
        code, out, _ = self.main(d, env=dict(ENV, NOTIFY_DURABLE='yes'))
        self.assertEqual((code, out.split()[-2:]), (notify.FAILED_EXIT, ['NOTIFY_FAILED', 'NotifyError']))

    def test_earlier_problems_do_not_fail_a_later_clean_run(self):
        d = self.data()
        self.main(d, http_error(400))
        code, _, _ = self.main(d, self.ok_reply())
        code2, _, opener = self.main(d, AssertionError('nothing to send'))
        self.assertEqual((code, code2, opener.call_count), (0, 0, 0))

    def test_workflow_tolerates_the_notify_step_and_still_commits(self):
        s = steps()
        names = list(s)
        notify_step = "Telegram notices through the user's bot (skipped without keys)"
        self.assertIn('        continue-on-error: true', s[notify_step])
        self.assertEqual(names[names.index(notify_step) + 1], 'Commit data')
        self.assertFalse([l for l in s['Commit data'] if l.startswith('        if:')])   # unchanged: runs while the job succeeds


# ---------------------------------------------------------------------------------------------------------------------
# Problem C: fast reads

class FakeClient:
    def is_connected(self):
        return False

    async def connect(self):
        raise ConnectionError('telegram down')

    async def disconnect(self):
        pass


class FastReads(unittest.TestCase):
    ENVIRON = {'TG_API_ID': '1', 'TG_API_HASH': 'h', 'TG_SESSION': 's', 'TG_SCANGOAL': 'bot'}

    def rounds(self, results, report_error=None):
        t, problems = [0.0], []

        async def sleep(sec):
            t[0] += sec

        async def read():
            r = results.pop(0) if results else 0
            if isinstance(r, Exception):
                raise r
            return r

        def on_new():
            if report_error:
                raise report_error
        with redirect_stdout(io.StringIO()):
            done = asyncio.run(watch.rounds(read, on_new, sleep, lambda: t[0], 480, 120, problems=problems))
        return done, problems

    def test_problems_are_only_consecutive_failures_or_report_failures(self):
        self.assertEqual(self.rounds([0, 1, 0, 0]), (4, []))
        self.assertEqual(self.rounds([RuntimeError(), 2, 0, 0]), (3, []))                 # isolated, recovered: tolerated
        self.assertEqual(self.rounds([0, RuntimeError(), RuntimeError(), 5]), (1, ['reads_stopped']))
        self.assertEqual(self.rounds([1, 0, 0, 0], notify.DeliveryIncomplete('failed')), (4, ['report_failed']))

    def main(self, fake_watch=None):
        slept = []
        with patch.dict(os.environ, self.ENVIRON), patch.object(watch.time, 'sleep', slept.append), \
                patch.object(watch.reader, 'make_client', FakeClient), patch.object(watch, 'INTERVAL', 0), \
                patch.object(watch, 'WATCH_SECONDS', 0):
            if fake_watch:
                with patch.object(watch, 'watch', fake_watch):
                    code, out = exit_code(watch.main)
            else:
                code, out = exit_code(watch.main)
        return code, out, slept

    def test_reads_that_stop_after_two_failures_exit_with_failure_after_waiting(self):
        code, out, slept = self.main()                                         # the real watch(), its client failing
        self.assertEqual(code, watch.FAILED_EXIT)
        self.assertEqual(out.count('WATCH_READ_FAILED ConnectionError'), 2)
        self.assertIn('WATCH_PROBLEMS reads_stopped', out)
        self.assertEqual(len(slept), 1)                                        # the cadence wait still happens

    def test_clean_watch_and_not_configured_exit_zero(self):
        async def clean():
            return 4
        code, out, slept = self.main(clean)
        self.assertEqual((code, out.strip(), len(slept)), (0, 'WATCH_DONE reads 4', 1))
        with patch.dict(os.environ, {k: '' for k in self.ENVIRON}), patch.object(watch.time, 'sleep', slept.append):
            code, out = exit_code(watch.main)
        self.assertEqual((code, out.strip()), (0, 'WATCH_NOT_CONFIGURED'))

    def test_watch_that_cannot_start_exits_with_failure_after_waiting(self):
        async def broken():
            raise RuntimeError('session not authorized')
        code, out, slept = self.main(broken)
        self.assertEqual((code, out.strip(), len(slept)), (watch.FAILED_EXIT, 'WATCH_FAILED RuntimeError', 1))

    def test_refresh_reports_delivery_problems_of_the_fast_reads(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}), \
                patch('scangoal.report.build', return_value={'x': 1}), \
                patch.object(notify, 'run', side_effect=lambda: {'delivery_counts': {'uncertain': 1}}):
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with redirect_stdout(io.StringIO()) as out, self.assertRaises(notify.DeliveryIncomplete):
                    watch.refresh()
            finally:
                os.chdir(cwd)
        self.assertIn('NOTIFY_INCOMPLETE {"uncertain": 1}', out.getvalue())

    def test_workflow_keeps_the_fast_reads_tolerated_and_the_cadence(self):
        s = steps()
        fast = s['Fast reads every 2 minutes until the next collection (read-only)']
        self.assertIn('        continue-on-error: true', fast)
        self.assertIn('        run: timeout --signal=TERM --kill-after=10s 540s python -u -m scangoal.watch', fast)
        start = '\n'.join(s['Start the next collection'])
        self.assertIn('if [ "${{ steps.fast.outcome }}" = "skipped" ]; then sleep 480; fi', start)   # failure != skipped


# ---------------------------------------------------------------------------------------------------------------------
# Phase 3: the launch of the next collection

class NextCollection(unittest.TestCase):
    def test_failure_notice_runs_after_the_launch_and_sees_its_failure(self):
        s = steps()
        names = list(s)
        notice = next(n for n in names if n.startswith('Tell the user when a collection fails'))
        self.assertEqual(names[-1], notice)                                       # last step
        self.assertEqual(names[-2], 'Start the next collection')
        self.assertIn('        id: next', s['Start the next collection'])
        self.assertIn('        if: ${{ failure() }}', s[notice])
        self.assertIn("          NEXT_COLLECTION_FAILED: ${{ steps.next.outcome == 'failure' && '1' || '' }}", s[notice])
        self.assertIn('        run: python -u -m scangoal.notify --failure', s[notice])
        self.assertEqual(sum(n.startswith('Tell the user') for n in names), 1)       # one mechanism, not two

    def test_no_new_schedule_dispatch_or_concurrency_change(self):
        text = WORKFLOW.read_text(encoding='utf-8')
        self.assertEqual(re.findall(r"cron: '([^']+)'", text), ['3,13,23,33,43,53 * * * *'])
        self.assertEqual(text.count('gh workflow run'), 1)
        self.assertIn('concurrency:\n  group: scangoal-write\n  cancel-in-progress: false', text)
        self.assertEqual(sorted(p.name for p in WORKFLOW.parent.iterdir()), ['collect.yml', 'tests.yml'])

    @unittest.skipUnless(shutil.which('bash'), 'bash required')
    def test_a_failed_launch_fails_the_step(self):
        """The step's script, run like Actions runs it (bash -e -o pipefail), with `gh` replaced by a failing stub."""
        lines = steps()['Start the next collection']
        script = '\n'.join(l[10:] for l in lines[lines.index('        run: |') + 1:] if l.startswith('          '))
        script = script.replace('${{ steps.fast.outcome }}', 'success')
        with tempfile.TemporaryDirectory() as d:
            for name, body in (('gh', 'echo "HTTP 500" >&2; exit 1'), ('sleep', 'exit 0')):
                stub = Path(d) / name
                stub.write_text('#!/bin/sh\n' + body + '\n')
                stub.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ['PATH'], GITHUB_REPOSITORY='owner/repo')
            failed = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', script], env=env, capture_output=True)
            (Path(d) / 'gh').write_text('#!/bin/sh\nexit 0\n')
            ok = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', script], env=env, capture_output=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(ok.returncode, 0)

    def test_failure_notice_bypasses_the_hourly_limit_only_when_the_chain_broke(self):
        sent = []
        sender = lambda token, chat, text: sent.append((token, chat, text))
        at = datetime(2026, 11, 17, 14, 37, tzinfo=timezone.utc)                  # outside the first 10 minutes
        self.assertFalse(notify.failure(sender, at, ENV))
        self.assertFalse(notify.failure(sender, at, dict(ENV, NEXT_COLLECTION_FAILED='')))
        self.assertTrue(notify.failure(sender, at, dict(ENV, NEXT_COLLECTION_FAILED='1')))
        self.assertTrue(notify.failure(sender, at.replace(minute=4), ENV))       # unchanged hourly rule
        self.assertFalse(notify.failure(sender, at, {'NEXT_COLLECTION_FAILED': '1'}))   # no keys: nothing
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], (TOKEN, CHAT, 'A recolha do goal-roi falhou às 14:37 UTC. Vê as execuções em '
                                                'https://github.com/nunovinhas-creator/goal-roi/actions'))   # same text


def _playwright():
    if not shutil.which('node'):
        return False
    return subprocess.run(['node', '-e', "require('playwright')"], capture_output=True).returncode == 0


class Dashboard(unittest.TestCase):
    @unittest.skipUnless(_playwright(), 'node + playwright (Chromium) required; offline, every request is routed')
    def test_one_coherent_snapshot_across_refreshes(self):
        r = subprocess.run(['node', 'tests/dashboard_refresh.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Dashboard refresh regressions passed', r.stdout)


class Steps(unittest.TestCase):
    def test_only_complementary_steps_are_tolerated(self):
        tolerated = sorted(n for n, lines in steps().items() if '        continue-on-error: true' in lines)
        self.assertEqual(tolerated, ['Commit data from the fast reads',
                                     'Fast reads every 2 minutes until the next collection (read-only)',
                                     'Results from BSD (hourly at most; skipped without key)',
                                     'Results from Highlightly (every 90 minutes at most; skipped without key)',
                                     "Telegram notices through the user's bot (skipped without keys)"])


if __name__ == '__main__':
    unittest.main()

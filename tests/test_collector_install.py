"""Inert installation of the BSD recovery hooks: nothing is activated.

Checks that the normal BSD collection, its output and the collector workflow keep
their behaviour, that the optional hooks are unused by the entry point, and that the
workflow changes are exactly the audited ones. No network: urlopen is replaced.
"""
import inspect
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import test_scangoal as base
from scangoal import results_bsd

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SHA = '11d5960a326750d5838078e36cf38b85af677262'      # actions/checkout v4 == v4.4.0
SETUP_PYTHON_SHA = 'a26af69be951a213d495a4c3e4e4022e16d87065'  # actions/setup-python v5 == v5.6.0
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, body, headers):
        self.body, self.headers = body, headers
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.body[:n]


class InertHooks(unittest.TestCase):
    setUp, tearDown, ev, run_bsd = base.BSD.setUp, base.BSD.tearDown, base.BSD.ev, base.BSD.run_bsd

    def page(self):
        return {'results': [self.ev(10, 'Barnsley FC', 'Oldham', '2026-11-11T19:00:00Z', 2, 1)], 'next': None}

    def test_normal_collection_and_output_without_callback(self):
        out = io.StringIO()
        with redirect_stdout(out):
            state, calls = self.run_bsd([self.page()])          # two-argument fetch, as today
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['results']['barnsley-vs-oldham-20261111']['ft'], [2, 1])
        saved = json.loads((Path(self.dir.name)/'results_bsd.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, state)
        self.assertEqual([l.split()[0] for l in out.getvalue().splitlines()], ['BSD_OK'])

    def test_callback_runs_after_saved_state_and_cannot_change_or_fail_it(self):
        seen = []
        def tail():
            seen.append(json.loads((Path(self.dir.name)/'results_bsd.json').read_text(encoding='utf-8')))
            raise RuntimeError('trial failure')
        out = io.StringIO()
        with redirect_stdout(out):
            state = results_bsd.run(fetch=lambda url, token: self.page(), now=NOW, token='k', after_bsd=tail)
        self.assertEqual(seen, [state])                          # called once, after the state is on disk
        self.assertIn('BSD_RECOVERY_TAIL_SKIPPED', out.getvalue())
        with tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': other}):
            (Path(other)/'messages.jsonl').write_bytes((Path(self.dir.name)/'messages.jsonl').read_bytes())
            with redirect_stdout(io.StringIO()):
                plain = results_bsd.run(fetch=lambda url, token: self.page(), now=NOW, token='k')
        self.assertEqual(plain, state)

    def test_callback_not_called_when_bsd_skips(self):
        called = []
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(results_bsd.run(token='', after_bsd=lambda: called.append(1)))
            self.run_bsd([self.page()])
            results_bsd.run(fetch=lambda url, token: self.page(), now=NOW.replace(minute=20), token='k',
                            after_bsd=lambda: called.append(1))       # BSD_SKIPPED_TOO_SOON
        self.assertEqual(called, [])

    def test_request_json_observe_is_optional_and_never_alters_the_reply(self):
        body = json.dumps({'results': [], 'next': None}).encode()
        headers = {'RateLimit': '"football";r=7400;t=100', 'RateLimit-Policy': '"football";q=7500;w=86400',
                   'Set-Cookie': 'session=1', 'X-Echo': 'Token secret-key'}
        url = results_bsd.ROOT + '?offset=0'
        with patch.object(results_bsd, 'urlopen', side_effect=lambda req, timeout: Response(body, headers)) as opener:
            plain = results_bsd.request_json(url, 'secret-key')
            seen = []
            observed = results_bsd.request_json(url, 'secret-key', observe=lambda h, b, t: seen.append((h, b, t)))
            def broken(*args):
                raise ValueError('observer bug')
            out = io.StringIO()
            with redirect_stdout(out):
                guarded = results_bsd.request_json(url, 'secret-key', observe=broken)
        self.assertEqual(plain, observed)
        self.assertEqual(plain, guarded)
        self.assertEqual(opener.call_args.kwargs['timeout'], 20)
        self.assertEqual(set(seen[0][0]), {'RateLimit', 'RateLimit-Policy'})   # only quota headers, never the key
        self.assertNotIn('secret-key', json.dumps(seen[0][0]))
        self.assertEqual(seen[0][1], body)
        self.assertEqual(out.getvalue().strip(), 'BSD_RECOVERY_QUOTA_UNAVAILABLE')
        error = HTTPError(url, 429, 'Too Many Requests', {}, None)
        with patch.object(results_bsd, 'urlopen', side_effect=error), self.assertRaisesRegex(results_bsd.BSDError, '^HTTP 429$'):
            results_bsd.request_json(url, 'secret-key', observe=lambda *a: self.fail('no reply, no observation'))

    def test_entry_point_never_activates_recovery(self):
        self.assertNotIn('after_bsd', inspect.getsource(results_bsd.main))
        self.assertIn('run()', inspect.getsource(results_bsd.main))
        source = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'scangoal').glob('*.py'))
        for forbidden in ('recovery_tail', 'PreparedTail', 'recovery_oidc', 'bsd-trial-journal'):
            self.assertNotIn(forbidden, source)


class WorkflowInstall(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT/'.github/workflows/collect.yml').read_text(encoding='utf-8')

    def test_only_reviewed_workflows_and_manual_quota_initialization(self):
        workflows = ROOT/'.github/workflows'
        initializer = 'initialize-workers-ai-budget.yml'
        self.assertEqual(sorted(p.name for p in workflows.iterdir()),
                         ['collect.yml', 'continuity.yml', initializer, 'tests.yml'])
        text = (workflows/initializer).read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'^on: (.+)$', text, re.M), ['workflow_dispatch'])
        self.assertIn('permissions:\n  contents: write\n\njobs:', text)
        self.assertNotIn('scangoal-write', text)

    def test_permissions_timeout_and_start_stamp(self):
        self.assertIn('permissions:\n  contents: write\n  actions: write\n'
                      '  # BSD recovery tail only: OIDC attestation of journal events. Adds no repository access.\n'
                      '  id-token: write\nconcurrency:', self.text)
        self.assertEqual(re.findall(r'^    timeout-minutes: (\d+)', self.text, re.M), ['18'])   # job level
        steps = self.text.split('    steps:\n', 1)[1]
        self.assertTrue(steps.startswith('      # BSD recovery tail only'))
        self.assertLess(steps.index('GOAL_ROI_JOB_STARTED_UTC'), steps.index('actions/checkout@'))

    def test_external_actions_pinned_to_audited_shas_and_credentials_persisted(self):
        production = self.text
        self.assertNotIn('\n  validate:\n', production)   # the journal validation job left with the external journal
        uses = re.findall(r'uses: ([^\s#]+)', production)
        self.assertEqual(uses, ['actions/checkout@'+CHECKOUT_SHA, 'actions/setup-python@'+SETUP_PYTHON_SHA,
                                'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'])
        self.assertNotIn('persist-credentials', production)     # default true: notify durability pushes
        self.assertNotIn('DURABLE_URL', production)
        self.assertNotIn('DURABLE_TOKEN', production)
        self.assertNotIn('\n  push:', production)

    def test_schedule_chain_and_normal_steps_unchanged(self):
        for line in ("    - cron: '3,13,23,33,43,53 * * * *'",
                     "  group: scangoal-write\n  cancel-in-progress: false",
                     "github.ref == 'refs/heads/main' }}",
                     'run: python -u -m scangoal.results_bsd',
                     'run: python -u -m scangoal.notify',
                     'timeout 85s python scripts/dispatch_collection.py',
                     'run: timeout --signal=TERM --kill-after=10s 540s python -u -m scangoal.watch'):
            self.assertIn(line, self.text)
        steps = self.text.split('    steps:\n', 1)[1]
        code = [l.split('#', 1)[0] for l in steps.splitlines() if not l.lstrip().startswith('#')]
        self.assertFalse([l for l in code if 'recovery' in l])   # no step runs recovery code

    def test_requirements_add_only_pinned_cryptography(self):
        self.assertEqual((ROOT/'requirements.txt').read_text().split(), ['Telethon==1.45.0', 'cryptography==50.0.0'])


if __name__ == '__main__':
    unittest.main()

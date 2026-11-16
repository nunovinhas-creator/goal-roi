"""OIDC attestation of the BSD trial journal: adversarial offline tests.

Real RSA signatures (pyca/cryptography) with locally generated keys. No network:
the official JWKS fetch is always injected. No sports API is reachable.
"""
import base64
import json
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import test_recovery_inline as baseline
import test_recovery_tail as tailtests
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from scangoal import recovery_oidc as oidc
from scangoal import recovery_tail as tail
from scangoal.recovery_inline import InlineSlots
from scangoal.recovery_trial import TRIAL_ID, TrialBlocked
from scangoal.recovery_trial_journal import OfflineGitStore
from scangoal.recovery_trial_transport import OfflineWire, PreparedBSD, WireResponse

DAY = baseline.DAY
KEYS = {name: rsa.generate_private_key(public_exponent=65537, key_size=2048) for name in ('gh-1', 'gh-2', 'evil')}


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def jwk(name):
    numbers = KEYS[name].public_key().public_numbers()
    return {'kty':'RSA','alg':'RS256','use':'sig','kid':name,
            'n':b64(numbers.n.to_bytes(256, 'big')),'e':b64(numbers.e.to_bytes(3, 'big'))}


def sign(claims, key='gh-1', kid='gh-1', header=None):
    head = b64(json.dumps(header or {'alg':'RS256','typ':'JWT','kid':kid}).encode())
    body = b64(json.dumps(claims).encode())
    signature = KEYS[key].sign((head+'.'+body).encode(), padding.PKCS1v15(), hashes.SHA256())
    return head+'.'+body+'.'+b64(signature)


def claims(aud, run='100', attempt='1', iat=None):
    iat = int(time.time()) if iat is None else iat
    return dict(iss=oidc.ISSUER, aud=aud, sub=oidc.SUBJECT, repository=oidc.REPOSITORY,
                repository_owner=oidc.OWNER, repository_id=oidc.REPOSITORY_ID,
                repository_owner_id=oidc.OWNER_ID, ref='refs/heads/main', ref_type='branch',
                sha='a'*40, workflow='Collect ScanGoal', workflow_ref=oidc.WORKFLOW_REF, workflow_sha='a'*40,
                event_name='workflow_dispatch', run_id=run, run_number='700', run_attempt=attempt,
                runner_environment='github-hosted', actor='github-actions[bot]', iat=iat, nbf=iat, exp=iat+300)


class FakeAttestor:
    """Stands in for the runner endpoint; signs like GitHub with the test key."""
    def __init__(self, run='100', attempt='1', fail_after=None):
        self.run, self.attempt, self.fail_after, self.calls, self.not_after = run, attempt, fail_after, 0, None
    def token(self, aud):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise TrialBlocked('oidc_request_failed')
        return sign(claims(aud, self.run, self.attempt))


def offline_only(*args):
    raise AssertionError('JWKS fetch must not be needed')


class OIDCJournalTests(unittest.TestCase):
    body = tailtests.TailTests.body
    targets = tailtests.TailTests.targets
    collector = tailtests.TailTests.collector

    def setUp(self):
        baseline.InlineTests.setUp(self)
        self.config = dict(self.config, attestation=oidc.MODE)
        self.store, self.anchor = OfflineGitStore.bootstrap_fixture(self.root/'attested.git', self.config)
        self.slots = self.make_slots()

    def make_slots(self, attestor=None, pinned=None, fetch=offline_only, receipt=None):
        return InlineSlots(self.store, self.anchor, self.out, receipt or self.receipt, 4,
                           attestor=attestor or FakeAttestor(),
                           verifier=oidc.Verifier({'gh-1':jwk('gh-1')} if pinned is None else pinned, fetch))

    def fresh_load(self, **kw):
        return self.make_slots(**kw).load()

    def event(self, **fields):
        base = dict(kind='reserve', operation_id='forged', owner='100:1', day=DAY, offset=0,
                    started_utc=self.now.isoformat(), attested_utc=datetime.now(timezone.utc).isoformat(),
                    quota=dict(self.receipt.__dict__, received_utc=self.receipt.received_utc.isoformat()))
        base.update(fields)
        return base

    def forge(self, event, mutate=None, aud=None, **sign_kw):
        """Attacker with Git write access appends a fast-forward commit."""
        head = self.store.head()
        c = claims(aud or oidc.audience(head, event))
        if mutate:
            mutate(c)
        candidate = self.store.propose(head, dict(event, attestation={'mode':oidc.MODE,'jwt':sign(c, **sign_kw)}))
        self.assertTrue(self.store.cas(head, candidate))

    def assert_rejected(self, reason, **kw):
        with self.assertRaisesRegex(TrialBlocked, reason):
            self.fresh_load(**kw)

    # 1
    def test_legitimate_attested_page_replays_in_new_verifier(self):
        PreparedBSD(OfflineWire([WireResponse(200, self.body())]), self.slots, '100:1', 'fixture-token', self.out)('bsd', DAY, 0, 200)
        _, state = self.fresh_load()
        attempt = next(iter(state['attempts'].values()))
        self.assertEqual(attempt['outcome'], 'complete')
        self.assertEqual(attempt['attestation']['mode'], oidc.MODE)
        self.assertTrue(tail.replay(self.make_slots(), self.targets())['accepted'])

    # 2
    def test_forged_signature_with_genuine_kid_rejected(self):
        self.forge(self.event(), key='evil', kid='gh-1')
        self.assert_rejected('signature_invalid')

    # 3
    def test_audience_not_bound_to_event_rejected(self):
        self.forge(self.event(), aud=oidc.AUD_PREFIX+'0'*64)
        self.assert_rejected('identity_rejected')

    # 4
    def test_event_altered_after_token_and_token_replay_rejected(self):
        head = self.store.head(); event = self.event()
        token = sign(claims(oidc.audience(head, event)))
        candidate = self.store.propose(head, dict(event, offset=200, attestation={'mode':oidc.MODE,'jwt':token}))
        self.store.cas(head, candidate)
        self.assert_rejected('identity_rejected')

    def test_genuine_event_copied_to_another_position_rejected(self):
        self.reserve()
        _, genuine = self.store.read(self.store.head())
        head = self.store.head()
        self.store.cas(head, self.store.propose(head, genuine))   # same bytes, new parent
        self.assert_rejected('identity_rejected')

    # 5-9
    def test_wrong_repository_branch_workflow_or_run_rejected(self):
        cases = {
            'identity_rejected': [
                lambda c: c.update(repository='attacker/goal-roi'),
                lambda c: c.update(ref='refs/heads/feature', sub='repo:'+oidc.REPOSITORY+':ref:refs/heads/feature',
                                   workflow_ref=oidc.REPOSITORY+'/.github/workflows/collect.yml@refs/heads/feature'),
                lambda c: c.update(event_name='pull_request', ref='refs/pull/31/merge',
                                   sub='repo:'+oidc.REPOSITORY+':pull_request',
                                   workflow_ref=oidc.REPOSITORY+'/.github/workflows/tests.yml@refs/pull/31/merge'),
                lambda c: c.update(workflow_ref=oidc.REPOSITORY+'/.github/workflows/tests.yml@refs/heads/main'),
            ],
            'run_mismatch': [lambda c: c.update(run_id='999'), lambda c: c.update(run_attempt='2')],
        }
        for reason, mutations in cases.items():
            for mutate in mutations:
                with self.subTest(reason=reason):
                    self.setUp()
                    self.forge(self.event(), mutate)
                    self.assert_rejected(reason)

    def test_immutable_subject_is_the_only_accepted_form(self):
        expected = 'repo:nunovinhas-creator@278368112/goal-roi@1454375710:ref:refs/heads/main'
        self.assertEqual(oidc.SUBJECT, expected)                       # 1: exact documented form
        verifier = oidc.Verifier({'gh-1':jwk('gh-1')}, offline_only)
        aud = oidc.AUD_PREFIX+'3'*64
        now = datetime.now(timezone.utc)
        self.assertEqual(verifier.verify(sign(claims(aud)), aud, now, '100:1'), '100:1')
        bad_owner = 'repo:nunovinhas-creator@1/goal-roi@1454375710:ref:refs/heads/main'
        bad_repo = 'repo:nunovinhas-creator@278368112/goal-roi@1:ref:refs/heads/main'
        cases = {
            'owner id wrong (claim + subject)': dict(sub=bad_owner, repository_owner_id='1'),        # 2
            'repository id wrong (claim + subject)': dict(sub=bad_repo, repository_id='1'),        # 3
            'legacy name-only subject': dict(sub='repo:nunovinhas-creator/goal-roi:ref:refs/heads/main'),   # 4
            'customized template': dict(sub='repo:nunovinhas-creator@278368112/goal-roi@1454375710:environment:prod'),
            'suffix appended': dict(sub=expected+':job_workflow_ref:x'),
            'claim ids inconsistent with subject': dict(repository_id='999', repository_owner_id='888'),  # 6
            'subject ids inconsistent with claims': dict(sub=bad_repo),
            'repository_id missing': dict(repository_id=None),
        }
        for name, change in cases.items():
            c = claims(aud); c.update(change)
            c = {k:v for k, v in c.items() if v is not None}
            with self.subTest(name), self.assertRaisesRegex(TrialBlocked, 'identity_rejected|claims_incomplete'):
                verifier.verify(sign(c), aud, now, '100:1')
        # 5: subject edited after signing (attacker rewrites the payload, keeps the signature)
        head, body, sig = sign(claims(aud)).split('.')
        forged = dict(claims(aud), sub='repo:attacker@1/goal-roi@1:ref:refs/heads/main')
        with self.assertRaisesRegex(TrialBlocked, 'signature_invalid'):
            verifier.verify(head+'.'+b64(json.dumps(forged).encode())+'.'+sig, aud, now, '100:1')

    def test_wrong_ids_rejected_in_the_journal(self):
        for change in (dict(repository_owner_id='1'), dict(repository_id='1'),
                       dict(sub='repo:nunovinhas-creator/goal-roi:ref:refs/heads/main')):
            with self.subTest(change=change):
                self.setUp()
                self.forge(self.event(), lambda c, change=change: c.update(change))
                self.assert_rejected('identity_rejected')

    # 10
    def test_missing_or_unexpected_claims_and_headers_rejected(self):
        cases = [
            ('claims_incomplete', dict(mutate=lambda c: c.pop('workflow_ref'))),
            ('identity_rejected', dict(mutate=lambda c: c.update(environment='production'))),
            ('identity_rejected', dict(mutate=lambda c: c.update(job_workflow_ref='other/repo/.github/workflows/x.yml@refs/heads/main'))),
            ('claims_incomplete', dict(mutate=lambda c: c.update(aud=[c['aud']]))),
            ('identity_rejected', dict(mutate=lambda c: c.update(runner_environment='self-hosted'))),
            ('identity_rejected', dict(mutate=lambda c: c.update(iss='https://attacker.example'))),
            ('header_rejected', dict(header={'alg':'none','typ':'JWT','kid':'gh-1'})),
            ('header_rejected', dict(header={'alg':'RS256','typ':'JWT','kid':'gh-1','jwk':jwk('evil')})),
            ('header_rejected', dict(header={'alg':'RS256','typ':'JWT','kid':'gh-1','jku':'https://attacker.example/jwks'})),
        ]
        for reason, kw in cases:
            with self.subTest(reason=reason, kw=str(kw)[:60]):
                self.setUp()
                self.forge(self.event(), **kw)
                self.assert_rejected(reason)

    def test_alg_confusion_hs256_with_public_key_rejected(self):
        head = self.store.head(); event = self.event()
        h = b64(json.dumps({'alg':'HS256','typ':'JWT','kid':'gh-1'}).encode())
        c = b64(json.dumps(claims(oidc.audience(head, event))).encode())
        mac = hmac.HMAC(json.dumps(jwk('gh-1')).encode(), hashes.SHA256()); mac.update((h+'.'+c).encode())
        token = h+'.'+c+'.'+b64(mac.finalize())
        self.store.cas(head, self.store.propose(head, dict(event, attestation={'mode':oidc.MODE,'jwt':token})))
        self.assert_rejected('header_rejected')

    def test_unattested_event_rejected(self):
        head = self.store.head()
        self.store.cas(head, self.store.propose(head, self.event()))
        self.assert_rejected('attestation_missing')

    # 11
    def test_expired_token_valid_in_history_but_never_for_new_write(self):
        verifier = oidc.Verifier({'gh-1':jwk('gh-1')}, offline_only)
        issued = int(time.time())-3*86400
        aud = oidc.AUD_PREFIX+'1'*64
        token = sign(claims(aud, iat=issued))
        stamp = datetime.fromtimestamp(issued-10, timezone.utc)
        self.assertEqual(verifier.verify(token, aud, stamp, '100:1'), '100:1')   # replay days later
        with self.assertRaisesRegex(TrialBlocked, 'not_fresh'):
            verifier.verify(token, aud, stamp, '100:1', now=datetime.now(timezone.utc))
        with self.assertRaisesRegex(TrialBlocked, 'time_rejected'):   # event far from issuance
            verifier.verify(token, aud, stamp-timedelta(hours=1), '100:1')
        class Stale(FakeAttestor):
            def token(self, aud):
                return sign(claims(aud, iat=int(time.time())-3600))
        with self.assertRaises(TrialBlocked):
            self.make_slots(attestor=Stale()).reserve('one', '100:1', DAY, 0, self.now)
        self.assertEqual(self.fresh_load()[1]['attempts'], {})

    # 12
    def test_key_rotation_pinned_history_and_untrusted_keys(self):
        self.reserve()
        self.assertEqual(len(self.fresh_load()[1]['attempts']), 1)              # pinned, no network
        self.assert_rejected('key_unknown_unverifiable', pinned={}, fetch=lambda *a: {'gh-2':jwk('gh-2')})
        def provider_down(*args):
            raise TrialBlocked('oidc_keys_unavailable')
        self.assert_rejected('keys_unavailable', pinned={}, fetch=provider_down)
        self.assertEqual(len(self.fresh_load(pinned={}, fetch=lambda *a: {'gh-1':jwk('gh-1')})[1]['attempts']), 1)
        # Attacker key carried in the journal is never consulted.
        self.forge(self.event(operation_id='x2', owner='101:1', offset=200, trusted_jwks={'evil':jwk('evil')}),
                   lambda c: c.update(run_id='101'), key='evil', kid='evil')
        self.assert_rejected('key_unknown_unverifiable', fetch=lambda *a: {'gh-1':jwk('gh-1')})

    def reserve(self, slots=None, op='one', owner='100:1', day=DAY, offset=0):
        return (slots or self.slots).reserve(op, owner, day, offset, self.now)

    # 13
    def test_forged_complete_with_invented_bytes_never_accepted(self):
        self.reserve()
        raw = self.body()
        import hashlib
        forged = dict(kind='complete', operation_id='one', owner='100:1', outcome='complete',
                      response_sha256=hashlib.sha256(raw).hexdigest(), observed_utc=self.now.isoformat(),
                      raw_b64=base64.b64encode(raw).decode(), attested_utc=datetime.now(timezone.utc).isoformat(),
                      metadata=dict(evidence_kind='synthetic_wire', observed_utc=self.now.isoformat(),
                                    response_sha256=hashlib.sha256(raw).hexdigest(), params={'status':'finished'}))
        self.forge(forged, key='evil', kid='gh-1')
        self.assert_rejected('signature_invalid')
        with self.assertRaises(TrialBlocked):
            tail.replay(self.make_slots(), self.targets())
        self.assertFalse((self.out/'engine-state.json').exists())

    # 14
    def test_forged_abandon_rejected_and_no_refund(self):
        self.reserve()
        proof = json.loads(self.terminal())
        abandon = dict(kind='abandon', operation_id='one', terminal_run=proof,
                       proof_sha256='0'*64, proof_raw_b64='', attested_utc=datetime.now(timezone.utc).isoformat())
        self.forge(abandon, key='evil', kid='gh-1')
        self.assert_rejected('signature_invalid')
        self.setUp(); self.reserve()
        self.forge(dict(abandon), lambda c: c.update(workflow_ref=oidc.REPOSITORY+'/.github/workflows/tests.yml@refs/heads/main'))
        self.assert_rejected('identity_rejected')

    terminal = baseline.InlineTests.terminal

    def test_genuine_attested_abandon_keeps_debit(self):
        self.reserve()
        self.make_slots(attestor=FakeAttestor('101', '1')).abandon('one', self.terminal())
        _, state = self.fresh_load()
        self.assertEqual(state['attempts']['one']['outcome'], 'abandoned')
        self.assertFalse(self.reserve()['send_permitted'])

    # 15
    def test_attestation_failure_after_reservation_burns_slot_no_resend(self):
        slots = self.make_slots(attestor=FakeAttestor(fail_after=1))
        wire = OfflineWire([WireResponse(200, self.body()), WireResponse(200, self.body())])
        with self.assertRaisesRegex(TrialBlocked, 'oidc_request_failed'):
            PreparedBSD(wire, slots, '100:1', 'fixture-token', self.out)('bsd', DAY, 0, 200)
        _, state = self.fresh_load()
        self.assertEqual([a['outcome'] for a in state['attempts'].values()], ['reserved'])
        self.assertIsNotNone(state['lease'])
        with self.assertRaises(TrialBlocked):
            PreparedBSD(wire, self.make_slots(), '100:1', 'fixture-token', self.out)('bsd', DAY, 0, 200)
        self.assertEqual(len(wire.calls), 1)

    # 16
    def test_sixth_attempt_blocked_with_attestation(self):
        self.config = dict(self.config, prior_debits=4)
        self.store, self.anchor = OfflineGitStore.bootstrap_fixture(self.root/'cap.git', self.config)
        receipt = replace(self.receipt, slot_debits=4)
        self.assertTrue(self.reserve(self.make_slots(receipt=receipt))['send_permitted'])
        later = replace(receipt, run_owner='101:1', remaining=104)
        with self.assertRaises(TrialBlocked):
            self.reserve(self.make_slots(attestor=FakeAttestor('101'), receipt=later), op='two', owner='101:1',
                         day='2026-11-15')
        state = self.fresh_load()[1]
        self.assertEqual(len(state['attempts'])+state['config']['prior_debits'], 5)

    # 17
    def test_oidc_failure_never_harms_normal_collection(self):
        ns, data = self.collector('oidc-down')
        def normal(url, token, observe=None):
            if observe:
                observe(self.headers, b'{}', self.now)
            return {'results':[], 'count':0}
        slots = self.make_slots(attestor=FakeAttestor(fail_after=0))
        executor = tail.PreparedTail(slots, '100:1', OfflineWire([WireResponse(200, self.body())]), 'fixture-token',
            self.targets(), job_deadline=datetime.now(timezone.utc)+timedelta(seconds=900), downstream_bound=550)
        result = executor.attach(ns['run'], normal, token='fixture-token', now=self.now)
        self.assertEqual(result['stats']['requests'], 1)
        self.assertTrue((data/'results_bsd.json').is_file())
        self.assertEqual(executor.outcome, 'trial_failed_normal_preserved')
        self.assertEqual(self.fresh_load()[1]['attempts'], {})

    def test_hanging_jwks_bounded_by_tail_watchdog_and_failure_cached(self):
        self.reserve()
        calls = []
        def hang(*args):
            calls.append(1); time.sleep(30)
        slots = self.make_slots(pinned={}, fetch=hang)
        executor = tail.PreparedTail(slots, '100:1', OfflineWire([WireResponse(200, self.body())]), 'fixture-token',
            self.targets(), job_deadline=datetime.now(timezone.utc)+timedelta(seconds=900), downstream_bound=550)
        executor.tracker.observe(self.headers, b'{}', self.now)
        started = time.monotonic()
        with patch.object(tail, 'TAIL_BUDGET_S', 1):
            executor()
        self.assertLess(time.monotonic()-started, 5)
        self.assertEqual(executor.outcome, 'trial_deadline_normal_preserved')
        self.assertEqual(len(self.fresh_load()[1]['attempts']), 1)
        down = []
        def provider_down(*args):
            down.append(1); raise TrialBlocked('oidc_keys_unavailable')
        slots = self.make_slots(pinned={}, fetch=provider_down)
        for _ in range(3):
            with self.assertRaisesRegex(TrialBlocked, 'keys_unavailable'):
                slots.load()
        self.assertEqual(len(down), 1)

    def test_hosted_remote_requires_attested_genesis(self):
        from scangoal.recovery_remote import GITHUB_REMOTE
        plain_store, plain_anchor = OfflineGitStore.bootstrap_fixture(self.root/'plain.git', dict(self.config, attestation=None))
        plain_store.remote = GITHUB_REMOTE
        with self.assertRaisesRegex(TrialBlocked, 'attestation_required'):
            InlineSlots(plain_store, plain_anchor, self.out).load()
        with self.assertRaisesRegex(TrialBlocked, 'attestation_unavailable'):
            InlineSlots(self.store, self.anchor, self.out, self.receipt, 4,
                        verifier=oidc.Verifier({'gh-1':jwk('gh-1')}, offline_only)).reserve('one', '100:1', DAY, 0, self.now)


class Response:
    def __init__(self, url, payload, status=200):
        self.url, self.payload, self.status = url, payload, status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def geturl(self):
        return self.url
    def read(self, n):
        return json.dumps(self.payload).encode()


class ProviderContractTests(unittest.TestCase):
    def test_runner_request_keeps_bearer_out_of_results_and_errors(self):
        with self.assertRaisesRegex(TrialBlocked, 'oidc_unavailable'):
            oidc.ActionsAttestor({})
        env = {'ACTIONS_ID_TOKEN_REQUEST_URL':'https://runner.example/token?api-version=2.0',
               'ACTIONS_ID_TOKEN_REQUEST_TOKEN':'fake-request-bearer'}
        attestor = oidc.ActionsAttestor(env)
        aud = oidc.AUD_PREFIX+'f'*64
        with patch.object(oidc, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda request, timeout: Response(request.full_url, {'value':'a.b.c'})
            self.assertEqual(attestor.token(aud), 'a.b.c')
            request = opener.return_value.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('&audience='+aud.replace(':', '%3A')))
        self.assertEqual(request.get_header('Authorization'), 'Bearer fake-request-bearer')
        with patch.object(oidc, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('fake-request-bearer leaked?')
            with self.assertRaises(TrialBlocked) as caught:
                attestor.token(aud)
        self.assertNotIn('fake-request-bearer', repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_jwks_only_from_official_issuer(self):
        def serve(config, keys):
            def open_(request, timeout):
                url = request.full_url
                return Response(url, config if url == oidc.DISCOVERY else {'keys':keys})
            return open_
        good = {'issuer':oidc.ISSUER, 'jwks_uri':oidc.ISSUER+'/.well-known/jwks'}
        with patch.object(oidc, 'build_opener') as opener:
            opener.return_value.open.side_effect = serve(good, [jwk('gh-1')])
            self.assertIn('gh-1', oidc.fetch_official_jwks())
        for config in ({'issuer':'https://attacker.example', 'jwks_uri':oidc.ISSUER+'/.well-known/jwks'},
                       {'issuer':oidc.ISSUER, 'jwks_uri':'https://attacker.example/jwks'},
                       {'issuer':oidc.ISSUER, 'jwks_uri':oidc.ISSUER+'.attacker.example/jwks'}):
            with self.subTest(config=config), patch.object(oidc, 'build_opener') as opener:
                opener.return_value.open.side_effect = serve(config, [jwk('evil')])
                with self.assertRaisesRegex(TrialBlocked, 'keys_unavailable'):
                    oidc.fetch_official_jwks()

    def test_small_or_wrong_type_keys_rejected(self):
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_numbers()
        for key in ({'kty':'EC','crv':'P-256','x':'AA','y':'AA'},
                    {'kty':'RSA','alg':'RS256','n':b64(weak.n.to_bytes(128, 'big')),'e':b64(b'\x01\x00\x01')},
                    dict(jwk('gh-1'), alg='RS512')):
            with self.subTest(key=str(key)[:30]), self.assertRaisesRegex(TrialBlocked, 'key_invalid'):
                oidc._public_key(key)


class PinnedKeyEvidenceTests(unittest.TestCase):
    def pin(self, name):
        entry = dict(jwk(name)); entry.pop('kid')
        return dict(entry, rfc7638_sha256=oidc.jwk_thumbprint(entry))

    def test_pins_require_matching_rfc7638_thumbprint(self):
        oidc.validate_pinned(oidc.PINNED_JWKS)          # module pins (empty now) must validate
        oidc.Verifier()                                  # default construction validates them
        good = {'gh-1':self.pin('gh-1')}
        self.assertIs(oidc.validate_pinned(good), good)
        swapped = {'gh-1':dict(self.pin('gh-1'), n=self.pin('evil')['n'])}   # key replaced, label kept
        missing = {'gh-1':{k:v for k, v in self.pin('gh-1').items() if k != 'rfc7638_sha256'}}
        for pins in (swapped, missing):
            with self.assertRaisesRegex(TrialBlocked, 'pinned_key_corrupt'):
                oidc.validate_pinned(pins)
        with patch.object(oidc, 'PINNED_JWKS', swapped), self.assertRaisesRegex(TrialBlocked, 'pinned_key_corrupt'):
            oidc.Verifier()

    def test_evidence_collector_offline_produces_verifiable_pins(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('evidence', baseline.ROOT/'scripts/oidc_jwks_evidence.py')
        evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)
        def server(config, keys):
            def get(url):
                payload = config if url == oidc.DISCOVERY else {'keys':keys}
                return 200, json.dumps(payload).encode(), {'leaf_sha256':'00'*32}
            return get
        good = {'issuer':oidc.ISSUER, 'jwks_uri':oidc.ISSUER+'/.well-known/jwks'}
        record = evidence.collect(server(good, [jwk('gh-1')]))
        self.assertEqual([k['kid'] for k in record['keys']], ['gh-1'])
        self.assertEqual(len(record['documents']), 2)
        aud = oidc.AUD_PREFIX+'2'*64
        verifier = oidc.Verifier(oidc.validate_pinned(record['pinned_jwks_proposal']), offline_only)
        self.assertEqual(verifier.verify(sign(claims(aud)), aud, datetime.now(timezone.utc), '100:1'), '100:1')
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_numbers()
        for config, keys in ((dict(good, issuer='https://attacker.example'), [jwk('gh-1')]),
                             (dict(good, jwks_uri='https://attacker.example/jwks'), [jwk('gh-1')]),
                             (good, [{'kty':'RSA','kid':'w','n':b64(weak.n.to_bytes(128, 'big')),'e':'AQAB'}]),
                             (good, [])):
            with self.subTest(config=config), self.assertRaises(TrialBlocked):
                evidence.collect(server(config, keys))
        with self.assertRaisesRegex(TrialBlocked, 'outside_issuer'):
            evidence.https_get('https://attacker.example/.well-known/jwks')


if __name__ == '__main__':
    unittest.main()

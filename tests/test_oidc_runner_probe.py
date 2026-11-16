"""Temporary OIDC diagnostic: never leaks tokens, never fails, verifies for real.

Offline: the runner token endpoint, discovery document and JWKS are served by a fake
opener; signatures use locally generated RSA keys (pyca/cryptography).
"""
import base64
import importlib.util
import io
import json
import re
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('oidc_runner_probe', ROOT/'scripts/oidc_runner_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

KEYS = {k: rsa.generate_private_key(public_exponent=65537, key_size=2048) for k in ('gh', 'evil')}
BEARER = 'fake-request-bearer-0123456789'
ENV = {'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://runner.example/idtoken?api-version=2.0',
       'ACTIONS_ID_TOKEN_REQUEST_TOKEN': BEARER, 'GITHUB_RUN_ID': '43954637606', 'GITHUB_RUN_ATTEMPT': '1'}
JWKS_URI = probe.ISSUER+'/.well-known/jwks'


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def jwk(name):
    n = KEYS[name].public_key().public_numbers()
    return {'kty':'RSA', 'alg':'RS256', 'use':'sig', 'kid':name, 'n':b64(n.n.to_bytes(256, 'big')), 'e':b64(n.e.to_bytes(3, 'big'))}


def claims(**changes):
    now = int(time.time())
    c = dict(iss=probe.ISSUER, aud=probe.AUDIENCE, sub=probe.SUBJECT, repository=probe.REPOSITORY,
             repository_owner='nunovinhas-creator', ref='refs/heads/main', ref_type='branch',
             workflow_ref=probe.WORKFLOW_REF, event_name='workflow_dispatch', run_id='43954637606',
             run_attempt='1', runner_environment='github-hosted', iat=now, nbf=now, exp=now+300)
    c.update(changes)
    return c


def token(c=None, key='gh', kid='gh', header=None):
    h = b64(json.dumps(header or {'alg':'RS256', 'typ':'JWT', 'kid':kid}).encode())
    b = b64(json.dumps(c or claims()).encode())
    return h+'.'+b+'.'+b64(KEYS[key].sign((h+'.'+b).encode(), padding.PKCS1v15(), hashes.SHA256()))


class Response:
    def __init__(self, url, payload):
        self.url, self.payload, self.status = url, payload, 200
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def geturl(self):
        return self.url
    def read(self, n):
        return json.dumps(self.payload).encode()


class Opener:
    def __init__(self, jwt, keys=('gh',), issuer=probe.ISSUER, fail=None):
        self.jwt, self.keys, self.issuer, self.fail, self.requests = jwt, keys, issuer, fail, []
    def open(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if self.fail and self.fail in url:
            raise OSError('connection refused for '+BEARER)       # message must never be printed
        if url.startswith(ENV['ACTIONS_ID_TOKEN_REQUEST_URL']):
            return Response(url, {'value':self.jwt})
        if url == probe.DISCOVERY:
            return Response(url, {'issuer':self.issuer, 'jwks_uri':JWKS_URI})
        if url == JWKS_URI:
            return Response(url, {'keys':[jwk(k) for k in self.keys]})
        raise AssertionError('unexpected host contacted: '+url)


class ProbeTests(unittest.TestCase):
    def run_probe(self, opener, env=ENV):
        report, secrets = probe.probe(env, opener)
        return report

    def test_genuine_token_verifies_and_reports_only_public_data(self):
        jwt = token()
        opener = Opener(jwt)
        report = self.run_probe(opener)
        self.assertEqual(report['result'], 'OIDC_OK')
        self.assertEqual(report['signing_kid'], 'gh')
        self.assertEqual(report['jwks'], [('gh', probe.thumbprint(jwk('gh')))])
        self.assertEqual(report['claims']['workflow_ref'], probe.WORKFLOW_REF)
        request = opener.requests[0]
        self.assertTrue(request.full_url.endswith('&audience='+probe.AUDIENCE))
        self.assertEqual(request.get_header('Authorization'), 'Bearer '+BEARER)
        self.assertEqual([r.full_url for r in opener.requests[1:]], [probe.DISCOVERY, JWKS_URI])  # only GitHub hosts
        serialized = json.dumps(report)
        self.assertNotIn(jwt, serialized)
        for part in jwt.split('.'):
            self.assertNotIn(part, serialized)
        self.assertNotIn(BEARER, serialized)
        self.assertNotIn('runner.example', serialized)                # request URL never reported

    def test_unavailable_when_permission_not_effective(self):
        report = self.run_probe(Opener(token()), {k: v for k, v in ENV.items() if not k.startswith('ACTIONS_ID_TOKEN')})
        self.assertEqual(report, {'probe':'oidc-diagnostic-v1', 'request_url_present':False,
                                  'request_token_present':False, 'result':'OIDC_UNAVAILABLE'})

    def test_forged_or_wrong_identity_never_reported_ok(self):
        cases = [
            ('OIDC_SIGNATURE_INVALID', dict(jwt=token(key='evil', kid='gh'))),
            ('OIDC_KEY_NOT_IN_JWKS', dict(jwt=token(key='evil', kid='evil'))),
            ('OIDC_HEADER_REJECTED', dict(jwt=token(header={'alg':'none', 'kid':'gh'}))),
            ('OIDC_HEADER_REJECTED', dict(jwt=token(header={'alg':'RS256', 'kid':'gh', 'jwk':jwk('evil')}))),
            ('OIDC_CLAIMS_MISMATCH', dict(jwt=token(claims(workflow_ref=probe.REPOSITORY+'/.github/workflows/tests.yml@refs/heads/main')))),
            ('OIDC_CLAIMS_MISMATCH', dict(jwt=token(claims(run_attempt='2')))),
            ('OIDC_CLAIMS_MISMATCH', dict(jwt=token(claims(aud='something-else')))),
            ('OIDC_CLAIMS_MISMATCH', dict(jwt=token(claims(environment='production')))),
            ('OIDC_ERROR', dict(jwt=token(), issuer='https://attacker.example')),
        ]
        for expected, kw in cases:
            with self.subTest(expected=expected):
                jwt = kw.pop('jwt')
                self.assertEqual(self.run_probe(Opener(jwt, **kw))['result'], expected)

    def test_never_fails_never_prints_secrets_on_errors(self):
        for fail in ('runner.example', 'openid-configuration', '/jwks'):
            out = io.StringIO()
            with self.subTest(fail=fail), patch.object(probe, 'build_opener', return_value=Opener(token(), fail=fail)), \
                    patch.dict(probe.os.environ, ENV, clear=True), redirect_stdout(out):
                probe.main()                                          # returns normally: exit code 0
            line = out.getvalue()
            self.assertTrue(line.startswith('OIDC_PROBE '))
            self.assertEqual(json.loads(line[len('OIDC_PROBE '):])['result'], 'OIDC_ERROR')
            self.assertNotIn(BEARER, line)
            self.assertNotIn('connection refused', line)

    def test_printed_line_is_redacted_if_a_secret_would_appear(self):
        jwt = token(claims(repository=BEARER))                        # pathological echo of the bearer
        out = io.StringIO()
        with patch.object(probe, 'build_opener', return_value=Opener(jwt)), \
                patch.dict(probe.os.environ, ENV, clear=True), redirect_stdout(out):
            probe.main()
        self.assertEqual(out.getvalue().strip(), 'OIDC_PROBE {"result": "OIDC_REDACTED_OUTPUT"}')

    def test_workflow_step_is_last_isolated_and_temporary(self):
        text = (ROOT/'.github/workflows/collect.yml').read_text(encoding='utf-8')
        step = text.split('      - name: OIDC diagnostic (temporary, read-only)\n', 1)[1]
        self.assertTrue(text.rstrip().endswith('run: python -u scripts/oidc_runner_probe.py'))
        self.assertIn('continue-on-error: true', step)
        self.assertIn('timeout-minutes: 1', step)
        self.assertNotIn('secrets.', step)                            # no secret is handed to the probe
        self.assertLess(text.index('gh workflow run collect.yml'), text.index('oidc_runner_probe'))
        source = (ROOT/'scripts/oidc_runner_probe.py').read_text(encoding='utf-8').split('"""', 2)[2]   # code, not docstring
        for forbidden in ('bzzoiro', 'highlightly', 'BSD_API_KEY', 'subprocess', 'write_text', 'bsd-trial-journal'):
            self.assertNotIn(forbidden, source.lower() if forbidden.islower() else source)
        self.assertIsNone(re.search(r'(?<![\w.])open\(', source))   # no file is opened or written


if __name__ == '__main__':
    unittest.main()

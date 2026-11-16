"""TEMPORARY OIDC diagnostic for the collector job. Remove after one observed run.

Answers one question: can THIS job (collect.yml on main) obtain a GitHub Actions
OIDC token whose signature and identity verify? It requests one token with a fixed,
harmless diagnostic audience that no service accepts, verifies the RS256 signature
against the official JWKS (TLS, no redirects) and checks the identity claims.

Never prints or stores the JWT, the request bearer or any credential. No BSD or
Highlightly request, no journal, no Git or file write. Always exits 0, so it can
never fail the collector. Prints one line: OIDC_PROBE {json}.
"""
import base64
import hashlib
import json
import os
import sys
import time
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = 'nunovinhas-creator/goal-roi'
ISSUER = 'https://token.actions.githubusercontent.com'
DISCOVERY = ISSUER+'/.well-known/openid-configuration'
WORKFLOW_REF = REPOSITORY+'/.github/workflows/collect.yml@refs/heads/main'
SUBJECT = 'repo:'+REPOSITORY+':ref:refs/heads/main'
AUDIENCE = 'goal-roi-oidc-diagnostic-v1'
TIMEOUT_S, MAX_BODY = 10, 256_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def b64d(part):
    return base64.urlsafe_b64decode(part+'='*(-len(part) % 4))


def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def get_json(opener, url, headers=None):
    request = Request(url, headers=dict(headers or {}, Accept='application/json'))
    with opener.open(request, timeout=TIMEOUT_S) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError('unexpected response')
        raw = response.read(MAX_BODY+1)
    if len(raw) > MAX_BODY:
        raise ValueError('response too large')
    return json.loads(raw)


def thumbprint(jwk):
    """RFC 7638 SHA-256 thumbprint of an RSA JWK (public data, safe to print)."""
    canonical = json.dumps({'e':jwk['e'], 'kty':'RSA', 'n':jwk['n']}, sort_keys=True, separators=(',', ':'))
    return b64e(hashlib.sha256(canonical.encode()).digest())


def check_claims(claims, environ):
    expected = {'iss':ISSUER, 'aud':AUDIENCE, 'sub':SUBJECT, 'repository':REPOSITORY,
                'ref':'refs/heads/main', 'ref_type':'branch', 'workflow_ref':WORKFLOW_REF,
                'runner_environment':'github-hosted',
                'run_id':environ.get('GITHUB_RUN_ID'), 'run_attempt':environ.get('GITHUB_RUN_ATTEMPT')}
    failed = [k for k, v in expected.items() if claims.get(k) != v]
    if claims.get('event_name') not in ('schedule', 'workflow_dispatch'):
        failed.append('event_name')
    if 'environment' in claims or claims.get('job_workflow_ref', WORKFLOW_REF) != WORKFLOW_REF:
        failed.append('unexpected_identity_claim')
    if not all(type(claims.get(k)) is int for k in ('iat', 'nbf', 'exp')) or not claims['iat'] <= claims['exp']:
        failed.append('times')
    return failed


def probe(environ=None, opener=None):
    environ = os.environ if environ is None else environ
    opener = opener or build_opener(NoRedirect())
    url, bearer = environ.get('ACTIONS_ID_TOKEN_REQUEST_URL'), environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    report = {'probe':'oidc-diagnostic-v1', 'request_url_present':bool(url), 'request_token_present':bool(bearer)}
    if not url or not bearer:
        report['result'] = 'OIDC_UNAVAILABLE'      # id-token: write not effective for this job
        return report, ()
    secrets = (bearer,)
    stage = 'token_request'
    try:
        if not url.startswith('https://'):
            raise ValueError('request url not https')
        token = get_json(opener, url+('&' if '?' in url else '?')+'audience='+quote(AUDIENCE, safe=''),
                         {'Authorization':'Bearer '+bearer})['value']
        secrets = (bearer, token)
        stage = 'decode'
        head_b64, body_b64, sig_b64 = token.split('.')
        header, claims = json.loads(b64d(head_b64)), json.loads(b64d(body_b64))
        if header.get('alg') != 'RS256' or set(header) - {'alg', 'typ', 'kid', 'x5t'}:
            report['result'] = 'OIDC_HEADER_REJECTED'
            return report, secrets
        stage = 'jwks'
        config = get_json(opener, DISCOVERY)
        if config.get('issuer') != ISSUER or not str(config.get('jwks_uri', '')).startswith(ISSUER+'/'):
            raise ValueError('discovery mismatch')
        keys = {k['kid']:k for k in get_json(opener, config['jwks_uri'])['keys'] if k.get('kty') == 'RSA'}
        report['jwks'] = sorted((kid, thumbprint(k)) for kid, k in keys.items())
        report['signing_kid'] = header.get('kid')
        jwk = keys.get(header.get('kid'))
        if jwk is None:
            report['result'] = 'OIDC_KEY_NOT_IN_JWKS'
            return report, secrets
        stage = 'signature'
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        key = RSAPublicNumbers(int.from_bytes(b64d(jwk['e']), 'big'), int.from_bytes(b64d(jwk['n']), 'big')).public_key()
        try:
            key.verify(b64d(sig_b64), (head_b64+'.'+body_b64).encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            report['result'] = 'OIDC_SIGNATURE_INVALID'
            return report, secrets
        stage = 'claims'
        report['key_bits'] = key.key_size
        report['claims'] = {k:claims.get(k) for k in ('iss', 'repository', 'ref', 'workflow_ref', 'event_name',
                                                     'runner_environment', 'run_id', 'run_attempt')}
        report['lifetime_s'] = claims.get('exp', 0)-claims.get('iat', 0)
        report['clock_skew_s'] = int(time.time())-claims.get('iat', 0)
        failed = check_claims(claims, environ)
        report['result'] = 'OIDC_OK' if not failed else 'OIDC_CLAIMS_MISMATCH'
        if failed:
            report['failed_checks'] = failed
    except Exception as exc:
        report['result'] = 'OIDC_ERROR'
        report['stage'], report['error'] = stage, type(exc).__name__   # never the message
    return report, secrets


def main():
    try:
        report, secrets = probe()
        line = 'OIDC_PROBE '+json.dumps(report, sort_keys=True)
        if any(s and s in line for s in secrets):          # defence in depth: never print a secret
            line = 'OIDC_PROBE {"result": "OIDC_REDACTED_OUTPUT"}'
    except Exception as exc:
        line = 'OIDC_PROBE '+json.dumps({'result':'OIDC_ERROR', 'stage':'main', 'error':type(exc).__name__})
    print(line)
    sys.stdout.flush()


if __name__ == '__main__':
    main()

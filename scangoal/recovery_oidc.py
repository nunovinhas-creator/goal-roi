"""GitHub Actions OIDC attestation of BSD trial journal events.

A token proves WHICH run of WHICH workflow asked for it (identity of the executor)
and binds that identity to one exact event at one exact journal position. It is NOT
proof that response bytes came from the BSD server: that still rests on the audited
TLS transport and the preserved evidence.

Trusted keys come only from (a) PINNED_JWKS, set by a reviewed commit from the
official JWKS, or (b) the official discovery document + JWKS fetched over TLS at
verification time. A key, URL or header carried inside the journal is never trusted.
Signature verification uses pyca/cryptography (no hand-written RSA).
"""
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, build_opener
from .recovery_remote import NoRedirect, REPOSITORY
from .recovery_trial import TRIAL_ID, TrialBlocked
from .recovery_trial_journal import canonical

UTC = timezone.utc
MODE = 'github-oidc-v1'
ISSUER = 'https://token.actions.githubusercontent.com'
DISCOVERY = ISSUER+'/.well-known/openid-configuration'
OWNER = REPOSITORY.split('/')[0]
# Plain (non-reusable) workflow: `workflow_ref` is the contract. `job_workflow_ref`
# only exists for reusable workflows and must never point elsewhere.
WORKFLOW_REF = REPOSITORY+'/.github/workflows/collect.yml@refs/heads/main'
SUBJECT = 'repo:'+REPOSITORY+':ref:refs/heads/main'   # job without `environment:`
TRIGGERS = ('schedule', 'workflow_dispatch')           # never pull_request*
AUD_PREFIX = 'goal-roi-bsd-journal-v1:'
# kid -> RSA JWK. Empty until a reviewed commit pins keys read from the official
# JWKS (see AUTHORIZATION.md). Needed for replay after GitHub retires a key.
PINNED_JWKS = {}
MAX_LIFETIME_S = 3600      # GitHub issues ~5 min tokens; longer is rejected
SKEW_S = 60
EVENT_WINDOW_S = 300       # event stamped at most 5 min before token issuance
HTTP_TIMEOUT_S = 10
MAX_BODY = 256_000
HEADER_FIELDS = {'alg', 'typ', 'kid', 'x5t'}   # no jwk/jku/x5u/x5c/crit: no key injection
B64URL = re.compile('[A-Za-z0-9_-]+')
STRING_CLAIMS = ('iss','aud','sub','repository','repository_owner','ref','ref_type',
                 'workflow_ref','event_name','run_id','run_attempt','runner_environment')


def audience(parent, event):
    """Bind identity to trial, journal position (parent commit) and exact event."""
    if not isinstance(parent, str) or not re.fullmatch('[0-9a-f]{40}', parent):
        raise TrialBlocked('attestation_parent_invalid')
    body = {k:v for k,v in event.items() if k != 'attestation'}
    return AUD_PREFIX+hashlib.sha256(canonical({'trial_id':TRIAL_ID,'parent':parent,'event':body})).hexdigest()


def _b64(part):
    if not isinstance(part, str) or not B64URL.fullmatch(part):
        raise TrialBlocked('oidc_token_malformed')
    try:
        return base64.urlsafe_b64decode(part+'='*(-len(part) % 4))
    except ValueError:
        raise TrialBlocked('oidc_token_malformed') from None


def _remaining(not_after):
    timeout = HTTP_TIMEOUT_S
    if not_after is not None:
        timeout = min(timeout, (not_after-datetime.now(UTC)).total_seconds())
        if timeout < 1:
            raise TrialBlocked('tail_time_budget_exhausted')
    return timeout


def _get_json(url, not_after, headers=None):
    request = Request(url, headers=dict(headers or {}, Accept='application/json'))
    with build_opener(NoRedirect()).open(request, timeout=_remaining(not_after)) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError()
        raw = response.read(MAX_BODY+1)
    if len(raw) > MAX_BODY:
        raise ValueError()
    return json.loads(raw)


def fetch_official_jwks(not_after=None):
    """Official discovery + JWKS over TLS, no redirects. Any doubt => unavailable."""
    try:
        config = _get_json(DISCOVERY, not_after)
        uri = config['jwks_uri']
        if config['issuer'] != ISSUER or not isinstance(uri, str) or not uri.startswith(ISSUER+'/'):
            raise ValueError()
        keys = _get_json(uri, not_after)['keys']
        return {k['kid']:k for k in keys if isinstance(k, dict) and isinstance(k.get('kid'), str)}
    except Exception:
        raise TrialBlocked('oidc_keys_unavailable') from None


def _public_key(jwk):
    try:
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    except ImportError:
        raise TrialBlocked('oidc_crypto_unavailable') from None
    if (not isinstance(jwk, dict) or jwk.get('kty') != 'RSA' or jwk.get('alg', 'RS256') != 'RS256'
            or jwk.get('use', 'sig') != 'sig'):
        raise TrialBlocked('oidc_key_invalid')
    n, e = (int.from_bytes(_b64(jwk.get(x)), 'big') for x in ('n', 'e'))
    if n.bit_length() < 2048 or e < 3 or e % 2 == 0:
        raise TrialBlocked('oidc_key_invalid')
    return RSAPublicNumbers(e, n).public_key()


class Verifier:
    """Verify one token for one event. Only RS256 with a trusted key is accepted."""
    def __init__(self, pinned=None, fetch=fetch_official_jwks):
        self.pinned = dict(PINNED_JWKS if pinned is None else pinned)
        self._fetch, self._live, self._down = fetch, None, False
        self.not_after = None

    def key(self, kid):
        jwk = self.pinned.get(kid)
        if jwk is None:
            if self._down:
                # One bounded attempt per process; never wait on the provider again.
                raise TrialBlocked('oidc_keys_unavailable')
            if self._live is None:
                try:
                    self._live = self._fetch(self.not_after)   # once per process
                except TrialBlocked:
                    self._down = True
                    raise
            jwk = self._live.get(kid)
        if jwk is None:
            # Unknown or retired key: UNVERIFIABLE, not proven fraud. Trial pauses.
            raise TrialBlocked('oidc_key_unknown_unverifiable')
        return _public_key(jwk)

    def verify(self, token, aud, event_time, owner=None, now=None):
        """Historical check when now is None: signature, identity, binding and the
        token's own time coherence. Freshness (exp vs clock) only when now is given."""
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise TrialBlocked('oidc_crypto_unavailable') from None
        if not isinstance(token, str) or token.count('.') != 2 or len(token) > 16_000:
            raise TrialBlocked('oidc_token_malformed')
        head_b64, body_b64, sig_b64 = token.split('.')
        try:
            header, claims = json.loads(_b64(head_b64)), json.loads(_b64(body_b64))
        except ValueError:
            raise TrialBlocked('oidc_token_malformed') from None
        if (not isinstance(header, dict) or set(header) - HEADER_FIELDS or header.get('alg') != 'RS256'
                or header.get('typ', 'JWT') != 'JWT' or not isinstance(header.get('kid'), str)):
            raise TrialBlocked('oidc_header_rejected')
        try:
            self.key(header['kid']).verify(_b64(sig_b64), (head_b64+'.'+body_b64).encode(),
                                           padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise TrialBlocked('oidc_signature_invalid') from None
        # Only now are the claims authenticated.
        if not isinstance(claims, dict) or any(type(claims.get(k)) is not str for k in STRING_CLAIMS):
            raise TrialBlocked('oidc_claims_incomplete')
        if any(type(claims.get(k)) is not int for k in ('iat', 'nbf', 'exp')):
            raise TrialBlocked('oidc_claims_incomplete')
        if (claims['iss'] != ISSUER or claims['aud'] != aud or claims['sub'] != SUBJECT
                or claims['repository'] != REPOSITORY or claims['repository_owner'] != OWNER
                or claims['ref'] != 'refs/heads/main' or claims['ref_type'] != 'branch'
                or claims['workflow_ref'] != WORKFLOW_REF
                or claims.get('job_workflow_ref', WORKFLOW_REF) != WORKFLOW_REF
                or 'environment' in claims or claims['event_name'] not in TRIGGERS
                or claims['runner_environment'] != 'github-hosted'
                or not claims['run_id'].isdigit() or not claims['run_attempt'].isdigit()):
            raise TrialBlocked('oidc_identity_rejected')
        identity = claims['run_id']+':'+claims['run_attempt']
        if owner is not None and identity != owner:
            raise TrialBlocked('oidc_run_mismatch')
        iat, nbf, exp = claims['iat'], claims['nbf'], claims['exp']
        start = min(iat, nbf)
        stamp = event_time.timestamp()
        if (nbf > iat+SKEW_S or iat > exp or exp-start > MAX_LIFETIME_S
                or not iat-EVENT_WINDOW_S <= stamp <= iat+SKEW_S):
            raise TrialBlocked('oidc_time_rejected')
        if now is not None and not nbf-SKEW_S <= now.timestamp() <= exp+SKEW_S:
            raise TrialBlocked('oidc_token_not_fresh')
        return identity


class ActionsAttestor:
    """Request a token from the running job. Needs `id-token: write` on that job.

    The request bearer lives only in memory, is sent only to the runner-provided
    HTTPS URL, and is never logged, persisted or put in an exception.
    """
    def __init__(self, environ=None):
        env = os.environ if environ is None else environ
        url, bearer = env.get('ACTIONS_ID_TOKEN_REQUEST_URL'), env.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
        if not isinstance(url, str) or not url.startswith('https://') or not bearer:
            raise TrialBlocked('oidc_unavailable')
        self._url, self._bearer = url, bearer
        self.not_after = None

    def token(self, aud):
        url = self._url+('&' if '?' in self._url else '?')+'audience='+quote(aud, safe='')
        try:
            value = _get_json(url, self.not_after, {'Authorization':'Bearer '+self._bearer})['value']
            if not isinstance(value, str) or self._bearer in value:
                raise ValueError()
            return value
        except TrialBlocked:
            raise
        except Exception:
            raise TrialBlocked('oidc_request_failed') from None

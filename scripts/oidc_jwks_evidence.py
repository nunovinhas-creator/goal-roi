"""Collect verifiable evidence of GitHub Actions OIDC signing keys (run by the owner).

Read-only: fetches the official discovery document and JWKS over TLS (system CA
store, no redirects), validates issuer/jwks_uri and every key with the same code the
verifier uses, and records time, URLs, response hashes, the TLS certificate seen and
per-key fingerprints. Prints a PINNED_JWKS proposal for a REVIEWED commit; it never
edits code. Run it from two independent networks and compare before pinning.

    python scripts/oidc_jwks_evidence.py --out /path/outside/repo/oidc-evidence.json
"""
import argparse
import hashlib
import http.client
import json
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scangoal import recovery_oidc as oidc  # noqa: E402
from scangoal.recovery_trial import TrialBlocked  # noqa: E402


def https_get(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != urlsplit(oidc.ISSUER).hostname:
        raise TrialBlocked('evidence_url_outside_issuer')
    conn = http.client.HTTPSConnection(parts.hostname, 443, timeout=15, context=ssl.create_default_context())
    try:
        conn.request('GET', parts.path or '/', headers={'Accept':'application/json'})
        response = conn.getresponse()
        body = response.read(oidc.MAX_BODY+1)
        cert = conn.sock.getpeercert()
        der = conn.sock.getpeercert(binary_form=True)
        peer = {'leaf_sha256':hashlib.sha256(der).hexdigest(),
                'subject':cert.get('subject'), 'issuer':cert.get('issuer'), 'notAfter':cert.get('notAfter')}
        return response.status, body, peer
    finally:
        conn.close()


def collect(get=https_get, now=None):
    now = now or datetime.now(timezone.utc)
    record = {'collected_utc':now.isoformat(timespec='seconds'), 'documents':[]}
    def fetch(url):
        status, body, peer = get(url)
        if status != 200 or len(body) > oidc.MAX_BODY:
            raise TrialBlocked('evidence_fetch_failed')
        record['documents'].append({'url':url, 'status':status, 'sha256':hashlib.sha256(body).hexdigest(), 'tls':peer})
        return json.loads(body)
    config = fetch(oidc.DISCOVERY)
    uri = config.get('jwks_uri')
    if config.get('issuer') != oidc.ISSUER or not isinstance(uri, str) or not uri.startswith(oidc.ISSUER+'/'):
        raise TrialBlocked('evidence_discovery_mismatch')
    record['issuer'], record['jwks_uri'] = config['issuer'], uri
    record['signing_algs'] = config.get('id_token_signing_alg_values_supported')
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pins = {}
    record['keys'] = []
    for jwk in fetch(uri).get('keys', []):
        kid = jwk.get('kid')
        key = oidc._public_key(jwk)            # same acceptance rules as the verifier
        spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        entry = {k:jwk[k] for k in ('kty', 'n', 'e')}
        entry.update(alg='RS256', use='sig', rfc7638_sha256=oidc.jwk_thumbprint(jwk))
        pins[kid] = entry
        record['keys'].append({'kid':kid, 'bits':key.key_size, 'rfc7638_sha256':entry['rfc7638_sha256'],
                               'spki_sha256':hashlib.sha256(spki).hexdigest(), 'x5t':jwk.get('x5t')})
    if not pins:
        raise TrialBlocked('evidence_no_keys')
    oidc.validate_pinned(pins)
    record['pinned_jwks_proposal'] = pins
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    record = collect()
    Path(args.out).write_text(json.dumps(record, indent=1, sort_keys=True)+'\n')
    for key in record['keys']:
        print(key['kid'], key['bits'], key['rfc7638_sha256'], key['spki_sha256'])
    print('Evidence written to', args.out, '- review, compare with a second network, then pin by reviewed commit.')


if __name__ == '__main__':
    main()

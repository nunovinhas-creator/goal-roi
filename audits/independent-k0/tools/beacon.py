"""Public-randomness round schedule and seed derivation for the K0 protocol.

No network access happens unless --fetch is given. The seeds of the blind banks
come only from the randomness of a drand round fixed in protocol.json, or from
the first pre-registered fallback round. Nobody chooses a seed.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import urllib.request

PROTOCOL = Path(__file__).resolve().parents[1] / 'protocol.json'


def load(path=PROTOCOL):
    return json.loads(Path(path).read_bytes())


def round_time(beacon, number):
    return datetime.fromtimestamp(beacon['genesis_time'] + (number - 1) * beacon['period_seconds'], timezone.utc)


def round_at(beacon, when):
    return (int(when.timestamp()) - beacon['genesis_time']) // beacon['period_seconds'] + 1


def candidates(beacon):
    """Primary round followed by the fixed fallback sequence. Order is the only rule."""
    for k in range(beacon['max_fallbacks'] + 1):
        yield k, beacon['primary_round'] + k * beacon['fallback_step_rounds']


def eligible_round(beacon, last_commitment_utc, *, unavailable=()):
    """First round in the sequence scheduled at least `margin` after the last commitment and not declared
    unavailable under the pre-registered availability rule."""
    margin = beacon['minimum_margin_seconds']
    for k, number in candidates(beacon):
        if (round_time(beacon, number) - last_commitment_utc).total_seconds() < margin:
            continue
        if number in unavailable:
            continue
        return k, number
    raise RuntimeError('no_registered_round_left_protocol_amendment_required')


def check_randomness(entry):
    """For unchained BLS beacons the randomness is SHA-256 of the signature."""
    signature = bytes.fromhex(entry['signature'])
    return hashlib.sha256(signature).hexdigest() == entry['randomness']


def derive_seeds(randomness_hex, protocol_sha256, matrix):
    key = bytes.fromhex(randomness_hex)
    seeds = {}
    for family in matrix['families']:
        for role, count in (('mining', matrix['mining_seeds']), ('validation', matrix['validation_seeds'])):
            for i in range(1, count + 1):
                label = f'goal-roi-k0/v1/{protocol_sha256}/{family["slot"]}/{role}/{i}'
                digest = hmac.new(key, label.encode(), hashlib.sha256).digest()
                seeds[f'{family["slot"]}/{role}/{i}'] = int.from_bytes(digest[:8], 'big') % (2 ** 63)
    return seeds


def fetch(beacon, number, timeout=20):
    """Return one entry per relay. Agreement of at least `relay_quorum` relays is required."""
    out = {}
    for relay in beacon['relays']:
        url = f'{relay}/{beacon["chain_hash"]}/public/{number}'
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                out[relay] = json.loads(response.read())
        except Exception as error:  # recorded, never retried with another round by hand
            out[relay] = dict(error=type(error).__name__)
    return out


def agreed(beacon, entries, number):
    good = [e for e in entries.values() if 'randomness' in e and e.get('round') == number and check_randomness(e)]
    values = {(e['randomness'], e['signature']) for e in good}
    if len(values) == 1 and len(good) >= beacon['relay_quorum']:
        return good[0]
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schedule', action='store_true', help='print primary and fallback rounds with UTC times')
    parser.add_argument('--fetch', type=int, help='fetch a round from all relays (network)')
    args = parser.parse_args()
    beacon = load()['beacon']
    if args.schedule:
        print(json.dumps([dict(k=k, round=n, utc=round_time(beacon, n).isoformat()) for k, n in candidates(beacon)],
                         indent=2))
    if args.fetch:
        print(json.dumps(fetch(beacon, args.fetch), indent=2, sort_keys=True))

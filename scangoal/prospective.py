"""Append-only prospective ledger (B4): future eligible signals, independent outcomes, frozen hypotheses.

    python -m scangoal.prospective record    # activate once, register new signals and outcomes, rebuild the summary
    python -m scangoal.prospective verify    # check the hash chain and duplicates; exit 3 if broken

The protocol is scangoal/prospective_protocol.json. The ledger is data/prospective_ledger.jsonl; the derived
tables are data/prospective_report.json. Nothing here sends alerts, places bets or reads new sources.
"""

import collections
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from .store import data_dir

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / 'prospective_protocol.json'
CODE = ('prospective.py', 'prospective_protocol.json', 'eligibility.py', 'results_bsd.py', 'opportunity_results.py')
CODE_V2 = CODE + ('report.py', 'results_hl.py', 'details_hl.py', 'match_identity.py', 'chronology.py', 'parser.py', 'store.py')
CODE_MANIFEST_SCHEMA = 2
LEDGER_NAME, REPORT_NAME = 'prospective_ledger.jsonl', 'prospective_report.json'
GOALS_PER_MIN = 2.7 / 95
MISSING_AFTER = timedelta(hours=72)
MIN_GAMES, Z_CRIT, MIN_BAND = 200, 2.576, 30
UTC = timezone.utc
FAILED_EXIT = 3


class LedgerError(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _utc(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def code_hashes():
    return {name: _sha((HERE / name).read_bytes()) for name in CODE_V2}


def code_manifest():
    aliases = data_dir() / 'team_aliases.json'
    try:
        reference_sha = _sha(aliases.read_bytes())
    except FileNotFoundError:
        reference_sha = None
    return dict(code_manifest_schema=CODE_MANIFEST_SCHEMA, code_sha256=code_hashes(),
                reference_sha256={'team_aliases.json': reference_sha})


def ledger_path():
    return data_dir() / LEDGER_NAME


def load(path=None):
    path = path or ledger_path()
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def verify(entries):
    """Hash chain, sequence, duplicate keys. Raises LedgerError on any defect."""
    prev, keys, outcomes = None, set(), set()
    for i, entry in enumerate(entries):
        body = {k: v for k, v in entry.items() if k != 'sha256'}
        if entry.get('seq') != i or entry.get('prev_sha256') != prev or _sha(_canonical(body).encode()) != entry.get('sha256'):
            raise LedgerError(f'chain_broken_at_{i}')
        if i == 0 and entry.get('kind') != 'activation':
            raise LedgerError('first_entry_is_not_activation')
        if entry['kind'] == 'signal':
            if entry['key'] in keys:
                raise LedgerError(f'duplicate_signal_{i}')
            keys.add(entry['key'])
        if entry['kind'] == 'outcome':
            pair = (entry['match_id'], entry['provider'])
            if pair in outcomes:
                raise LedgerError(f'duplicate_outcome_{i}')
            outcomes.add(pair)
        prev = entry['sha256']
    return True


class Writer:
    def __init__(self, path, entries):
        self.path, self.entries = path, entries

    def append(self, kind, **fields):
        body = dict(fields, kind=kind, seq=len(self.entries),
                    prev_sha256=self.entries[-1]['sha256'] if self.entries else None)
        entry = dict(body, sha256=_sha(_canonical(body).encode()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(_canonical(entry) + '\n')
        self.entries.append(entry)
        return entry


def registered_matches(path=None):
    """Fixtures that need an independent result (used by the provider queries). Never raises."""
    try:
        return {e['match_id'] for e in load(path) if e.get('kind') == 'signal' and e.get('match_id')}
    except (OSError, ValueError, KeyError):
        return set()


def signal_key(b):
    return _sha(_canonical([b.get('match_id'), b.get('type'), b.get('market'), b.get('selection')]).encode())


def _direction(selection):
    found = re.match(r'\s*(over|under)\s', selection or '', re.I)
    return found[1].lower() if found else None


def _h1(b):
    return (b['phase'] == 'live' and type(b.get('minute')) is int and 0 <= b['minute'] <= 89
            and isinstance(b.get('score_at_signal'), (list, tuple)) and len(b['score_at_signal']) == 2)


def _h2(b):
    return b['type'] == 'OPEN_GAME' and b['phase'] == 'live' and b.get('odds') and 1.50 <= b['odds'] < 2.50


def _signals(now):
    from . import report
    inputs = report.load_bet_inputs(now)
    return report.build_bets(inputs.identified, strict=False, **inputs.bet_options)


def _providers(now):
    from .opportunity_results import provider_records
    from .results_bsd import load_result_state
    out = {}
    for name, label in (('hl', 'Highlightly'), ('bsd', 'BSD')):
        try:
            state = load_result_state(data_dir() / f'results_{name}.json')
        except (OSError, ValueError):
            continue
        out[label] = provider_records(state, name, now)
    return out


def _bound_versions(target, providers, now):
    """Return every independently bound regulation version, ordered by availability."""
    from .opportunity_results import ft_time_valid, score_pair
    from .report import external_matches

    for provider, fixtures in sorted(providers.items()):
        if provider not in ('BSD', 'Highlightly'):
            continue
        rows = fixtures.get(target['match_id'], ())
        if isinstance(rows, dict):
            rows = (rows,)
        valid = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = score_pair(row.get('ft'))
            observed = _utc(row.get('observed_at', row.get('matched_at_utc')))
            available = _utc(row.get('available_at', observed))
            if (score is not None and row.get('period', 'regulation') == 'regulation'
                    and type(row.get('bsd_id')) is int and row['bsd_id'] > 0
                    and ft_time_valid(_utc(row.get('event_date')), observed, available, None, now)
                    and external_matches(target, row, independent=True)):
                valid.append((available, observed, _sha(_canonical(row).encode()), score, row))
        if valid:
            yield provider, sorted(valid, key=lambda version: version[:3])


def record(now=None, *, signals=None, providers=None):
    now = now or datetime.now(UTC)
    path = ledger_path()
    entries = load(path)
    verify(entries)
    writer = Writer(path, entries)
    protocol_sha = _sha(PROTOCOL.read_bytes())
    if entries and entries[0]['protocol_sha256'] != protocol_sha:
        raise LedgerError('protocol_changed_since_activation')
    if entries:
        last_at = _utc(entries[-1].get('at_utc'))
        if last_at is None or now < last_at:
            raise LedgerError('invalid_cutoff_for_ledger_head')
    manifest = code_manifest()
    if not entries:
        writer.append('activation', at_utc=now.isoformat(), protocol_sha256=protocol_sha, **manifest,
                      note='prospective registration starts here; earlier data never enters this ledger')
    activation = entries[0]
    last_code = next((e for e in reversed(entries) if 'code_sha256' in e), {})
    if any(last_code.get(key) != value for key, value in manifest.items()):
        writer.append('code_version', at_utc=now.isoformat(), **manifest)
    start = _utc(activation['at_utc'])
    signals = _signals(now) if signals is None else signals
    providers = _providers(now) if providers is None else providers
    provider_known = {e['match_id'] for e in entries if e['kind'] in ('outcome', 'outcome_conflict')}
    from .eligibility import eligible
    registered = {e['key'] for e in entries if e['kind'] == 'signal'}
    totals = collections.defaultdict(list)
    for e in entries:
        if e['kind'] == 'signal' and e.get('direction'):
            totals[e['match_id']].append(e)
    added = 0
    for b in sorted(signals, key=lambda x: (x.get('signal_seen_utc') or '', x.get('message_id') or 0)):
        seen = _utc(b.get('signal_seen_utc'))
        if seen is None or not start <= seen <= now or not eligible(b):
            continue
        key = signal_key(b)
        if key in registered:
            continue
        known = (bool(b.get('ft')) or bool(b.get('match_result_first_seen_utc')) or b['match_id'] in provider_known
                 or any(_bound_versions(b, providers, now)))
        entry = writer.append(
            'signal', at_utc=now.isoformat(), key=key, match_id=b['match_id'],
            canonical_match_id=b.get('canonical_match_id'), message_id=b.get('message_id'), type=b['type'],
            group=b.get('group'), phase=b['phase'], market=b.get('market'), selection=b.get('selection'),
            odds=b.get('odds'), minute=b.get('minute'), score_at_signal=b.get('score_at_signal'),
            teams=b.get('teams'), league=b.get('league'), kickoff_utc=b.get('kickoff_utc'), date_utc=b.get('date_utc'),
            signal_seen_utc=b.get('signal_seen_utc'), result_known_at_registration=known,
            direction=_direction(b.get('selection')), h1=_h1(b), h2=bool(_h2(b)))
        registered.add(key)
        added += 1
        if entry['direction']:
            for earlier in totals[entry['match_id']]:
                if earlier['type'] != entry['type'] and earlier['direction'] != entry['direction']:
                    writer.append('diagnostic', at_utc=now.isoformat(), name='D1_opposite_totals',
                                  match_id=entry['match_id'], keys=[earlier['key'], key])
                    break
            totals[entry['match_id']].append(entry)
    outcomes(writer, now, providers)
    return added


def outcomes(writer, now, providers=None):
    providers = _providers(now) if providers is None else providers
    entries = writer.entries
    games = {}
    for e in entries:
        if e['kind'] == 'signal':
            games.setdefault(e['match_id'], e)
    first, conflicts = collections.defaultdict(dict), collections.defaultdict(set)
    prior_outcomes = collections.defaultdict(list)
    for entry in entries:
        if entry['kind'] == 'outcome':
            first[entry['match_id']][entry['provider']] = tuple(entry['ft'])
            prior_outcomes[entry['match_id']].append(entry)
        elif entry['kind'] == 'outcome_conflict':
            conflicts[entry['match_id']].add((entry['provider'], tuple(entry['ft'])))
    missing = {e['match_id'] for e in entries if e['kind'] == 'outcome_missing'}
    for mid, sig in sorted(games.items()):
        versions = list(_bound_versions(sig, providers, now))
        scores = set(first[mid].values())
        # Legacy runs could record two provider outcomes without an explicit conflict entry.
        if len(scores) > 1:
            for previous_outcome in prior_outcomes[mid]:
                key = (previous_outcome['provider'], tuple(previous_outcome['ft']))
                if key not in conflicts[mid]:
                    writer.append('outcome_conflict', at_utc=now.isoformat(), match_id=mid,
                                  provider=key[0], ft=list(key[1]), first_ft=None,
                                  prior_outcome_sha256=previous_outcome['sha256'])
                    conflicts[mid].add(key)
        scores.update(score for _, history in versions for _, _, _, score, _ in history)
        conflicted = bool(conflicts[mid]) or len(scores) > 1
        for provider, history in versions:
            previous = first[mid].get(provider)
            for available, observed, digest, score, row in history:
                if score == previous or (provider, score) in conflicts[mid]:
                    continue
                proof = dict(at_utc=now.isoformat(), match_id=mid, provider=provider, ft=list(score),
                             provider_id=row.get('bsd_id'), period='regulation',
                             provider_observed_utc=observed.isoformat(), provider_available_utc=available.isoformat(),
                             identity_method=row.get('identity_method'), provider_record_sha256=digest)
                if conflicted:
                    writer.append('outcome_conflict', first_ft=list(previous) if previous is not None else None, **proof)
                    conflicts[mid].add((provider, score))
                else:
                    writer.append('outcome', **proof)
                    previous = first[mid][provider] = score
        reference = _utc(sig['kickoff_utc']) or _utc(sig['date_utc'])
        if (not versions and mid not in missing and not first[mid] and not conflicts[mid]
                and reference is not None and now - reference > MISSING_AFTER):
            writer.append('outcome_missing', at_utc=now.isoformat(), match_id=mid, after_hours=72)
            missing.add(mid)


def _band(minute):
    return '0-29' if minute < 30 else '30-59' if minute < 60 else '60-89'


def _state(score):
    margin, total = abs(score[0] - score[1]), sum(score)
    return ('level' if margin == 0 else 'one' if margin == 1 else 'two_plus'), ('0' if total == 0 else '1' if total == 1 else '2+')


def summary(entries):
    from .parser import settle
    signals = [e for e in entries if e['kind'] == 'signal']
    clean = [e for e in signals if not e['result_known_at_registration']]
    by_game = collections.defaultdict(list)
    for e in clean:
        by_game[e['match_id']].append(e)
    results = collections.defaultdict(dict)
    for e in entries:
        if e['kind'] == 'outcome':
            results[e['match_id']][e['provider']] = e['ft']
    conflicted = {e['match_id'] for e in entries if e['kind'] == 'outcome_conflict'}
    conflicted |= {m for m, r in results.items() if len({tuple(v) for v in r.values()}) > 1}
    confirmed = {m: next(iter(r.values())) for m, r in results.items() if m not in conflicted}
    missing = {e['match_id'] for e in entries if e['kind'] == 'outcome_missing'}
    games = {e['match_id'] for e in signals}

    def first(rows, predicate):
        chosen = {}
        for e in rows:
            if predicate(e) and e['match_id'] not in chosen:
                chosen[e['match_id']] = e
        return chosen

    h1 = first(clean, lambda e: e['h1'])
    rows = []
    for mid, e in h1.items():
        if mid in confirmed:
            minute, score = e['minute'], e['score_at_signal']
            p0 = 1 - math.exp(-GOALS_PER_MIN * max(1, 95 - minute))
            rows.append(dict(y=int(sum(confirmed[mid]) > sum(score)), p0=p0, band=_band(minute), state=_state(score)))
    o, ex = sum(r['y'] for r in rows), sum(r['p0'] for r in rows)
    var = sum(r['p0'] * (1 - r['p0']) for r in rows)
    z = (o - ex) / math.sqrt(var) if var > 0 else None
    bands = {}
    for band in ('0-29', '30-59', '60-89'):
        sub = [r for r in rows if r['band'] == band]
        bands[band] = dict(games=len(sub), observed=sum(r['y'] for r in sub), expected=round(sum(r['p0'] for r in sub), 3))
    strata = collections.defaultdict(lambda: dict(games=0, observed=0, expected=0.0))
    for r in rows:
        s = strata['margin_%s|goals_%s' % r['state']]
        s['games'] += 1
        s['observed'] += r['y']
        s['expected'] = round(s['expected'] + r['p0'], 3)
    if len(rows) < MIN_GAMES:
        h1_status = 'insufficient_games'
    else:
        ok = z >= Z_CRIT and all(b['observed'] > b['expected'] for b in bands.values() if b['games'] >= MIN_BAND)
        h1_status = 'supported_predictive_only' if ok else 'not_supported'
    h2 = first(clean, lambda e: e['h2'])
    h2_rows = []
    for mid, e in h2.items():
        if mid in confirmed:
            w = settle(e['market'], e['selection'], confirmed[mid], e['teams'])
            if w is not None:
                h2_rows.append(dict(w=w, odds=e['odds']))
    return dict(
        schema='goal_roi_prospective_report_v1', ledger_entries=len(entries),
        activated_utc=entries[0]['at_utc'] if entries else None,
        games_registered=len(games), signals_registered=len(signals),
        signals_registered_before_result=len(clean),
        signals_result_known_at_registration=len(signals) - len(clean),
        games_with_independent_result=len(confirmed), games_with_conflicting_results=len(conflicted),
        games_without_result_after_72h=len(missing - set(confirmed) - conflicted),
        independent_result_share=round(len(confirmed) / len(games), 4) if games else None,
        H1=dict(games_in_population=len(h1), games_with_result=len(rows), observed=o, expected=round(ex, 3),
                z=round(z, 3) if z is not None else None, minute_bands=bands, score_strata=dict(strata),
                status=h1_status, note='predictive information relative to a generic rate; not profitability'),
        H2=dict(games_in_population=len(h2), games_settled=len(h2_rows),
                wins=sum(1 for r in h2_rows if r['w'] > 0), losses=sum(1 for r in h2_rows if r['w'] < 0),
                mean_implied=round(sum(1 / r['odds'] for r in h2_rows) / len(h2_rows), 4) if h2_rows else None,
                status='insufficient_games' if len(h2_rows) < MIN_GAMES else 'descriptive_only',
                note='earlier edge did not replicate; no ROI validation without executable prices and known costs'),
        D1=dict(opposite_totals_games=len({e['match_id'] for e in entries if e['kind'] == 'diagnostic'}),
                status='diagnostic_only'),
        verdict='RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.')


def write_summary(entries):
    path = data_dir() / REPORT_NAME
    path.write_text(json.dumps(summary(entries), indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else 'record'
    try:
        if command == 'verify':
            entries = load()
            verify(entries)
            print('PROSPECTIVE_OK', len(entries))
            return 0
        if command == 'record':
            added = record()
            entries = load()
            verify(entries)
            write_summary(entries)
            print('PROSPECTIVE_RECORDED', json.dumps(dict(new_signals=added, entries=len(entries))))
            return 0
    except (LedgerError, OSError, ValueError) as error:
        print('PROSPECTIVE_FAILED', type(error).__name__, str(error)[:200])
        return FAILED_EXIT
    print('usage: python -m scangoal.prospective [record|verify]')
    return 2


if __name__ == '__main__':
    sys.exit(main())

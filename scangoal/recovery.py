"""Isolated Result Recovery. Offline by default; never writes production data.

Run `python -m scangoal.recovery --help`. Live requests require both --backfill
and --allow-live plus an explicit shared-quota allocation. No workflow calls this.
"""
import argparse
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode

from .chronology import parse_utc
from .recovery_audit import audit, days_for, reference, snapshot
from .results_bsd import (ROOT, kickoff_of, load_aliases, name_of, norm_team,
                          request_json as bsd_request, score_of, time_ok)
from .results_hl import request_json as hl_request, to_event
from .store import data_dir

UTC = timezone.utc
PLAN_CAP = {'hl': 100, 'bsd': 7500}
SIZE = {'hl': 100, 'bsd': 200}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stamp(now):
    return now.astimezone(UTC).isoformat(timespec='seconds')


def initial_state():
    return {'version': 1, 'pages': {}, 'usage': {}, 'attempts': [], 'observations': {},
            'accepted': {}, 'conflicts': {}, 'alias_proposals': {}}


def targets_from(bets, now, history_days=30):
    out = {}
    for b in bets:
        ref = reference(b)
        wait = 110 if b.get('kickoff_utc') else 105
        if b['w'] is not None or b['ft'] is not None or ref is None or not b.get('teams') or len(b['teams']) != 2 or not b.get('match_id'):
            continue
        if timedelta(minutes=wait) <= now - ref <= timedelta(days=history_days):
            t = {k: b.get(k) for k in ('teams', 'date_utc', 'kickoff_utc', 'league')}
            t['days'] = days_for(b)
            # Multiple signals for a game must agree on identity. Retain all time windows.
            item = out.setdefault(b['match_id'], {'variants': [], 'days': [], 'invalid': False})
            if item['variants'] and item['variants'][0]['teams'] != t['teams']:
                item['invalid'] = True
            if t not in item['variants']:
                item['variants'].append(t)
            item['days'] = sorted(set(item['days']) | set(t['days']))
    return out


def protected(name):
    words = norm_team(name).split()
    return tuple(sorted(x for x in words if x in {'women', 'woman', 'ladies', 'feminine', 'femenino', 'ii', 'iii', 'b', 'reserves'} or re.fullmatch(r'u\d{2}', x)))


def names_equal(a, b, aliases):
    # Even legacy reviewed aliases must not erase gender/reserve/age markers.
    if protected(a) != protected(b):
        return False
    na, nb = norm_team(a), norm_team(b)
    return bool(na and nb and (na == nb or nb in {norm_team(x) for x in aliases.get(na, [])}))


def identity(target, events, aliases):
    if target.get('invalid'):
        return None, 'target_identity_conflict'
    variants = target['variants']
    exact = []
    for ev in events:
        pair = (name_of(ev, 'home_team'), name_of(ev, 'away_team'))
        if all(all(names_equal(t['teams'][i], pair[i], aliases) for i in (0, 1)) and time_ok(t, kickoff_of(ev)) for t in variants):
            # If both providers use the same competition designation it is useful evidence.
            # Different or absent competition labels require reviewed mapping, not fuzzy acceptance.
            leagues = {norm_team(t['league']) for t in variants if t.get('league')}
            allowed_leagues = set(leagues)
            for league in leagues:
                allowed_leagues.update(norm_team(x) for x in aliases.get('_competitions', {}).get(league, []))
            if leagues and norm_team(name_of(ev, 'league')) not in allowed_leagues:
                continue
            exact.append(ev)
    # Dedupe repeated pagination rows only if all relevant content agrees.
    unique = {digest(ev): ev for ev in exact}
    if len(unique) != 1:
        return None, 'multiple_candidates' if unique else 'no_deterministic_identity'
    ev = next(iter(unique.values()))
    return ev, 'exact_or_reviewed_alias+all_signal_times+competition+unique'


def proposal(target, ev):
    t = target['variants'][0]
    if not time_ok(t, kickoff_of(ev)):
        return None
    pair = [name_of(ev, k) for k in ('home_team', 'away_team')]
    if any(protected(a) != protected(b) for a, b in zip(t['teams'], pair)):
        return None
    sims = [SequenceMatcher(None, norm_team(a), norm_team(b)).ratio() for a, b in zip(t['teams'], pair)]
    if min(sims) < .5 or max(sims) < .8:
        return None
    return {'bot': t['teams'], 'candidate': pair, 'event_id': ev.get('id'), 'event_date': ev.get('event_date'),
            'league': ev.get('league'), 'score': score_of(ev), 'similarities': sims,
            'status': 'review_required', 'accepted': False}


def parse_page(source, payload, offset):
    remaining = None
    if source == 'hl':
        if not isinstance(payload, tuple) or len(payload) != 2:
            raise ValueError('invalid_response')
        payload, remaining = payload
    if not isinstance(payload, dict):
        raise ValueError('invalid_response')
    rows = payload.get('data' if source == 'hl' else 'results')
    total = (payload.get('pagination') or {}).get('totalCount') if source == 'hl' else payload.get('count')
    if not isinstance(rows, list) or type(total) is not int or total < 0 or any(not isinstance(r, dict) for r in rows):
        raise ValueError('invalid_response')
    if source == 'hl' and (payload.get('pagination') or {}).get('offset', offset) != offset:
        raise ValueError('offset_mismatch')
    if len(rows) > SIZE[source] or offset + len(rows) > total:
        raise ValueError('invalid_response')
    events = [to_event(r) for r in rows] if source == 'hl' else rows
    if any(type(e.get('id')) is not int for e in events):
        raise ValueError('invalid_event_id')
    return events, total, remaining


def reconcile(state, targets, aliases, existing):
    """Rebuild staged acceptances; corrections/conflicts revoke staging, never rewrite history."""
    accepted = {}
    for mid, target in targets.items():
        observed, bad = [], []
        for src in PLAN_CAP:
            pages = [state['pages'].get(src + '|' + day) for day in target['days']]
            evs = [ev for page in pages if page for ev in page.get('events', {}).values()]
            for ev in evs:
                p = proposal(target, ev)
                if p and p['bot'] != p['candidate']:
                    state['alias_proposals'].setdefault(digest([src, mid, p]), dict(p, source=src, match_id=mid))
            ev, criterion = identity(target, evs, aliases)
            if criterion in ('multiple_candidates', 'target_identity_conflict'):
                bad.append(criterion)
            if ev:
                versions = state['observations'].get(src + '|' + str(ev['id']), [])
                if len({digest(v['event']) for v in versions}) > 1:
                    bad.append('provider_event_changed')
                score = score_of(ev)
                if score is None:
                    bad.append('non_regular_or_invalid_score')
                    continue
                # Uniqueness cannot be certified from a partial set of pages.
                complete = all(page and page.get('status') == 'complete' for page in pages)
                observed.append({'complete_search': complete, 'source': src, 'event_id': ev['id'], 'ft': score, 'status': ev['status'],
                                 'event_date': ev.get('event_date'), 'criteria': criterion,
                                 'observed_utc': versions[0]['observed_utc'] if versions else None,
                                 'raw_sha256': digest(ev), 'event': ev})
        scores = {tuple(o['ft']) for o in observed}
        historical = existing.get(mid)
        if historical and scores and tuple(historical['ft']) not in scores:
            bad.append('previously_confirmed_conflict')
        if len(scores) > 1:
            bad.append('provider_conflict')
        if bad:
            state['conflicts'][mid] = sorted(set(bad))
        if len(scores) == 1 and any(o['complete_search'] for o in observed) and not bad and mid not in state['conflicts'] and not historical:
            observed.sort(key=lambda o: o['observed_utc'] or '9999')
            accepted[mid] = {'ft': observed[0]['ft'], 'matched_at_utc': observed[0]['observed_utc'],
                             'evidence': observed, 'historical_recovery': True}
    state['accepted'] = accepted


def run_batch(state, targets, fetch, now, *, aliases=None, existing=None, max_requests=4,
              daily=None, reserve=None, available=None, save=lambda s: None, max_passes=2):
    """fetch(source, day, offset, limit). Persist quota reservation BEFORE each request.

    Caller holds the state lock. No implicit retry; errors cool down for at least 1h,
    429 blocks that provider for the UTC day. A completed day gets at most two passes.
    """
    state = copy.deepcopy(state)
    aliases, existing = aliases or {}, existing or {}
    daily = daily or {'hl': 5, 'bsd': 20}
    reserve = reserve or {'hl': 20, 'bsd': 100}
    available = available or {'hl': 0, 'bsd': 0}
    for src in PLAN_CAP:
        if not (0 <= daily[src] <= PLAN_CAP[src] and 0 <= available[src] <= PLAN_CAP[src] and 0 <= reserve[src] <= PLAN_CAP[src]):
            raise ValueError('quota configuration outside free plan')
    for src in PLAN_CAP:
        for day in sorted({d for t in targets.values() for d in t['days']}):
            state['pages'].setdefault(src + '|' + day, {'offset': 0, 'status': 'pending', 'events': {}, 'passes': 0, 'attempts': 0, 'page_hashes': []})
    used = 0
    while used < max_requests:
        jobs = []
        for key, page in state['pages'].items():
            src, day = key.split('|')
            if not any(day in t['days'] for t in targets.values()):
                continue
            quota_key = src + '|' + now.date().isoformat()
            q = state['usage'].setdefault(quota_key, {'reserved_requests': 0})
            if q.get('blocked') or q['reserved_requests'] >= daily[src] or q['reserved_requests'] >= available[src] - reserve[src]:
                continue
            if q.get('remaining') is not None and q['remaining'] <= reserve[src]:
                continue
            retry = parse_utc(page.get('retry_at_utc'))
            if retry and now < retry:
                continue
            if page.get('terminal_error') or page.get('failures', 0) >= 3:
                continue
            if page['status'] == 'complete' and page['passes'] >= max_passes:
                continue
            # Least-attempted first prevents starvation; continue partial pages before new days on ties.
            jobs.append((page['attempts'], page['status'] != 'incomplete', day, src, key))
        if not jobs:
            break
        _, _, day, src, key = min(jobs)
        page = state['pages'][key]
        if page['status'] == 'complete':
            page.update(offset=0, events={}, page_hashes=[], status='pending')
        offset = page['offset']
        q = state['usage'][src + '|' + now.date().isoformat()]
        q['reserved_requests'] += 1
        page['attempts'] += 1
        used += 1
        attempt = {'source': src, 'date': day, 'offset': offset, 'started_utc': stamp(now), 'outcome': 'reserved'}
        state['attempts'].append(attempt)
        save(state)  # a crash may lose a response but never refund a request
        try:
            payload = fetch(src, day, offset, SIZE[src])
            observed_at = datetime.now(UTC) if getattr(fetch, 'live', False) else now
            events, total, remaining = parse_page(src, payload, offset)
            if remaining is not None:
                q['remaining'] = remaining
            fingerprint = digest(events)
            if events and fingerprint in page['page_hashes']:
                raise ValueError('repeated_page')
            if not events and offset < total:
                raise ValueError('truncated_page')
            if offset and page.get('total') != total:
                raise ValueError('pagination_total_changed_requires_review')
            if any(str(e['id']) in page['events'] for e in events):
                raise ValueError('overlapping_page_requires_review')
            page['failures'] = 0
            page['total'] = total
            for ev in events:
                page['events'][str(ev['id'])] = ev
                obs = state['observations'].setdefault(src + '|' + str(ev['id']), [])
                if not any(digest(v['event']) == digest(ev) for v in obs):
                    obs.append({'event': ev, 'observed_utc': stamp(observed_at)})
            page['page_hashes'].append(fingerprint)
            page['offset'] += len(events)
            page['status'] = 'complete' if page['offset'] >= total and len(page['events']) >= total else 'incomplete'
            if page['status'] == 'complete':
                page['passes'] += 1
                page['retry_at_utc'] = stamp(now + timedelta(days=1))
            attempt.update(outcome=page['status'], observed_utc=stamp(observed_at), count=len(events), total=total)
        except Exception as exc:
            # Never persist the transport exception text: URLs, tokens and bodies may be sensitive.
            rate_limited = '429' in str(exc)
            why = str(exc) if type(exc) is ValueError and str(exc) in {'invalid_response', 'invalid_event_id', 'offset_mismatch', 'repeated_page', 'truncated_page', 'pagination_total_changed_requires_review', 'overlapping_page_requires_review'} else ('http_429' if rate_limited else 'transport_unavailable')
            page.update(status='failed', retry_at_utc=stamp(now + timedelta(hours=1)))
            page['failures'] = page.get('failures', 0) + 1
            if why in {'repeated_page', 'pagination_total_changed_requires_review', 'overlapping_page_requires_review'}:
                page['terminal_error'] = why
            attempt['outcome'] = why
            if rate_limited:
                q['blocked'] = True
        reconcile(state, targets, aliases, existing)
        save(state)
    reconcile(state, targets, aliases, existing)
    save(state)
    return state


class LiveFetch:
    live = True

    def __call__(self, source, day, offset, limit):
        if source == 'hl':
            return hl_request({'date': day, 'offset': offset, 'limit': limit}, os.environ['HIGHLIGHTLY_KEY'])
        return bsd_request(ROOT + '?' + urlencode({'date_from': day, 'date_to': day, 'limit': limit, 'offset': offset}), os.environ['BSD_API_KEY'])


def replay_events(states):
    """Only saved, verified event records, never invent scores from near-miss names."""
    out = {'hl': [], 'bsd': []}
    for src, st in states.items():
        for r in st.get('results', {}).values():
            out[src].append({'id': r.get('bsd_id'), 'status': 'finished', 'home_team': r.get('home'),
                             'away_team': r.get('away'), 'event_date': r.get('event_date'),
                             'home_score': r['ft'][0], 'away_score': r['ft'][1], 'league': r.get('league')})
    return out


def offline_trial(now, aliases=None):
    bets, states, ext = snapshot()
    targets = targets_from(bets, now)
    state = initial_state()
    for src, events in replay_events(states).items():
        for ev in events:
            dt = parse_utc(ev['event_date'])
            if dt is None or type(ev['id']) is not int:
                continue
            day = dt.date().isoformat()
            page = state['pages'].setdefault(src + '|' + day, {'events': {}, 'status': 'incomplete'})
            page['events'][str(ev['id'])] = ev
            state['observations'][src + '|' + str(ev['id'])] = [{'event': ev, 'observed_utc': stamp(now)}]
    reconcile(state, targets, aliases if aliases is not None else load_aliases(), ext)
    merged = dict(ext)
    for mid, res in state['accepted'].items():
        merged[mid] = dict(res, provider='recovery-offline')
    # Existing production settlement code on a separate in-memory view; no ledger/report writes.
    chron = {}
    msgs, ft, marks, notes, _ = __import__('scangoal.report', fromlist=['load']).load(chron)
    details = json.loads((data_dir() / 'details_hl.json').read_text()).get('details', {})
    from .report import build_bets
    after = build_bets(msgs, ft, marks, notes, bsd=merged, details=details, chronology=chron)
    def metrics(rows):
        pending = [b for b in rows if b['w'] is None]
        return {'unique_bets': len(rows), 'settled_bets': len(rows)-len(pending), 'pending_bets': len(pending),
                'distinct_pending_matches': len({b['match_id'] for b in pending})}
    return {'as_of_utc': stamp(now), 'before': metrics(bets), 'after': metrics(after),
            'demonstrated_recovered_matches': len(state['accepted']), 'accepted': state['accepted'],
            'ambiguous_or_conflicting_matches': state['conflicts'], 'alias_proposals': list(state['alias_proposals'].values()),
            'api_requests_made': 0, 'minimum_first_pass_requests': {s: len({d for t in targets.values() for d in t['days']}) for s in ('hl','bsd')},
            'limitation': 'Saved result records omit competition and raw pages; near-misses omit scores, dates and IDs. No assumed recoveries. Minimum request estimate excludes additional pages.'}


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=1, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--backfill', action='store_true')
    p.add_argument('--allow-live', action='store_true', help='only after explicit operator authorization')
    p.add_argument('--reviewed-mappings', type=Path, help='isolated, human-reviewed team and competition mappings JSON')
    p.add_argument('--history-days', type=int, default=30)
    p.add_argument('--max-requests', type=int, default=4)
    p.add_argument('--output', type=Path, required=True, help='isolated directory outside data/ and docs/')
    p.add_argument('--now', help='fixed UTC audit/replay clock; forbidden for live')
    for src, default, reserve in (('hl',5,20), ('bsd',20,100)):
        p.add_argument('--available-' + src, type=int, default=0, help='operator verified unspent shared quota for UTC day')
        p.add_argument('--daily-' + src, type=int, default=default)
        p.add_argument('--reserve-' + src, type=int, default=reserve)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = a.output.resolve()
    forbidden = [data_dir().resolve(), root / 'data', root / 'docs', root / 'scangoal', root / '.github', root / '.git']
    if any(output == f or f in output.parents or output in f.parents for f in forbidden):
        p.error('output must be isolated from production, source and git metadata')
    if a.history_days < 1 or a.max_requests < 0:
        p.error('invalid limits')
    now = parse_utc(a.now) if a.now else datetime.now(UTC)
    if now is None:
        p.error('--now requires explicit timezone')
    if a.allow_live and (not a.backfill or a.dry_run or a.now):
        p.error('live requires --backfill, without --dry-run/--now')
    aliases = load_aliases()
    if a.reviewed_mappings:
        mapping = json.loads(a.reviewed_mappings.read_text(encoding='utf-8'))
        if mapping.get('reviewed') is not True or not mapping.get('evidence'):
            p.error('mappings require reviewed=true and evidence')
        for section in ('teams', 'competitions'):
            value = mapping.get(section, {})
            if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, list) or not all(isinstance(x, str) for x in v) for k, v in value.items()):
                p.error('mapping sections must map names to lists of names')
        aliases.update({norm_team(k): v for k, v in mapping.get('teams', {}).items()})
        aliases['_competitions'] = {norm_team(k): v for k, v in mapping.get('competitions', {}).items()}
    if a.audit:
        write_json(output / 'pending.json', audit(now))
    if a.dry_run or (a.backfill and not a.allow_live):
        write_json(output / 'trial.json', offline_trial(now, aliases))
    if a.allow_live:
        for src, key in (('hl','HIGHLIGHTLY_KEY'), ('bsd','BSD_API_KEY')):
            if getattr(a, 'available_' + src) > getattr(a, 'reserve_' + src) and not os.environ.get(key):
                p.error('missing key for allocated provider')
        # Exclusive lock plus atomic checkpoint, including pre-request reservation.
        import fcntl
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'state.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state_path = output / 'state.json'
            state = json.loads(state_path.read_text()) if state_path.exists() else initial_state()
            bets, _, ext = snapshot()
            if a.reviewed_mappings:
                mapping_id = digest(mapping)
                state.setdefault('reviewed_mappings', {})[mapping_id] = mapping
            run_batch(state, targets_from(bets, now, a.history_days), LiveFetch(), now, aliases=aliases, existing=ext,
                      max_requests=a.max_requests, daily={s:getattr(a,'daily_'+s) for s in PLAN_CAP},
                      reserve={s:getattr(a,'reserve_'+s) for s in PLAN_CAP}, available={s:getattr(a,'available_'+s) for s in PLAN_CAP},
                      save=lambda s: write_json(state_path, s))
    if not (a.audit or a.dry_run or a.backfill):
        p.error('select --audit, --dry-run or --backfill')


if __name__ == '__main__':
    main()

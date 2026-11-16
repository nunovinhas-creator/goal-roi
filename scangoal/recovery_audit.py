"""Read-only, reproducible pending-bet census. No inference of coverage from absence."""
import hashlib
import json
from collections import Counter
from datetime import timedelta

from . import report
from .chronology import parse_utc
from .results_bsd import load_json
from .store import data_dir


def snapshot():
    chron = {}
    msgs, ft, marks, notes, _ = report.load(chron)
    states = {s: load_json(data_dir() / ('results_' + s + '.json'), {}) for s in ('hl', 'bsd')}
    ext = {}
    chron['external'] = {}
    for src, state in states.items():
        for mid, res in state.get('results', {}).items():
            ext.setdefault(mid, dict(res, provider=src.upper()))
            chron['external'].setdefault(mid, []).append(res.get('matched_at_utc'))
    details = load_json(data_dir() / 'details_hl.json', {}).get('details', {})
    chron['details'] = {m: d.get('fetched_at_utc') for m, d in details.items()}
    manual = load_json(data_dir() / 'manual_results.json', {})
    for mid, res in manual.items():
        if mid.startswith('_') or not isinstance(res, dict) or 'ft' not in res:
            continue
        chron['external'].setdefault(mid, []).append(res.get('observed_utc'))
        ext.setdefault(mid, dict(res, provider='manual', known_utc=res.get('observed_utc')))
    # Use the exact production settlement rules, including verified goal details.
    import re
    for mid, d in details.items():
        if d.get('consistent') and d.get('score_90') and re.search(r'extra time|penalt', d.get('status') or '', re.I):
            ext.setdefault(mid, {'ft': d['score_90'], 'provider': 'HL aos 90 min', 'known_utc': d.get('fetched_at_utc')})
    bets = report.build_bets(msgs, ft, marks, notes, bsd=ext, details=details,
                             not_covered=states['hl'].get('not_covered', {}), chronology=chron)
    return bets, states, ext


def reference(b):
    return parse_utc(b.get('kickoff_utc') or b.get('date_utc'))


def days_for(b):
    ref = reference(b)
    if ref is None:
        return []
    # Search both sides of UTC midnight, including the entire ±90m prematch tolerance.
    end = ref + timedelta(minutes=90) if b.get('kickoff_utc') else ref
    return sorted({(ref - timedelta(minutes=150)).date().isoformat(), ref.date().isoformat(), end.date().isoformat()})


def audit(now):
    bets, states, ext = snapshot()
    rows = []
    for b in bets:
        if b['w'] is not None:
            continue
        mid, ref = b['match_id'], reference(b)
        evidence = {}
        for src, age in (('hl', 2), ('bsd', 3)):
            st = states[src]
            perday = st.get('stats', {}).get('per_day', {})
            evidence[src] = {
                'stored_result': st.get('results', {}).get(mid),
                'near_miss': st.get('near_misses', {}).get(mid),
                'legacy_not_covered': st.get('not_covered', {}).get(mid),
                'non_regular_time': st.get('not_regular_time', {}).get(mid),
                'last_run_utc': st.get('last_run_utc'),
                'latest_day_scans': {d: perday[d] for d in days_for(b) if d in perday},
                'outside_current_window': bool(ref and now - ref > timedelta(days=age)),
                'stop_reason': st.get('stats', {}).get('stopped'),
                'quota_remaining': st.get('stats', {}).get('quota_remaining'),
                'attempt_history': 'unknown: legacy state stores only latest aggregate scan, not per-target attempts',
            }
        incomplete = any(type(p.get('count_reported')) is int and p.get('events_read', 0) < p['count_reported']
                         for e in evidence.values() for p in e['latest_day_scans'].values())
        if b['ft'] is not None:
            cat, why = 'F', 'FT exists; existing settlement rules require market-specific data or bot mark'
        elif ref and now - ref < timedelta(minutes=110 if b.get('kickoff_utc') else 105):
            cat, why = 'I', 'conservative completion waiting period not elapsed (not evidence of actual live status)'
        elif any(e['non_regular_time'] for e in evidence.values()):
            cat, why = 'G', 'provider recorded non-regular-time ending; verified 90-minute score missing'
        elif any(e['near_miss'] for e in evidence.values()):
            cat, why = 'H', 'candidate names retained without event ID, score or kickoff: identity and availability unverified'
        elif incomplete:
            cat, why = 'C', 'latest saved scan for a relevant day is incomplete; event availability unknown'
        else:
            cat, why = 'J', 'unknown: no raw event pages or full attempt history; cannot prove A/B/D/E'
        rows.append(dict(bet_id=hashlib.sha256(json.dumps(b['key'], ensure_ascii=False).encode()).hexdigest()[:20],
                         **{k: b.get(k) for k in ('key', 'match_id', 'message_id', 'date_utc', 'kickoff_utc', 'teams', 'league', 'type', 'phase', 'market', 'selection', 'ft')},
                         category=cat, reason=why, sources=evidence,
                         result_in_another_source=mid in ext,
                         missing_information='raw event identity, kickoff, status and score; complete historical query journal' if cat in ('H', 'J') else None))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir().glob('*')) if p.is_file()}
    return {'as_of_utc': now.isoformat(), 'input_sha256': hashes, 'unique_bets': len(bets),
            'settled_bets': sum(b['w'] is not None for b in bets), 'pending_bets': len(rows),
            'distinct_pending_matches': len({r['match_id'] for r in rows}),
            'by_category': dict(Counter(r['category'] for r in rows)),
            'by_market': dict(Counter(r['market'] for r in rows)),
            'by_source': {s: {'stored_results_for_pending': sum(bool(r['sources'][s]['stored_result']) for r in rows),
                              'near_misses_pending_bets': sum(bool(r['sources'][s]['near_miss']) for r in rows),
                              'outside_window_pending_bets': sum(r['sources'][s]['outside_current_window'] for r in rows)} for s in states},
            'rows': rows}

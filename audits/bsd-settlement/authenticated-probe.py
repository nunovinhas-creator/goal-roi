"""One-shot GitHub Actions probe. Emits derived evidence only."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay import LECCO, original_checks, report_rejection
from scangoal import chronology, prospective, results_bsd
from scangoal.report import external_matches

DAYS = ('2026-11-25', '2026-11-24', '2026-11-23', '2026-11-22')
DETAIL_ID = 655441
MAX_REQUESTS = 5


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def emit(label, data):
    print(label, json.dumps(data, sort_keys=True, separators=(',', ':')), flush=True)


def audit(snapshot, older):
    token = os.environ.pop('BSD_API_KEY', '').strip()
    if not token:
        emit('BSD_PROBE_STOP', {'attempts': 0, 'reason': 'missing_secret'})
        return 2
    evidence = json.loads(Path(__file__).with_name('public-evidence.json').read_bytes())
    raw = (snapshot / 'data/results_bsd.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence['diagnostic_snapshot_sha256']:
        return 2
    state = json.loads(raw)
    ids = {mid: row['competition_review'][0]['provider_id'] for mid, row in state['near_misses'].items()
           if row.get('competition_review')}
    checks = original_checks(snapshot)['checks']
    lecco = original_checks(older)['checks'][LECCO]['target']
    targets = {mid: checks[mid]['target'] for mid in ids if mid in checks}
    targets[LECCO] = lecco
    public = {row['id']: row for row in evidence['rows']}
    entries = prospective.load(snapshot / 'data/prospective_ledger.jsonl')
    prospective.verify(entries)
    first, highlightly = {}, {}
    for entry in entries:
        if entry['kind'] == 'signal':
            first.setdefault(entry['match_id'], entry)
        elif entry['kind'] == 'outcome' and entry['provider'] == 'Highlightly':
            highlightly[entry['match_id']] = entry['ft']
    os.environ['SCANGOAL_DATA_DIR'] = str(snapshot / 'data')
    aliases = results_bsd.load_aliases()
    opener = build_opener(ProxyHandler({}), RejectRedirect())
    attempts = 0
    remaining = None

    def fetch(suffix):
        nonlocal attempts, remaining
        if attempts >= MAX_REQUESTS or (attempts and (remaining is None or remaining < 1)):
            raise RuntimeError('quota_or_cap')
        if attempts:
            time.sleep(1)
        attempts += 1
        request = Request(results_bsd.ROOT + suffix, headers={'Authorization': 'Token ' + token,
                          'Accept': 'application/json', 'User-Agent': 'goal-roi-isolated-audit/1'})
        with opener.open(request, timeout=20) as response:
            raw_body = response.read(3_000_001)
            if len(raw_body) > 3_000_000:
                raise RuntimeError('body_limit')
            rate = re.fullmatch(r'\s*"football";\s*r=(\d+);\s*t=(\d+)\s*', response.headers.get('RateLimit', ''))
            remaining = int(rate[1]) if rate else None
            emit('BSD_PROBE_RECEIPT', {'attempt': attempts, 'http': response.status,
                                      'sha256': hashlib.sha256(raw_body).hexdigest(),
                                      'observed_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                      'football_remaining': remaining})
            return json.loads(raw_body)

    events = []
    try:
        for day in DAYS:
            page = fetch('?' + urlencode({'date_from': day, 'date_to': day, 'status': 'finished',
                                          'limit': 200, 'offset': 0}))
            rows = page['results']
            if not isinstance(rows, list) or not all(isinstance(event, dict) for event in rows):
                raise RuntimeError('unexpected_page')
            emit('BSD_PROBE_PAGE', {'day': day, 'count': page['count'] if type(page['count']) is int else None,
                                    'rows': len(rows), 'next': bool(page['next'])})
            if page['next'] or page['count'] != len(rows):
                raise RuntimeError('incomplete_page')
            events.extend(rows)
        if not any(row.get('id') == DETAIL_ID for row in events):
            raise RuntimeError('detail_id_not_in_list')
        detail = fetch(f'{DETAIL_ID}/')
        if detail.get('id') != DETAIL_ID:
            raise RuntimeError('unexpected_detail')
    except HTTPError as error:
        emit('BSD_PROBE_STOP', {'attempts': attempts, 'http': error.code})
        return 2
    except Exception as error:
        allowed = {'quota_or_cap', 'body_limit', 'unexpected_page', 'incomplete_page',
                   'detail_id_not_in_list', 'unexpected_detail'}
        reason = str(error) if type(error) is RuntimeError and str(error) in allowed else 'transport_or_response'
        emit('BSD_PROBE_STOP', {'attempts': attempts, 'reason': reason})
        return 2

    def projection(event):
        return {key: event.get(key) for key in ('id', 'league_id', 'event_date', 'home_team', 'away_team',
                                                'status', 'home_score', 'away_score', 'extra_time_score',
                                                'penalty_shootout')}

    existing_fields = ('league', 'competition', 'league_id', 'league_name')
    emit('BSD_PROBE_SHAPE', {'detail_fields': [field for field in existing_fields if field in detail],
                            'list_presence': {field: sum(field in row for row in events) for field in existing_fields},
                            'rows': len(events), 'detail_id': DETAIL_ID,
                            'detail_and_list_agree': any(projection(row) == projection(detail) for row in events
                                                         if row.get('id') == DETAIL_ID)})
    matched, _ = results_bsd.match_events(targets, events, aliases)
    verdicts = []
    for mid, event_id in sorted(ids.items()):
        public_row = public[event_id]
        observed = [event for event in events if event.get('id') == event_id]
        candidate = observed[0] if len(observed) == 1 else None
        row = matched.get(mid)
        bound = bool(row and row['bsd_id'] == event_id and external_matches(dict(targets[mid], match_id=mid), row))
        prospective_bound = bool(bound and mid in first and external_matches(first[mid], row))
        verdicts.append({'id': event_id, 'list_rows': len(observed),
                         'league_id_shape': ('absent' if not candidate or 'league_id' not in candidate
                                             else type(candidate['league_id']).__name__),
                         'period_fields_present': [field for field in ('extra_time_score', 'penalty_shootout')
                                                   if candidate and field in candidate],
                         'league_agrees_with_public': bool(candidate and candidate.get('league_id') == public_row['league_id']),
                         'kickoff_agrees_with_public': bool(candidate and results_bsd.kickoff_of(candidate)
                                                            == chronology.parse_utc(public_row['event_date'])),
                         'score_agrees_with_public': bool(candidate and results_bsd.score_of(candidate)
                                                          == public_row['page_score']),
                         'bound': bound, 'prospective_bound': prospective_bound,
                         'agrees_with_highlightly': bool(prospective_bound and highlightly.get(mid) == row['ft']),
                         'reason': report_rejection(targets[mid], candidate, aliases) if candidate and not bound else None})
    emit('BSD_PROBE_VERDICTS', {'attempts': attempts, 'cases': verdicts})
    return 0


if __name__ == '__main__':
    try:
        sys.exit(audit(Path(sys.argv[1]), Path(sys.argv[2])))
    except Exception:
        emit('BSD_PROBE_STOP', {'reason': 'local_evidence_validation'})
        sys.exit(2)

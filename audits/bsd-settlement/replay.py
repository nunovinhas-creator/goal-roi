#!/usr/bin/env python3
"""Offline BSD replay against two historical checkouts and archived public evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scangoal import chronology, opportunity_results, prospective, results_bsd
from scangoal.report import external_matches


LECCO = 'lecco-vs-lumezzane-20261123'
OLDER = '58cd373ecb07df58f04afb0417cde6ac2c9ed6f2'
OLDER_DIAGNOSTIC_SHA = 'fd03a124aabe17fe064a18266a38b820cb1b0a197b44b735c0e6a3b82d2b3f54'
LEDGER_SHA = '8cb16554c2e09341f001fd501d75046fc7202edb57c280e56fd13b046a1630fd'
BASELINE = """
import json
from datetime import datetime
from scangoal.results_bsd import competition_ok, kickoff_of, pending_targets, time_ok
from scangoal.store import data_dir

state = json.loads((data_dir() / 'results_bsd.json').read_bytes())
now = datetime.fromisoformat(state['last_run_utc'])
targets = {mid: t for mid, t in pending_targets(now).items() if mid not in state['results']}
checks = {}
for mid, review in state['near_misses'].items():
    if mid in targets and review.get('competition_review'):
        diagnostic, = review['competition_review']
        event = {'id': diagnostic['provider_id'], 'event_date': diagnostic['event_date'],
                 'league': diagnostic['league']}
        target = targets[mid]
        checks[mid] = {'target': target, 'kickoff_compatible': time_ok(target, kickoff_of(event)),
                       'competition_accepted': competition_ok(target, event)}
print(json.dumps({'target_count': len(targets), 'checks': checks}))
"""


def original_checks(snapshot):
    env = dict(os.environ, SCANGOAL_DATA_DIR=str(snapshot / 'data'), PYTHONDONTWRITEBYTECODE='1')
    return json.loads(subprocess.check_output([sys.executable, '-c', BASELINE], cwd=snapshot, env=env))


def public_event(row, diagnostic, catalog):
    """Project independently observed public fields into the API matcher shape."""
    if (row['http_status'] != 200 or row['id'] != diagnostic['provider_id']
            or row['event_date'] != diagnostic['event_date']
            or row['url'] != f"{results_bsd.PUBLIC_ROOT}matches/{row['id']}/"
            or not any(league['http_status'] == 200 and league['id'] == row['league_id']
                       and league['name'] == row['league'] and league['url'] == row['league_url']
                       for league in catalog)):
        raise ValueError(f"Inconsistent public observation for event {row['id']}")
    event = {key: row[key] for key in ('id', 'league_id', 'league', 'home_team', 'away_team', 'event_date')}
    if row['page_status'] == ['Full time'] and not row['scoreboard_other_text']:
        event.update(status='finished', home_score=row['page_score'][0], away_score=row['page_score'][1])
    return event


def report_rejection(target, event, aliases):
    if not results_bsd.time_ok(target, results_bsd.kickoff_of(event)):
        return 'kickoff_mismatch'
    for name, side in zip(target['teams'], ('home_team', 'away_team')):
        team = results_bsd.norm_team(name)
        accepted = {team, *(results_bsd.norm_team(alias) for alias in aliases.get(team, ()))}
        if results_bsd.norm_team(event[side]) not in accepted:
            return 'team_mismatch'
    if not results_bsd.competition_ok(target, event):
        return 'competition_not_proven_compatible'
    if chronology.kickoff_time(target) is None:
        return 'explicit_kickoff_missing'
    if results_bsd.score_of(event) is None:
        return 'regulation_ft_unverified'
    return 'ambiguous_or_unverified_binding'


def replay(snapshot, older, evidence):
    raw = (snapshot / 'data/results_bsd.json').read_bytes()
    pinned = {
        snapshot / 'data/results_bsd.json': evidence['diagnostic_snapshot_sha256'],
        older / 'data/results_bsd.json': OLDER_DIAGNOSTIC_SHA,
        snapshot / 'data/prospective_ledger.jsonl': LEDGER_SHA,
    }
    for path, digest in pinned.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Snapshot checksum mismatch for {path}')
    state = json.loads(raw)
    diagnostics = {mid: review['competition_review'][0] for mid, review in state['near_misses'].items()
                   if review.get('competition_review')}
    baseline, lecco_baseline = original_checks(snapshot), original_checks(older)
    before = dict(baseline['checks'], **{LECCO: lecco_baseline['checks'][LECCO]})
    if set(before) != set(diagnostics) or any(not c['kickoff_compatible'] or c['competition_accepted']
                                             for c in before.values()):
        raise ValueError('Historical replay does not match archived diagnostics')
    public = {row['id']: row for row in evidence['rows']}
    if set(public) != {row['provider_id'] for row in diagnostics.values()}:
        raise ValueError('Public evidence does not cover exactly the diagnostic event IDs')
    events = {mid: public_event(public[row['provider_id']], row, evidence['leagues'])
              for mid, row in diagnostics.items()}
    targets = {mid: check['target'] for mid, check in before.items()}
    entries = prospective.load(snapshot / 'data/prospective_ledger.jsonl')
    prospective.verify(entries)
    first, highlightly = {}, {}
    for entry in entries:
        if entry['kind'] == 'signal':
            first.setdefault(entry['match_id'], entry)
        if entry['kind'] == 'outcome' and entry['provider'] == 'Highlightly':
            highlightly[entry['match_id']] = entry['ft']

    with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(snapshot / 'data')}):
        aliases = results_bsd.load_aliases()
        found, _ = results_bsd.match_events(targets, list(events.values()), aliases)
        replay_archive = {'results': {}}
        cut = max(chronology.parse_utc(row['observed_at_utc']) for row in public.values())
        results_bsd.capture_result_versions(replay_archive, found, list(events.values()), cut)
        rows = opportunity_results.provider_snapshot(replay_archive, 'bsd', cut)
        details = []
        for mid, event in sorted(events.items()):
            row = rows.get(mid)
            target = dict(targets[mid], match_id=mid)
            if row and not external_matches(target, row):
                raise ValueError(f'Report rejected collector binding for {mid}')
            signal = first.get(mid)
            hl_score = highlightly.get(mid)
            if signal is None:
                status = 'not_registered'
            elif chronology.kickoff_time(signal) is None:
                status = 'explicit_kickoff_missing_in_frozen_first_signal'
            elif not row:
                status = 'no_collector_binding'
            elif not external_matches(signal, row):
                status = 'frozen_fixture_binding_mismatch'
            elif hl_score is None:
                status = 'first_independent_result'
            elif hl_score != row['ft']:
                status = 'provider_conflict'
            else:
                status = 'agrees_with_existing_highlightly'
            details.append({
                'match_id': mid,
                'event_id': event['id'],
                'league_id': event['league_id'],
                'active_in_main_snapshot': mid in baseline['checks'],
                'before': 'competition_missing_and_no_prebound_id',
                'collector': 'bound' if row else report_rejection(target, event, aliases),
                'bsd_ft': row['ft'] if row else None,
                'first_signal_kickoff_utc': signal.get('kickoff_utc') if signal else None,
                'highlightly_ft': hl_score,
                'prospective': status,
            })

    confirmed = {'first_independent_result', 'agrees_with_existing_highlightly', 'provider_conflict'}
    return {
        'diagnostic_commit': evidence['diagnostic_commit'],
        'older_lecco_commit': OLDER,
        'public_observation_cut_utc': cut.isoformat(),
        'replay_scope': '17 retained competition diagnostics and separately observed public match details',
        'archived_run': {
            'targets': baseline['target_count'],
            'events': state['stats']['events_seen'],
            'requests': state['stats']['requests'],
            'matched': state['stats']['matched_this_run'],
            'historical_results': len(state['results']),
        },
        'comparison': {
            'candidates': len(details),
            'before': sum(check['competition_accepted'] for check in before.values()),
            'after_collector_bound': len(rows),
            'after_collector_bound_active': sum(
                d['active_in_main_snapshot'] and d['collector'] == 'bound' for d in details),
            'registered': sum(d['prospective'] != 'not_registered' for d in details),
            'existing_highlightly': sum(d['highlightly_ft'] is not None for d in details),
            'after_prospective_bindable': sum(d['prospective'] in confirmed for d in details),
            'after_prospective_first_independent': sum(
                d['prospective'] == 'first_independent_result' for d in details),
            'after_prospective_conflicts': sum(d['prospective'] == 'provider_conflict' for d in details),
        },
        'cases': details,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path, help='checkout at the main diagnostic commit')
    parser.add_argument('older', type=Path, help=f'checkout at the original Lecco diagnostic {OLDER}')
    parser.add_argument('--evidence', type=Path, default=Path(__file__).with_name('public-evidence.json'))
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_bytes())
    result = replay(args.snapshot.resolve(), args.older.resolve(), evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

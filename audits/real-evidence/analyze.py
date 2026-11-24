"""Coverage, observability and exploratory relations in the real Goal-ROI archive.

Read-only: SCANGOAL_DATA_DIR must point at a copy of data/. Uses the repository's
own parser, identity resolution and result reconciliation at an explicit clock.
No manual odds or results. Discovery and the later check use disjoint periods;
nothing here is validated evidence.
"""

import argparse
import collections
from datetime import datetime, timezone
import json
import math

from scangoal import chronology, opportunity_results, report
from scangoal.results_bsd import norm_team, time_ok
from scangoal.store import data_dir
from scangoal.eligibility import eligible, exclusion_reasons

SPLIT = datetime(2026, 11, 20, tzinfo=timezone.utc)
BANDS = ((1.10, 1.50), (1.50, 2.50), (2.50, 99.0))


def _t(value):
    return chronology.parse_utc(value) if isinstance(value, str) else value


def _pl(b):
    return report.pl(b['w'], b['odds'])


def _summary(rows):
    rows = [b for b in rows if b['w'] is not None and b['odds']]
    n = len(rows)
    if not n:
        return dict(n=0)
    pl = [_pl(b) for b in rows]
    mean = sum(pl) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in pl) / max(1, n - 1))
    games = len({b['canonical_match_id'] or b['match_id'] for b in rows})
    return dict(n=n, games=games, wins=sum(1 for b in rows if b['w'] == 1), roi=round(mean, 4),
                roi_ci95=[round(mean - 1.96 * sd / math.sqrt(n), 4), round(mean + 1.96 * sd / math.sqrt(n), 4)],
                implied=round(sum(1 / b['odds'] for b in rows) / n, 4))


def _band(odds):
    return next((f'{lo:.2f}-{hi:.2f}' for lo, hi in BANDS if lo <= odds < hi), 'out')


def coverage(msgs, signals, bets, options, now):
    games = {b['canonical_match_id'] or b['match_id'] for b in signals}
    with_odds = [b for b in signals if b['odds']]
    ft_known = {b['canonical_match_id'] or b['match_id'] for b in signals if b['ft']}
    external = set(options['bsd'])
    source = collections.Counter(str(b['result_known_source']) for b in signals if b['ft'])
    elig = [b for b in signals if eligible(b)]
    timely = [b for b in elig if b['w'] is not None and b['result_known_utc'] and b['signal_seen_utc']
              and _t(b['signal_seen_utc']) < _t(b['result_known_utc'])
              and (b['phase'] != 'pre' or not b['kickoff_utc'] or _t(b['signal_seen_utc']) < _t(b['kickoff_utc']))]
    reasons = collections.Counter(r for b in signals for r in exclusion_reasons(b))
    seen = sorted(_t(b['signal_seen_utc']) for b in signals if b['signal_seen_utc'])
    return dict(
        clock_utc=now.isoformat(), period=[seen[0].isoformat(), seen[-1].isoformat()],
        messages_parsed=len(msgs), signal_records=len(signals), unique_bets=len(bets),
        distinct_games=len(games),
        signals_with_explicit_odds=len(with_odds), games_with_odds=len({b['canonical_match_id'] or b['match_id'] for b in with_odds}),
        games_with_final_score=len(ft_known & games),
        games_with_independent_provider_result=len(external & games),
        result_sources=dict(source),
        settled_signals=sum(1 for b in signals if b['w'] is not None),
        eligible_signals=len(elig), exclusion_reasons=dict(reasons),
        evaluable_without_future_information=dict(
            signals=len(timely), games=len({b['canonical_match_id'] or b['match_id'] for b in timely}),
            rule='eligible, settled, seen before the result was known and, if pre-match, before kick-off'),
        prospective_ledger_entries=0)


def provider_binding(msgs, signals, now):
    """Why independent results do not reach the report (diagnostic; changes nothing)."""
    fixtures = collections.defaultdict(list)
    for m in msgs:
        if m.get('match_id') and m.get('teams'):
            fixtures[m['match_id']].append(m)
    bot = {b['match_id']: tuple(b['ft']) for b in signals if b['ft']}
    out = {}
    for name in ('hl', 'bsd'):
        try:
            state = json.loads((data_dir() / f'results_{name}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        res = opportunity_results.provider_snapshot(state, name, now)
        bound = [m for m, r in res.items() if any(report.external_matches(msg, r) for msg in fixtures[m])]
        teams_time = [m for m, r in res.items() if any(
            [norm_team(x) for x in (r.get('home'), r.get('away'))] == [norm_team(t) for t in (msg.get('teams') or ())]
            and time_ok(msg, chronology.parse_utc(r.get('event_date'))) for msg in fixtures[m])]
        comparable = [m for m in teams_time if m in bot]
        out[name] = dict(records=len(res), records_with_league=sum(1 for r in res.values() if r.get('league') or r.get('target_league')),
                         bound_by_report=len(bound), would_bind_on_teams_and_time=len(teams_time),
                         comparable_with_bot_score=len(comparable),
                         agree_with_bot_score=sum(1 for m in comparable if tuple(res[m]['ft']) == bot[m]),
                         last_run=state.get('last_run_utc'), stats=state.get('stats'))
    out['cause'] = 'report.external_matches requires a league on the provider record; stored provider records have none'
    return out


def relations(signals):
    rows = [b for b in signals if eligible(b)]
    by_game = collections.defaultdict(list)
    for b in rows:
        by_game[b['canonical_match_id'] or b['match_id']].append(b)
    pairs, first_type, conflicts, total_pairs = collections.Counter(), collections.Counter(), 0, 0
    for game in by_game.values():
        types = sorted({b['type'] for b in game})
        if len(types) > 1:
            for i, a in enumerate(types):
                for c in types[i + 1:]:
                    pairs[f'{a}+{c}'] += 1
            ordered = sorted(game, key=lambda b: _t(b['signal_seen_utc']))
            first_type[ordered[0]['type']] += 1
        totals = [b for b in game if b['selection'] and re.match(r'(Over|Under) ', b['selection'])]
        for i, a in enumerate(totals):
            for c in totals[i + 1:]:
                if a['type'] != c['type']:
                    total_pairs += 1
                    conflicts += a['selection'].split()[0] != c['selection'].split()[0]
    multi = {k for k, g in by_game.items() if len({b['type'] for b in g}) > 1}
    moved = [b for b in rows if b['odds'] and b.get('odds_last') and b['w'] is not None]
    move = {k: _summary([b for b in moved if (b['odds_last'] < b['odds']) == (k == 'shortened')
                         and b['odds_last'] != b['odds']]) for k in ('shortened', 'drifted')}
    betfair = [b for b in rows if b['type'] == 'BETFAIR' and b['mkt_volume'] is not None]
    volume = {k: _summary([b for b in betfair if (b['mkt_volume'] >= 10_000) == (k == '>=10K')]) for k in ('<10K', '>=10K')}
    live = [b for b in rows if b['phase'] == 'live' and b['score_at_signal'] and b['minute'] is not None and b['ft']]
    more = [sum(b['ft']) > sum(b['score_at_signal']) for b in live]
    baseline = [report.baseline_more_goals(b['minute']) for b in live]
    reinforce = {k: _summary([b for b in rows if (b['n_signals'] > 1) == (k == 'repeated')]) for k in ('single', 'repeated')}
    return dict(
        signal_types_per_game=dict(games=len(by_game), games_with_several_types=len(multi), top_pairs=pairs.most_common(8),
                                   first_type_in_multi_type_games=dict(first_type),
                                   opposite_total_directions=dict(pairs=total_pairs, opposite=conflicts),
                                   prospective=True, note='only earlier signals can inform a decision'),
        odds_move_after_signal=dict(move, prospective=False,
                                    note='odds_last is a later edit with no reliable time: future information'),
        betfair_market_volume=dict(volume, prospective=True),
        score_after_live_signal=dict(n=len(live), observed_more_goals=round(sum(more) / len(live), 4) if live else None,
                                     baseline_more_goals=round(sum(baseline) / len(live), 4) if live else None,
                                     prospective=True, note='score and minute are known at the signal'),
        repeated_signal=dict(reinforce, prospective=False, note='repetitions arrive after the first signal'))


def hypotheses(signals):
    """Discover on signals seen before SPLIT; check once on signals seen after it."""
    rows = [b for b in signals if eligible(b) and b['w'] is not None and b['odds'] and b['signal_seen_utc']]
    early = [b for b in rows if _t(b['signal_seen_utc']) < SPLIT]
    late = [b for b in rows if _t(b['signal_seen_utc']) >= SPLIT]
    key = lambda b: (b['group'], b['phase'], _band(b['odds']))
    segments = collections.defaultdict(list)
    for b in early:
        segments[key(b)].append(b)
    found = []
    for k, items in sorted(segments.items()):
        s = _summary(items)
        if s['n'] >= 30 and s['roi_ci95'][0] > -0.05 and s['roi'] > 0.05:
            found.append(dict(segment=list(k), discovery=s,
                              check=_summary([b for b in late if key(b) == k])))
    return dict(split_utc=SPLIT.isoformat(), discovery_signals=len(early), check_signals=len(late),
                segments_scanned=len(segments), rule='n >= 30, ROI > 5% and 95% lower bound > -5% in discovery',
                selected=found, status='exploratory: single check, no multiplicity correction, no prospective registration')


import re  # noqa: E402


def run(now):
    inputs = report.load_bet_inputs(now)
    options = inputs.bet_options
    bets = report.build_bets(inputs.identified, **options)
    signals = report.build_bets(inputs.identified, strict=False, **options)
    by_group = collections.defaultdict(list)
    for b in signals:
        if eligible(b):
            by_group[b['group']].append(b)
    return dict(schema='goal_roi_real_evidence_v1', confirmatory=False,
                coverage=coverage(inputs.messages, signals, bets, options, now),
                groups={g: _summary(rows) for g, rows in sorted(by_group.items())},
                result_verification=provider_binding(inputs.messages, signals, now),
                relations=relations(signals), hypotheses=hypotheses(signals),
                verdict='RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='UTC clock, e.g. 2026-11-24T18:35:00+00:00')
    args = parser.parse_args()
    print(json.dumps(run(datetime.fromisoformat(args.as_of)), ensure_ascii=False, indent=2, sort_keys=True, default=str))

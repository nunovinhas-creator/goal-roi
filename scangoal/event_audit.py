"""Reproducible read-only audit of experimental temporal episodes."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
from pathlib import Path

from . import chronology, details_hl, match_identity, opportunity_forward, opportunity_results, parser, report
from . import event_intelligence as events

VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'
INPUT_FILES = ('messages.jsonl', 'results_hl.json', 'results_bsd.json', 'details_hl.json')
DIRECTIONS = ('under_to_over', 'over_to_under', 'over_to_over', 'under_to_under')
MOVEMENTS = ('down', 'stable', 'up', 'unproven')
SETTLEMENT_MARKETS = {'match_goals': 'Asian Total', 'winner': '1X2', 'both_teams_to_score': 'Both Teams To Score',
                      'double chance': 'Double Chance', 'asian_handicap': 'Asian Handicap'}


def result_evidence(groups, signals, bindings, directory, as_of):
    """Link hash-verified journal observations and archived provider evidence."""
    raw, notes, proofs = defaultdict(list), [], {}
    for rows in groups.values():
        original = rows[0]
        initial = original.parsed
        identity = match_identity.components(initial)
        for version in rows:
            p = version.parsed
            if (not p.get('ft') or len(p['explicit_match_ids']) > 1
                    or p['explicit_match_ids'] != initial['explicit_match_ids']
                    or p['type'] != initial['type']
                    or any(a and b and a != b for a, b in zip(identity, match_identity.components(p)))):
                continue
            mid = p['match_id']
            note = p['type'] == 'FULL_TIME_NOTE'
            if not note:
                binding = bindings.get(mid)
                if (binding is None or not all(identity[1:])
                        or any(a and b and a != b for a, b in zip(binding.parts, identity))):
                    continue
            result = opportunity_results.collected(
                'telegram_note' if note else 'telegram', version.message_id, p['ft'],
                version.evidence['first_read_utc'], published=events.iso(version.published),
                exact=version.evidence['receipt'] == 'completed_page',
                kickoff=p.get('kickoff_utc') or initial.get('kickoff_utc'), fixture_id=mid,
                link=f'https://scangoal.com/match/{mid}' if mid else None,
            )
            key = (result.source, result.record_id, result.published_at, result.observed_at, result.score)
            proofs[key] = version.evidence
            if note:
                notes.append((p, result.published_at, result))
            else:
                raw[mid].append(result)

    note_inputs = [dict(e, date_utc=e['emitted_utc'], signal_seen_utc=e['available_utc']) for e in signals
                   if not e.get('exclusion')]
    for row in report.link_ft_notes(note_inputs, notes, as_of=as_of):
        raw[row.fixture_id].append(row)
    for file, source in (('results_hl.json', 'highlightly'), ('results_bsd.json', 'bsd')):
        path = directory / file
        if path.exists():
            for mid, observations in opportunity_results.provider_observations(json.loads(path.read_bytes()), source).items():
                raw[mid].extend(row for row in observations if row.available_at <= as_of)
    for mid, observations in details_hl.observations(details_hl.load_state(directory), as_of).items():
        raw[mid].extend(observations)
    return opportunity_forward.fixture_evidence(raw, bindings), proofs


def announced_kickoffs(history, clock):
    return {ko for e in history if events.seen(e) <= clock and (ko := events.kickoff(e)) is not None}


def fixture_kickoff(history, evidence, clock):
    announced = announced_kickoffs(history, clock)
    if len(announced) == 1:
        return next(iter(announced))
    if announced:
        return None
    sourced = {e.kickoff for e in evidence if e.kickoff is not None and e.available_at <= clock}
    if len(sourced) == 1:
        return next(iter(sourced))
    if sourced:
        return None
    # A live minute proves at least this much time has elapsed. No future
    # signal can contribute this bound to an earlier decision.
    bounds = [events.emitted(e) - timedelta(minutes=max(0, e['minute'] - 1))
              for e in history if events.seen(e) <= clock and e['phase'] == 'live'
              and type(e['minute']) is int and 0 <= e['minute'] <= 120]
    return min(bounds, default=None)


def resolution(history, evidence, clock):
    ko = fixture_kickoff(history, evidence, clock)
    return opportunity_results.resolve(evidence, kickoff=ko, as_of=clock), ko


def result_proofs(evidence, proofs, ko, as_of):
    return [
        dict(source=row.source, record_id=row.record_id, fixture_id=row.fixture_id, link=row.link, score=row.score,
             available_utc=events.iso(row.available_at), observed_utc=events.iso(row.observed_at),
             published_utc=events.iso(row.published_at), kickoff_utc=events.iso(row.kickoff),
             provider_id=(row.provider_record or {}).get('bsd_id'),
             journal=proofs.get((row.source, row.record_id, row.published_at, row.observed_at, row.score)))
        for row in evidence
        if row.period == 'regulation' and row.score is not None
        and opportunity_results.ft_time_valid(ko or row.kickoff, row.observed_at, row.available_at, row.published_at, as_of)
    ]


def phase_at_emission(event, known_kickoff):
    ko = events.kickoff(event) or known_kickoff
    if ko is not None:
        return 'pre_kickoff' if events.emitted(event) < ko else 'at_or_after_kickoff'
    return 'reported_live' if event['phase'] == 'live' else 'unknown'


def timely(event, available, known_kickoff):
    phase = phase_at_emission(event, known_kickoff)
    if (event['phase'] == 'pre' and phase == 'at_or_after_kickoff'
            or event['phase'] == 'live' and phase == 'pre_kickoff'):
        return 'phase_conflicts_with_kickoff'
    # Legacy page snapshots already include the five-minute allowance. The
    # existing conservative operating window is also required for this audit.
    row = dict(event, date_utc=event['emitted_utc'], signal_seen_utc=events.iso(available),
               kickoff_utc=events.iso(events.kickoff(event) or known_kickoff))
    if event['phase'] not in ('pre', 'live') or (event['phase'] == 'pre' and not row['kickoff_utc']):
        return 'unproven_decision_window'
    if event['period'] == 'first_half' and event['phase'] == 'live':
        minute = event['minute']
        if minute is None or minute >= 45:
            return 'unproven_first_half_window'
        window_end = events.emitted(event) + timedelta(minutes=45 - minute)
        if available + timedelta(minutes=chronology.DECISION_DELAY_MAX_MIN) >= window_end:
            return 'first_half_window_elapsed'
    if event['period'] == 'second_half' and event['phase'] == 'live' and (event['minute'] or 0) < 46:
        return 'unproven_second_half_window'
    return None if chronology.decidable(row) else 'signal_outside_decision_window'


def relation(earlier, later):
    same_market = earlier['market_key'] == later['market_key']
    same_period = earlier['period'] == later['period']
    same_line = earlier['line'] == later['line']
    same_direction = earlier['direction'] == later['direction']
    cross_source = earlier['source'] != later['source']
    comparable = same_market and same_period
    source_relation = 'same_source'
    if cross_source:
        if comparable and same_direction:
            source_relation = 'confirmation' if same_line else 'same_direction_different_line'
        else:
            source_relation = 'divergence' if comparable else 'different_scope'
    joint_win = None
    if comparable and not same_direction and earlier['market_key'] in {'match_goals', 'corners'}:
        over = earlier if earlier['direction'] == 'over' else later
        under = later if later['direction'] == 'under' else earlier
        if all(parser.asian_line(e['line']) is not None for e in (earlier, later)):
            first_winning_integer = Decimal(over['line']).to_integral_value(rounding=ROUND_FLOOR) + 1
            joint_win = first_winning_integer < Decimal(under['line'])
    return dict(same_market=same_market, same_period=same_period, same_line=same_line,
                cross_source=cross_source, source_relation=source_relation, joint_win_possible=joint_win,
                exact_repetition=comparable and same_line and same_direction,
                same_direction=same_direction)


def payoff(event, score):
    if score is None or event['period'] != 'regulation':
        return None
    market = SETTLEMENT_MARKETS.get(event['market_key'])
    selection = event['selection']
    if event['direction'] and event['line'] is not None:
        selection = f"{event['direction']} {event['line']}"
    if market == 'Both Teams To Score' and event['source'] == 'Polymarket':
        selection = selection.removeprefix('BTTS: ')
    if market == 'Asian Handicap':
        if event['phase'] == 'live':
            return None
        handicap = (re_match_handicap(selection) if event['source'] == 'Polymarket' else None)
        if handicap:
            team, line = handicap
            selection = f"{event['teams'][int(team) - 1]} ({line})"
    return parser.settle(market, selection, score, event['teams'])


def re_match_handicap(selection):
    # This is the explicit Polymarket spelling accepted by parser.special_quote_scope.
    if selection.upper().startswith(('AH1(', 'AH2(')) and selection.endswith(')'):
        return selection[2], selection[4:-1]
    return None


def quote_reference(quotes, anchor, timing):
    def qualifies(quote):
        if timing == 'subsequent':
            return emitted_after(quote, anchor) and events.seen(quote) > events.seen(anchor)
        return emitted_after(anchor, quote) and ((events.seen(quote) > events.seen(anchor)) == (timing == 'late_prior'))

    candidates = [q for q in quotes if qualifies(q)]
    if not candidates:
        return None, 'no_comparable_quote'
    order = ((lambda q: (events.seen(q), events.emitted(q))) if timing != 'prior' else
             (lambda q: (events.emitted(q), events.seen(q))))
    timestamp = (max if timing == 'prior' else min)(map(order, candidates))
    chosen = [q for q in candidates if order(q) == timestamp]
    if any(q['odds'] != chosen[0]['odds'] for q in chosen[1:]):
        return None, 'ambiguous_quote_at_same_time'
    return min(chosen, key=lambda q: q['message_id']), None


def emitted_after(later, earlier):
    return events.emitted(later) > events.emitted(earlier)


def outcome_for(inputs, row, final, known_before, as_of):
    anchor = inputs[-1]
    first_payoff = payoff(inputs[0], final.score) if len(inputs) > 1 else None
    unit_outcome = payoff(anchor, final.score)
    if final.conflict:
        status, reason = 'indeterminate', 'conflicting_ft'
    elif final.score is None:
        limit = chronology.parse_utc(row['observed_utc']) + timedelta(days=opportunity_results.SETTLEMENT_WINDOW_DAYS)
        status = 'pending' if as_of < limit else 'indeterminate'
        reason = 'ft_not_proven'
    elif unit_outcome is None:
        status, reason = 'indeterminate', 'market_not_settled_by_ft'
    else:
        status, reason = 'confirmed', None

    profit = None
    price = anchor['odds']
    if (unit_outcome is not None and not row['exclusion_reasons']
            and price is not None and price[0] == price[1] and Decimal(price[0]) >= Decimal(str(parser.MIN_ODDS))):
        fraction = Decimal(str(unit_outcome))
        profit = float(fraction * (Decimal(price[0]) - 1) if fraction > 0 else fraction)
    return dict(status=status, reason=reason, ft=final.score, last_payoff=unit_outcome,
                first_payoff=first_payoff, theoretical_profit_units=profit,
                ft_available_utc=events.iso(final.available_at),
                ft_known_by_decision=known_before.first_available_at is not None)


def build(directory, as_of, *, volume_min=10_000):
    """Build all observations, chronological episodes, and separate outcome labels."""
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('as_of must be a timezone-aware datetime')
    if type(volume_min) is not int or volume_min <= 0:
        raise ValueError('volume_min must be a positive integer USD threshold')
    directory = Path(directory)
    if not (directory / 'messages.jsonl').is_file():
        raise FileNotFoundError(directory / 'messages.jsonl')
    def digest_inputs():
        return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                for name in INPUT_FILES if (directory / name).is_file()}

    manifest = digest_inputs()
    versions, version_exclusions = events.read_versions(directory / 'messages.jsonl', as_of)
    signals, excluded, edits, bindings, groups = events.reconstruct(versions, as_of, burst_size=report.BURST)
    results, proofs = result_evidence(groups, signals, bindings, directory, as_of)
    history, price_history = defaultdict(list), defaultdict(list)
    for event in signals:
        if event.get('exclusion'):
            continue
        history[event['fixture_id']].append(event)
        key = events.selection_key(event)
        if key is not None and event['odds'] is not None:
            price_history[key].append(event)

    episodes, outcomes, fixture_results = [], {}, {}
    incompleteness = Counter()

    for fixture, fixture_events in sorted(history.items()):
        evidence = results.get(fixture, [])
        final, final_kickoff = resolution(fixture_events, evidence, as_of)
        fixture_results[fixture] = result_proofs(evidence, proofs, final_kickoff, as_of)
        decision_evidence = {}
        totals = []

        def add(kind, pattern, inputs, features, *, later_quotes=None):
            anchor = inputs[-1]
            available = max(map(events.seen, inputs))
            if available not in decision_evidence:
                # The source's kickoff/clock bound must itself be available at the decision.
                prior, _ = resolution(fixture_events, evidence, available)
                # Result evidence may exclude a decision. Only original alerts
                # contribute kickoff context to its features and window.
                ko = next(iter(announced_kickoffs(fixture_events, available)), None)
                decision_evidence[available] = prior, ko
            prior, ko = decision_evidence[available]
            reasons = []
            if len(inputs) == 2 and (reason := timely(inputs[0], events.seen(inputs[0]), ko)):
                reasons.append('first_' + reason)
            if reason := timely(anchor, available, ko):
                reasons.append('last_' + reason)
            if prior.first_available_at is not None:
                reasons.append('ft_already_known')
            # Grouping first observed identity is stable. A later collision can
            # block decisions from that point forward, including across aliases.
            if any(binding.collision is not None and binding.collision <= available
                   for binding in fixture_bindings):
                reasons.append('fixture_identifier_collision')
            features = dict(features, phases=[phase_at_emission(e, ko) for e in inputs])
            ids = [e['message_id'] for e in inputs]
            episode_id = kind + ':' + ':'.join(map(str, ids))
            row = dict(episode_id=episode_id, kind=kind, pattern=pattern, fixture_id=fixture,
                       message_ids=ids, observed_utc=events.iso(available),
                       decision_utc=events.iso(available) if not reasons else None, exclusion_reasons=reasons,
                       features=features)
            label = outcome_for(inputs, row, final, prior, as_of)
            if later_quotes is not None:
                label.update(later_quotes)
            episodes.append(row)
            outcomes[episode_id] = label

        fixture_bindings = [binding for binding in bindings.values() if binding.canonical == fixture]
        for anchor in sorted(fixture_events, key=lambda e: (events.emitted(e), events.seen(e), e['message_id'])):
            if anchor['direction'] is not None:
                if anchor['line'] is None or anchor['market_key'] is None or anchor['period'] is None:
                    incompleteness['total_without_exact_scope'] += 1
                else:
                    for prior in totals:
                        if not emitted_after(anchor, prior):
                            incompleteness['totals_at_equal_emission_time'] += 1
                            continue
                        features = dict(relation(prior, anchor),
                                        emission_gap_seconds=(events.emitted(anchor) - events.emitted(prior)).total_seconds(),
                                        receipt_gap_seconds=(events.seen(anchor) - events.seen(prior)).total_seconds())
                        add('sequence', f"{prior['direction']}_to_{anchor['direction']}", (prior, anchor), features)
                    totals.append(anchor)

            high_volume = (anchor['source'] in events.VOLUME_SOURCES and anchor['volume_usd'] is not None
                           and anchor['volume_usd'] >= volume_min)
            if anchor['odds'] is None and not high_volume:
                continue
            key = events.selection_key(anchor)
            if key is None or anchor['odds'] is None:
                reason = 'price_missing' if anchor['odds'] is None else 'selection_scope_unproven'
                before = events.movement(None, None, reason)
                later_quotes = {name: dict(before) for name in ('subsequent_movement', 'late_prior_movement')}
            else:
                quotes = price_history[key]
                older, reason = quote_reference(quotes, anchor, 'prior')
                before = events.movement(older, anchor, reason)
                newer, reason = quote_reference(quotes, anchor, 'subsequent')
                after = events.movement(anchor, newer, reason)
                lagged, reason = quote_reference(quotes, anchor, 'late_prior')
                late_prior = events.movement(lagged, anchor, reason)
                later_quotes = dict(subsequent_movement=after, late_prior_movement=late_prior)
                for move in later_quotes.values():
                    known = chronology.parse_utc(move.get('known_utc'))
                    if known is not None:
                        at_quote, _ = resolution(fixture_events, evidence, known)
                        move['ft_known_by_quote'] = at_quote.first_available_at is not None
            add('quote', before['status'], (anchor,), dict(high_volume=high_volume, prior_movement=before),
                later_quotes=later_quotes)

    input_counts = Counter(e['exclusion'] for e in signals if e.get('exclusion'))
    input_counts.update(e['reason'] for e in excluded)
    input_counts.update(version_exclusions)
    if manifest != digest_inputs():
        raise ValueError('audit inputs changed during replay; use a stable snapshot')
    return dict(schema='goal_roi_temporal_audit_v1', experimental=True, as_of_utc=events.iso(as_of),
                volume_min_usd=volume_min, input_sha256=manifest, versions_visible=len(versions),
                events=signals, excluded_first_versions=excluded, edits=edits,
                input_exclusions=dict(input_counts), episode_incompleteness=dict(incompleteness),
                episodes=episodes, outcomes=outcomes, fixture_results=fixture_results, verdict=VERDICT)


def stats(rows, outcomes, *, economic=True):
    labels = [outcomes[row['episode_id']] for row in rows]
    at_decision = [label for row, label in zip(rows, labels) if row['decision_utc'] is not None]
    payoffs = [label['last_payoff'] for label in at_decision if label['last_payoff'] is not None]
    hit, loss = sum(p > 0 for p in payoffs), sum(p < 0 for p in payoffs)
    profit = [label['theoretical_profit_units'] for label in at_decision
              if label['theoretical_profit_units'] is not None]
    status = Counter(label['status'] for label in labels)
    outcome_reasons = Counter(label['reason'] for label in labels if label['reason'] is not None)
    return dict(games=len({row['fixture_id'] for row in rows}), occurrences=len(rows),
                decisions=len(at_decision),
                decision_games=len({row['fixture_id'] for row in rows if row['decision_utc'] is not None}),
                confirmed=status['confirmed'], pending=status['pending'], indeterminate=status['indeterminate'],
                decision_status=dict(sorted(Counter(label['status'] for label in at_decision).items())),
                ft_confirmed=sum(label['ft'] is not None for label in labels),
                outcome_reasons=dict(sorted(outcome_reasons.items())),
                wins=hit, losses=loss, pushes=len(payoffs) - hit - loss,
                hit_rate=round(hit / (hit + loss), 6) if hit + loss else None,
                roi_priced_n=len(profit) if economic else None,
                theoretical_roi=round(sum(profit) / len(profit), 6) if economic and profit else None,
                exclusions=dict(sorted(Counter(reason for row in rows for reason in row['exclusion_reasons']).items())))


def breakdown(rows, outcomes, by, *, economic=True):
    groups = defaultdict(list)
    for row in rows:
        groups[by(row)].append(row)
    return {key: stats(group, outcomes, economic=economic) for key, group in sorted(groups.items())}


def summarize(audit):
    labels = audit['outcomes']
    by_id = {event['message_id']: event for event in audit['events']}
    sequences = [row for row in audit['episodes'] if row['kind'] == 'sequence']
    quotes = [row for row in audit['episodes'] if row['kind'] == 'quote']
    volume = [row for row in quotes if row['features']['high_volume']]

    def dimensions(rows):
        return {name: breakdown(rows, labels, lambda row: ' → '.join(
            by_id[mid][key] or 'unknown' for mid in row['message_ids']))
                for name, key in (('market', 'market_key'), ('reported_market', 'market'),
                                  ('source', 'source'), ('period', 'period'))}

    def patterns(rows, names):
        return {name: dict(stats(group, labels), distributions=dimensions(group))
                for name in names if (group := [row for row in rows if row['pattern'] == name])}

    flips = [row for row in sequences if not row['features']['same_direction']]
    hints = []
    for row in flips:
        label = labels[row['episode_id']]
        first, last = (by_id[mid] for mid in row['message_ids'])
        over = first if first['direction'] == 'over' else last
        if payoff(over, label['ft']) is not None and payoff(over, label['ft']) > 0:
            hints.append(row['episode_id'])
    return dict(
        reconstruction=stats(sequences, labels), sequences=patterns(sequences, DIRECTIONS),
        sequence_scope=breakdown(sequences, labels, lambda row: '/'.join(
            ('same_' if row['features']['same_' + key] else 'different_') + key for key in ('market', 'period', 'line'))),
        sequence_source=breakdown(sequences, labels, lambda row: row['features']['source_relation']),
        sequence_phase=breakdown(sequences, labels, lambda row: ' → '.join(row['features']['phases'])),
        exact_repetition=stats([row for row in sequences if row['features']['exact_repetition']], labels),
        inversions=stats(flips, labels),
        same_contract_inversions=stats([row for row in flips if all(row['features']['same_' + key]
                                                                    for key in ('market', 'period', 'line'))], labels),
        quote_movements=patterns(quotes, MOVEMENTS), volume=stats(volume, labels),
        volume_prior=patterns(volume, MOVEMENTS),
        volume_subsequent=breakdown(volume, labels, lambda row: labels[row['episode_id']]['subsequent_movement']['status'],
                                    economic=False),
        volume_late_prior=breakdown(volume, labels, lambda row: labels[row['episode_id']]['late_prior_movement']['status'],
                                    economic=False),
        inversion_with_winning_over_episode_ids=hints,
    )


def llm_context(audit, *, limit=30):
    """Export bounded decision-only inputs, with no outcomes or later revisions."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be from 1 to 100')
    rows = sorted((row for row in audit['episodes'] if row['decision_utc'] is not None),
                  key=lambda row: (chronology.parse_utc(row['decision_utc']), row['episode_id']), reverse=True)
    by_id = {e['message_id']: e for e in audit['events']}
    keys = ('message_id', 'text_sha256', 'record_sha256', 'emitted_utc', 'available_utc', 'source',
            'signal_type', 'market_key', 'period', 'selection', 'line', 'odds', 'volume_usd', 'share_pct',
            'phase', 'minute', 'score', 'signal_kind')
    items = []
    for row in rows[:limit]:
        ids = row['message_ids']
        prior = row['features'].get('prior_movement', {}).get('from_message_id')
        if prior is not None:
            ids = [prior, *ids]
        snapshots = [{key: value[:160] if isinstance(value, str) else value for key in keys
                      if (value := by_id[mid][key]) is not None} for mid in ids]
        items.append(dict(episode_id=row['episode_id'], kind=row['kind'], pattern=row['pattern'],
                          fixture_id=row['fixture_id'][:160], decision_utc=row['decision_utc'],
                          features=row['features'], inputs=snapshots))
    return dict(schema='goal_roi_experimental_temporal_context_v1', experimental=True, as_of_utc=audit['as_of_utc'], episodes=items)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--as-of', required=True, help='ISO 8601 with an explicit timezone')
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--volume-min-usd', type=int, default=10_000)
    cli.add_argument('--format', choices=('audit', 'summary', 'llm-context'), default='summary')
    cli.add_argument('--limit', type=int, default=30, help='episode limit for llm-context (1–100)')
    args = cli.parse_args()
    try:
        audit = build(args.data_dir, chronology.parse_utc(args.as_of), volume_min=args.volume_min_usd)
        if args.format == 'summary':
            keys = ('schema', 'experimental', 'as_of_utc', 'volume_min_usd', 'input_sha256', 'versions_visible',
                    'input_exclusions', 'episode_incompleteness', 'verdict')
            output = dict({key: audit[key] for key in keys}, **summarize(audit),
                          original_events=len(audit['events']), edited_versions=len(audit['edits']))
        elif args.format == 'llm-context':
            output = llm_context(audit, limit=args.limit)
        else:
            output = audit
    except (OSError, ValueError) as exc:
        cli.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()

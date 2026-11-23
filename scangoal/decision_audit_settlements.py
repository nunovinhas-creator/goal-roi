"""Read-only outcome reconciliation for the decision audit."""

from collections import Counter
from datetime import datetime
import math

from . import brain, chronology, opportunity_forward, opportunity_results


STATES = ('confirmed', 'pending', 'imputed_loss', 'quarantined',
          'ft_without_contract_settlement', 'unknown')
ODDS_FACTOR = opportunity_forward.ODDS_FACTOR


def _iso(stamp):
    return chronology.iso(stamp, timespec='auto')


def _zoned(stamp):
    return isinstance(stamp, datetime) and stamp.utcoffset() is not None


def _reconciliation(ft, conflict, available):
    return {'ft': list(ft) if ft is not None else None,
            'quarantined': conflict, 'result_available_utc': _iso(available)}


def _pr55_result(bet, evidence, as_of, deadline):
    kickoff = chronology.kickoff_time(bet)
    if kickoff is None:
        return {'result_issue': 'missing_kickoff'}, None
    if evidence is not None and not isinstance(evidence, (tuple, list, dict)):
        return {'result_issue': 'malformed_result_evidence'}, None
    primary, late = opportunity_results.settlement(evidence, kickoff=kickoff, as_of=as_of, deadline=deadline)
    row = {'ft': primary.score,
           'w': float(1 if sum(primary.score) >= 3 else -1) if primary.score is not None else None,
           'result_available_utc': _iso(primary.available_at),
           'result_known_utc': _iso(primary.observed_at),
           'match_result_first_seen_utc': _iso(primary.first_available_at),
           'result_quarantined': primary.conflict,
           'result_quarantined_utc': _iso(primary.available_at) if primary.conflict else None,
           'result_known_source': 'versioned_ft' if primary.score is not None else None}
    return row, _reconciliation(late.score, late.conflict, late.available_at) if late is not None else None


def _versioned_result(bet, as_of, deadline):
    # Reuse the research contract rules, including the exception for LIVE handicaps.
    if not isinstance(bet['_ft_observations'], (tuple, list)):
        return {'result_issue': 'malformed_result_evidence'}, None
    market = bet.get('market')
    bet = dict(bet, market=market if isinstance(market, str) else None, selection=bet.get('selection'))
    primary = opportunity_results.settle_research_bet(bet, min(as_of, deadline))
    if as_of <= deadline:
        return primary, None
    current = opportunity_results.settle_research_bet(bet, as_of)
    late = current.get('result_reconciliation')
    if late is not None:
        return primary, _reconciliation(late['ft'], late['conflict'], chronology.parse_utc(late['available_utc']))
    if (current['ft'], current['result_quarantined']) != (primary['ft'], primary['result_quarantined']):
        stamp = chronology.parse_utc(current['result_available_utc'])
        return primary, _reconciliation(current['ft'], current['result_quarantined'], stamp)
    return primary, None


def _legacy_result(bet, as_of, cutoff):
    known, proof = chronology.result_check(bet)
    quarantine = bet.get('result_quarantined') is True
    conflict_at = chronology.parse_utc(bet.get('result_quarantined_utc'))
    if quarantine:
        if conflict_at is None:
            return {'result_issue': 'unproven_quarantine_time'}, None
        if conflict_at <= cutoff:
            return bet, None
        if conflict_at <= as_of:
            return {}, _reconciliation(None, True, conflict_at)
        return {}, None
    if known is not None and known > cutoff:
        late = _reconciliation(opportunity_results.score_pair(bet.get('ft')), False, known) if known <= as_of else None
        return {}, late
    if (bet.get('ft') is not None or bet.get('w') is not None) and proof != 'verified':
        return {'result_issue': 'unproven_result_time'}, None
    return bet, None


def outcome(bet, as_of, *, deadline, pr55=False, evidence=None):
    """Classify one admitted game at the cutoff and charge every unresolved game one adverse unit.

    A confirmed contract may win, lose, or be returned. A known FT with no contract
    payout remains separate even after the deadline. An empty result is pending
    until the deadline, then a timeout loss. Unusable evidence is unknown.
    """
    clock_valid = _zoned(as_of) and _zoned(deadline)
    cutoff = min(as_of, deadline) if clock_valid else None
    expired = clock_valid and as_of >= deadline
    if not clock_valid:
        row, late = {'result_issue': 'missing_settlement_clock'}, None
    elif pr55:
        row, late = _pr55_result(bet, evidence, as_of, deadline)
    elif '_ft_observations' in bet:
        row, late = _versioned_result(bet, as_of, deadline)
    else:
        row, late = _legacy_result(bet, as_of, cutoff)

    ft = opportunity_results.score_pair(row.get('ft'))
    first_seen = chronology.parse_utc(row.get('match_result_first_seen_utc'))
    result_timing = {'date_utc': bet.get('date_utc'), 'kickoff_utc': bet.get('kickoff_utc')}
    result_timing.update({key: row[key] for key in ('result_known_utc', 'result_available_utc') if key in row})
    known, proof = chronology.result_check(result_timing)
    payout = row.get('w')
    profit = None
    issue = row.get('result_issue')
    if row.get('result_quarantined'):
        state = 'quarantined'
    elif issue:
        state = 'unknown'
    elif row.get('ft') is not None and ft is None:
        state, issue = 'unknown', 'invalid_full_time_score'
    elif ft is not None and payout is None:
        if proof == 'verified':
            state = 'ft_without_contract_settlement'
        else:
            state, issue = 'unknown', 'unproven_result_time'
    elif payout is not None:
        profit = brain.bet_profit({'odds': bet.get('odds'), 'w': payout})
        if profit is not None and proof == 'verified' and first_seen is not None and first_seen <= known <= cutoff:
            state = 'confirmed'
        else:
            state, issue, profit = 'unknown', 'unproven_contract_settlement', None
    else:
        state = 'imputed_loss' if expired else 'pending'

    return {'state': state, 'issue': issue, 'ft': list(ft) if ft is not None else None,
            'deadline_utc': _iso(deadline) if _zoned(deadline) else None, 'deadline_passed': expired,
            'first_result_seen_utc': _iso(first_seen), 'result_available_utc': _iso(known),
            'result_observed_utc': row.get('result_known_utc'),
            'result_source': row.get('result_known_source'),
            'quarantine_utc': row.get('result_quarantined_utc') if state == 'quarantined' else None,
            'late_reconciliation': late,
            'confirmed_payoff': payout if state == 'confirmed' else None,
            'adverse_profit_units': profit if state == 'confirmed' else -1.0}


def summarize(outcomes):
    """Reconcile disjoint outcome counts and use all admitted games as the denominator."""
    rows = list(outcomes)
    counts = Counter(row['state'] for row in rows)
    paid = [row['confirmed_payoff'] for row in rows if row['state'] == 'confirmed']
    admitted = len(rows)
    profit = math.fsum(row['adverse_profit_units'] for row in rows)
    return {'admitted': admitted, 'counts': {state: counts[state] for state in STATES},
            'confirmed_wins': sum(payoff > 0 for payoff in paid),
            'confirmed_losses': sum(payoff < 0 for payoff in paid),
            'confirmed_voids': sum(payoff == 0 for payoff in paid),
            'adverse': {'odds_factor': ODDS_FACTOR, 'unresolved_profit_units_per_game': -1.0,
                        'stake_units': admitted, 'profit_units': round(profit, 4),
                        'roi': round(profit / admitted, 4) if admitted else None}}

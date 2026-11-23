"""Offline comparison of current research decisions and temporal diagnostics."""
from collections import Counter, defaultdict
from datetime import timedelta
import json

from . import brain, chronology, insights, match_identity, opportunity_forward, report, strategy_board
from .decision_audit_provenance import Registration, registration, signal_versions
from .decision_audit_settlements import outcome, summarize
from .eligibility import eligible, exclusion_reasons, game_identity
from .store import data_dir


def _iso(stamp):
    return chronology.iso(stamp, timespec='auto')


def _registration_metadata(reg):
    return {'declared_utc': _iso(reg.declared), 'proof_utc': _iso(reg.proven_at),
            'effective_utc': _iso(reg.effective), 'proof': reg.proof, 'flags': list(reg.flags)}


def _window(bet, version, as_of, *, pr55=False):
    sent = chronology.signal_time(bet)
    decision = chronology.decision_time(bet)
    kickoff = chronology.kickoff_time(bet)
    published = chronology.parse_utc((version or {}).get('published_utc'))
    receipt = chronology.parse_utc((version or {}).get('first_read_utc'))
    available = None
    if decision is not None and receipt is not None and published is not None:
        margin = 0 if version['receipt_quality'] == 'completed_page' else chronology.OBSERVATION_MARGIN_MIN
        available = max(decision, published, receipt + timedelta(minutes=margin))
    if pr55:
        # A fixed part of the frozen protocol even for completed-page receipts.
        available = receipt + timedelta(minutes=chronology.OBSERVATION_MARGIN_MIN) if receipt is not None else None
        latest = receipt + opportunity_forward.MIN_LEAD if receipt is not None else None
    else:
        latest = brain.latest_decision(bet)

    phase = bet.get('phase')
    minute = bet.get('minute')
    boundary = None
    flags = []
    if (version is None or version.get('text_sha256_matches_journal') is not True
            or receipt != chronology.parse_utc(bet.get('signal_seen_utc'))):
        flags.append('signal_version_unverified')
    if available is None or latest is None or available > latest:
        flags.append('signal_availability_unproven')
    if latest is not None and latest > as_of:
        flags.append('decision_window_after_cutoff')
    if phase == 'pre':
        if kickoff is None:
            flags.append('pre_kickoff_unproven')
        elif latest is not None and latest >= kickoff:
            flags.append('decision_window_reaches_kickoff')
    elif phase == 'live':
        first_half = bet.get('period') == 'first_half' or str(bet.get('market')).lower().startswith('1st half')
        boundary = 45 if first_half else 90
        if type(minute) is not int or not 0 <= minute <= 120:
            flags.append('live_minute_unproven')
        elif minute >= boundary - 1 or (latest is not None and sent is not None
                                        and latest - sent >= timedelta(minutes=boundary - minute)):
            flags.append('live_window_may_reach_first_half_end' if first_half
                         else 'live_window_may_reach_regulation_end')
    else:
        flags.append('phase_unproven')
    return {'phase': phase, 'live_minute': minute, 'live_boundary_minute': boundary, 'kickoff_utc': _iso(kickoff),
            'available_from_utc': _iso(available), 'decision_by_utc': _iso(latest)}, flags


def _attempt(bet, version, reg, as_of, *, pr55=False):
    window, flags = _window(bet, version, as_of, pr55=pr55)
    sent, seen = chronology.signal_time(bet), chronology.parse_utc(bet.get('signal_seen_utc'))
    if reg.effective is None:
        flags.append('registration_unproven')
    elif sent is not None and seen is not None and min(sent, seen) <= reg.effective:
        flags.append('signal_not_strictly_after_registration')
    if not pr55:
        flags.extend(exclusion_reasons(bet))
        if game_identity(bet) is None:
            flags.append('fixture_unproven')
        if not chronology.decidable(bet):
            flags.append('outside_current_time_window')
        if brain.latest_decision(bet) is not None and not _before_first_result(bet, as_of):
            flags.append('decision_window_reaches_known_result')
    return {'message_id': bet['message_id'], 'signal_version': version, 'time_window': window,
            'market': bet.get('market'), 'selection': bet.get('selection'), 'period': bet.get('period'),
            'odds_advertised': bet.get('odds'), 'flags': flags}


def _before_first_result(bet, as_of):
    first = chronology.parse_utc(bet.get('match_result_first_seen_utc'))
    latest = brain.latest_decision(bet)
    return latest is not None and (first is None or first > as_of or latest < first)


def _current_admitted(bet, reg, as_of):
    """Mirror economic_summary's admission for its per-fixture comparison."""
    return (brain.operational_signal(bet, reg)
            and _before_first_result(bet, as_of) and brain.latest_decision(bet) <= as_of)


def _matching(rows, hypothesis, *, as_of_conditions=False):
    conditions = {tuple(item) for item in hypothesis['conditions']}
    multi = set() if as_of_conditions else brain.multi_matches(rows)
    earlier = None if as_of_conditions else brain.earlier_index(rows)
    return [bet for bet in rows if insights.family(bet) == hypothesis['family']
            and conditions <= set(brain.atoms(dict(bet), multi, earlier))]


def _ordered(bets):
    return sorted(bets, key=lambda b: (chronology.decision_time(b) or match_identity.UNPROVEN,
                                       chronology.signal_time(b) or match_identity.UNPROVEN,
                                       str(b.get('key')), b['message_id']))


def _result(bet, as_of, *, pr55=False, evidence=None):
    stamp = chronology.kickoff_time(bet) if pr55 else chronology.decision_time(bet)
    delay = opportunity_forward.MAX_UNRESOLVED if pr55 else timedelta(days=brain.OPEN_TIMEOUT_DAYS)
    return {'message_id': bet['message_id'],
            'outcome': outcome(bet, as_of, deadline=stamp + delay if stamp is not None else None,
                               pr55=pr55, evidence=evidence)}


def _counts(decisions):
    changes = Counter()
    for row in decisions:
        current, proposed = row['current'], row['proposed']
        if current is None and proposed is not None:
            changes['proposed_only'] += 1
        elif current is not None and proposed is None:
            changes['current_only'] += 1
        elif current is not None and proposed is not None:
            key = 'same_signal' if current['message_id'] == proposed['message_id'] else 'different_signal'
            changes[key] += 1
    return {key: changes[key] for key in ('same_signal', 'different_signal', 'current_only', 'proposed_only')}


def _cohort(hid, reg, original, decisions, *, scope):
    reasons = Counter(flag for row in decisions for flag in row['difference_reasons'])
    timing = Counter(flag for row in decisions for flag in {flag for attempt in row['signals'] for flag in attempt['flags']})
    return {'hypothesis_id': hid, 'scope': scope, 'registration': _registration_metadata(reg),
            'current': {'reported_economics': original,
                        'reconciled': summarize(row['current']['outcome'] for row in decisions if row['current'] is not None)},
            'proposed': summarize(row['proposed']['outcome'] for row in decisions if row['proposed'] is not None),
            'comparison': {'fixtures_reviewed': len(decisions), 'admission': _counts(decisions),
                           'fixtures_with_timing_flags': dict(sorted(timing.items())),
                           'fixtures_with_difference_reasons': dict(sorted(reasons.items()))},
            'decisions': decisions}


def compare_hypothesis(hid, hypothesis, reg, current_rows, raw_rows, versions, as_of, *, frozen=False):
    """Review matching quotes at the cutoff and keep both one-unit commitments visible."""
    scope = 'frozen_pr76' if frozen else 'registry_hypothesis'
    if not hypothesis or hypothesis.get('expect') == 'abaixo':
        return _cohort(hid, reg, None, [], scope=scope)
    current_cutoff = reg.effective if frozen else reg.declared
    has_current_economics = current_cutoff is not None and current_cutoff <= as_of and (
        hypothesis.get('source') != 'llm' or brain.research_conditions(hypothesis))
    current_pool = _matching([bet for bet in current_rows if eligible(bet)], hypothesis)
    if hypothesis.get('source') != 'gerador':
        current_pool = [bet for bet in current_pool if current_cutoff is not None
                        and (chronology.signal_time(bet) or current_cutoff) > current_cutoff]

    original = brain.economic_summary(current_pool, current_cutoff, as_of) if has_current_economics else None
    committed = {game_identity(bet): bet for bet in brain.committed_fixtures(current_pool, as_of)}
    unknown_conditions = {condition[0] for condition in hypothesis['conditions']} - brain.RESEARCH_DIMENSIONS
    raw_pool = _matching(raw_rows, hypothesis, as_of_conditions=not unknown_conditions)
    by_fixture = defaultdict(list)
    for bet in raw_pool:
        # Keep unverifiable identities separate and traceable by message, never as a pooled game.
        fixture = game_identity(bet)
        by_fixture[(fixture, None if fixture else bet['message_id'])].append(bet)
    for fixture in committed:
        by_fixture.setdefault((fixture, None), [])

    decisions = []
    for (fixture, unbound), bets in by_fixture.items():
        first = committed.get(fixture)
        current = first if has_current_economics and first is not None and _current_admitted(first, current_cutoff, as_of) else None
        attempts, candidate = [], None
        for bet in _ordered(bets):
            attempt = _attempt(bet, versions.get(bet['message_id']), reg, as_of)
            if unknown_conditions:
                attempt['flags'].append('condition_not_proven_at_decision')
            attempts.append(attempt)
            if candidate is None and not attempt['flags']:
                candidate = bet
        if first is not None and all(a['message_id'] != first['message_id'] for a in attempts):
            attempt = _attempt(first, versions.get(first['message_id']), reg, as_of)
            attempt['flags'].append('matches_only_aggregated_signals')
            attempts.append(attempt)

        reasons = set()
        if candidate is not None and any('signal_not_strictly_after_registration' in a['flags'] for a in attempts):
            reasons.add('earlier_signal_then_eligible_signal')
        current_id = current['message_id'] if current is not None else None
        proposed_id = candidate['message_id'] if candidate is not None else None
        if proposed_id != current_id:
            if first is not None:
                reasons.update(next(a['flags'] for a in attempts if a['message_id'] == first['message_id']))
            if candidate is not None:
                reasons.add('first_eligible_signal_differs_from_current_commitment')
        decisions.append({'fixture_id': fixture, 'unbound_message_id': unbound, 'signals': attempts,
                          'current_commitment_message_id': first['message_id'] if first is not None else None,
                          'current': _result(current, as_of) if current is not None else None,
                          'proposed': _result(candidate, as_of) if candidate is not None else None,
                          'difference_reasons': sorted(reasons)})
    decisions.sort(key=lambda row: (row['fixture_id'] or '', row['unbound_message_id'] or 0))
    return _cohort(hid, reg, original, decisions, scope=scope)


def _pr55(inputs, versions, as_of, protocol, activation):
    born, activated, cutoff, _ = opportunity_forward.evaluation_window(protocol, activation, as_of)
    proof = {'kind': 'frozen_pr55_protocol_and_activation', 'protocol_sha256': opportunity_forward.PROTOCOL_SHA256,
             'merge_utc': opportunity_forward.EXPECTED_BORN, 'activation_utc': _iso(activated),
             'activation_source': protocol['activation_source']}
    reg = Registration(born, activated, cutoff, proof=proof,
                       flags=() if cutoff is not None else ('frozen_window_not_active_at_cutoff',))
    if cutoff is None:
        return _cohort(protocol['id'], reg, None, [], scope='frozen_pr55')
    current, _, bindings, _ = opportunity_forward.select_signals(inputs.messages, cutoff, as_of)
    evidence = opportunity_forward.fixture_evidence(inputs.observations, bindings)
    original, _ = opportunity_forward.settled_statistics(current, evidence, as_of)
    decisions = []
    # Preserve the frozen rule's exact chosen quote and window. The second view adds
    # provenance and outcome checks without selecting an alternative PR #55 quote.
    for fixture, (_, signal) in sorted(current.items()):
        bet = dict(signal, signal_seen_utc=signal['collected_at_utc'])
        attempt = _attempt(bet, versions.get(bet['message_id']), reg, as_of, pr55=True)
        result = _result(bet, as_of, pr55=True, evidence=evidence.get(fixture))
        decisions.append({'fixture_id': fixture, 'unbound_message_id': None, 'signals': [attempt],
                          'current_commitment_message_id': bet['message_id'], 'current': result,
                          'proposed': result if not attempt['flags'] else None, 'difference_reasons': attempt['flags']})
    return _cohort(protocol['id'], reg, original, decisions, scope='frozen_pr55')


def build(as_of, *, root, hypotheses=(), registration_ref='HEAD', summary=False):
    inputs = report.load_bet_inputs(as_of)
    versions = signal_versions(data_dir() / 'messages.jsonl', as_of)
    current = chronology.as_of_view(inputs.research(), as_of)
    individual = chronology.as_of_view(inputs.research(per_message=True), as_of)
    shortlist = strategy_board.load_shortlist(root / 'audits/strategy-lab/shortlist-v1.json')
    start = strategy_board.shortlist_evidence_start(shortlist)
    declared = chronology.parse_utc(shortlist['registered_utc'])
    proven = chronology.parse_utc(strategy_board.SHORTLIST_PROOF['created_at_utc'])
    cohorts = []
    if start is not None and start <= as_of:
        reg = Registration(declared, proven, start, proof=strategy_board.SHORTLIST_PROOF)
        for entry in shortlist['entries']:
            if entry.get('conditions'):
                cohorts.append(compare_hypothesis(entry['id'], dict(entry, source='shortlist'), reg,
                                                  current, individual, versions, as_of, frozen=True))
    protocol = json.loads((root / 'audits/opportunity-lab/polymarket-over25-v1.json').read_text(encoding='utf-8'))
    state_path = data_dir() / 'notify_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    activation = ((state.get('migrations') or {}).get('polymarket_over25_v1') or {}).get('at_utc')
    cohorts.append(_pr55(inputs, versions, as_of, protocol, activation))
    for hid in dict.fromkeys(hypotheses):
        reg = registration(root, hid, as_of, ref=registration_ref)
        cohorts.append(compare_hypothesis(hid, reg.entry, reg, current, individual, versions, as_of))
    if summary:
        for cohort in cohorts:
            del cohort['decisions']
    return {'schema': 'goal_roi_decision_audit_v1', 'as_of_utc': _iso(as_of),
            'registration_ref': registration_ref, 'shortlist_visible_at_cutoff': start is not None and start <= as_of,
            'scope': 'offline_diagnostic_only', 'quote_basis': 'advertised_odds_no_execution_or_cost_evidence',
            'cohorts': cohorts}

"""Discovery, registration decisions, and out-of-sample replay orchestration."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

from .. import chronology, event_intelligence as events
from . import dsl, ledger, miner, replay as interpreter, researcher, study
from .replay import SETTLEMENT, Budget

DISCOVERY_FLOOR = chronology.parse_utc('2026-11-24T08:22:00Z')
MIN_DISCOVERY_CONTROLS = 5
ALTERNATIVE_REPORTS = 6
MAX_WAVES = 12
MAX_MODEL_REPLAY_WORK = 50_000
# Version 2 adds the terminal decision rule that version 1 lacked. No version 1
# batch was ever independently witnessed. Unknown protocols are reported per
# batch and never block the evaluation of other publication families.
PROTOCOL = {
    'version': 2, 'dsl_version': dsl.VERSION, 'discovery_floor_utc': events.iso(DISCOVERY_FLOOR),
    'start': 'next_utc_midnight_after_verified_publication',
    'window_days': study.WINDOW.days, 'ft_freeze_days': 3,
    'reservation': 'first_eligible_anchor_by_receipt_emission_and_numeric_id_per_fixture',
    'receipt': 'completed_page_original',
    'maximum_pair_lag_minutes': chronology.DECISION_DELAY_MAX_MIN,
    'matching': 'exact_stratum_one_per_arm_and_fixture_before_market_close',
    'inference': 'two_sided_kickoff_day_sign_holm_with_wave_alpha_spending',
    'minimum_priced_pairs': study.MIN_FORWARD_PAIRS, 'minimum_informative_days': study.MIN_FORWARD_DAYS,
    'minimum_arm_coverage': study.MIN_COVERAGE,
    'verdict': 'after_window_and_settlement_close: experimental_iff_holm_adjusted_p_le_wave_alpha_'
               'and_positive_day_difference_else_rejected_with_reason',
}
# Bytes of the code that gives a registered definition its meaning. A change is
# reported at evaluation and withholds any verdict for batches recorded earlier.
INTERPRETER_SHA256 = hashlib.sha256(b''.join(
    Path(module.__file__).read_bytes() for module in (dsl, interpreter))).hexdigest()


def candidate_record(candidate, state, reason, cases, controls, pairs, track):
    return dict(id=candidate.id, rule=candidate.rule, status=state, origin=candidate.origin,
                reason=reason, track_prospectively=track, discovery_support=dict(cases=cases, controls=controls, pairs=pairs),
                mechanism=candidate.mechanism, refutation=candidate.refutation, alternative=candidate.alternative)


def discover(replay, batches=(), *, model=None, reply=None, work=miner.MAX_REPLAY_WORK, emissions=miner.MAX_EMISSIONS):
    as_of, manifest = replay.as_of, replay.audit['input_sha256']
    known = ledger.known(batches, as_of, replay.snapshot_sha256)
    mined, metrics = miner.mine(replay, known, emissions=emissions)
    memory = [record for b in ledger.visible(batches, as_of) for record in b['records']]
    model_response, reason = None, 'not_configured'
    model_request = None
    if model is not None or reply is not None:
        model_request = researcher.prompt(replay, memory)
        if model is not None:
            model_response, reason = researcher.run_local(model, model_request)
        else:
            model_response, reason = reply, 'offline_response'
    proposed, refusals = researcher.proposals(model_response) if model_response is not None else ([], [])
    model_report = {'status': reason, 'proposals': len(proposed), 'rejected': len(refusals)}
    if model_request is not None:
        model_report['prompt_sha256'] = dsl.digest(model_request)
        model_report['prompt_bytes'] = len(model_request.encode())
    if model_response is not None:
        model_report['response_sha256'] = hashlib.sha256(model_response).hexdigest()
        model_report['response_bytes'] = len(model_response)

    # The optional, untrusted reply gets its own meter. Deterministic results
    # are therefore identical with or without a model reply.
    model_work = min(work // 4, MAX_MODEL_REPLAY_WORK) if proposed else 0
    model_budget, budget = Budget(model_work), Budget(work)
    records = list({r['id']: r for r in refusals if r['id'] not in known}.values())
    reports, alternatives = [], []
    signatures = {}
    attempted = set(known)
    # Reserve deterministic work independently of an optional untrusted reply.
    deferred = 0
    for candidate in [*mined, *proposed]:
        if candidate.id in attempted:
            continue
        attempted.add(candidate.id)
        meter = model_budget if candidate.origin == 'llm' else budget
        if meter.exhausted or meter.limit == 0:
            deferred += 1
            continue
        decisions, pairs = replay.decide(candidate.rule, meter)
        if meter.exhausted:
            deferred += 1
            continue
        counts = Counter(d.state for d in decisions)
        cases, controls = counts['case'], counts['control']
        state, why, track = 'insufficient', 'incomplete_search', False
        if not counts['search_budget']:
            if cases < miner.MIN_SCREEN_GAMES:
                why = 'too_few_case_fixtures'
            elif controls < MIN_DISCOVERY_CONTROLS:
                why = 'too_few_control_fixtures'
            else:
                footprint = dsl.digest([(d.fixture, d.anchor['message_id'], d.state) for d in decisions])
                if footprint in signatures:
                    why = 'same_discovery_cohort'
                else:
                    signatures[footprint] = candidate.id
                    # Allow a well-defined opportunity to collect new evidence
                    # even when strict matching found no historical support.
                    track = True
                    if pairs:
                        state, why = 'experimental', 'discovery_common_support'
                    else:
                        why = 'no_comparable_discovery_pairs'
        record = candidate_record(candidate, state, why, cases, controls, len(pairs), track)
        records.append(record)
        report = dict(record, historical=study.profile(replay, decisions, pairs, examples=2),
                      prospective_fixtures=0)
        reports.append(report)
        if track and candidate.alternative is not None:
            alternatives.append((report, candidate.alternative))

    for report, alternative in alternatives[:ALTERNATIVE_REPORTS]:
        meter = model_budget if report['origin'] == 'llm' else budget
        if meter.exhausted:
            continue
        decisions, pairs = replay.decide(alternative, meter)
        if not meter.exhausted:
            report['alternative_probe'] = study.profile(replay, decisions, pairs, examples=1)

    reports.sort(key=lambda r: (r['status'] != 'experimental', -r['discovery_support']['pairs'],
                                -r['discovery_support']['cases'], r['id']))
    counts = dict(Counter(r['status'] for r in records))
    metrics.update(search_expansions=budget.used + model_budget.used,
                   search_truncated=budget.exhausted or model_budget.exhausted,
                   model_replay_operations=model_budget.used, unevaluated_candidates=deferred,
                   publication_candidates=len(records), known_at_cutoff=len(known))
    return dict(schema='temporal_discovery_report_v1', status='discovery_only', as_of_utc=events.iso(as_of),
                input_sha256=manifest, snapshot_sha256=replay.snapshot_sha256, exclusions=dict(replay.exclusions), candidate_states=counts,
                investigations=reports, model=model_report, model_rejections=refusals, work=metrics,
                prospective_fixtures=0, price_basis='announced_only', real_execution='unproven',
                protocol_sha256=dsl.digest(PROTOCOL), interpreter_sha256=INTERPRETER_SHA256,
                verdict=study.VERDICT), records


def terminal(evidence, adjusted, alpha, closed):
    """Fixed decision once the window and its settlement period have closed."""
    if not closed:
        return 'insufficient', 'forward_window_open'
    matched = evidence['matched']
    if matched['sign_test_p'] is None:
        return 'rejected', 'no_forward_support_at_close'
    if adjusted <= alpha:
        if (matched['average_day_difference'] or 0) > 0:
            return 'experimental', 'forward_difference_survived_holm'
        return 'rejected', 'adverse_forward_difference'
    return 'rejected', 'no_forward_difference_at_close'


def _next_day(at):
    return datetime.combine(at.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)


def evaluate(replay, batches, receipts=None, *, verify=None, wave=None):
    """Recompute evidence against immutable definitions and independently witnessed windows.

    Errors and rejections are retained: each registered definition receives a
    row, and a closed window ends as `experimental` or `rejected` with a reason.
    """
    as_of = replay.as_of
    closed_reports, known, families = [], set(), []
    budget = Budget(study.MAX_FORWARD_WORK)
    for batch in ledger.visible(batches, as_of):
        tracked = [r for r in batch['records'] if r.get('track_prospectively') and r['id'] not in known]
        known.update(r['id'] for r in tracked)
        if tracked and (wave is None or wave == batch['number']):
            families.append((batch, tracked))
    for batch, tracked in families[:MAX_WAVES]:
        protocol = batch['investigation'].get('protocol_sha256') == dsl.digest(PROTOCOL)
        recorded = batch['investigation'].get('interpreter_sha256')
        semantics = ('unrecorded' if recorded is None else
                     'unchanged' if recorded == INTERPRETER_SHA256 else 'changed_since_registration')
        checked = {'verify': verify} if verify is not None else {}
        proof = ledger.attest(batch, receipts, as_of, **checked) if protocol else None
        discover_at = chronology.parse_utc(batch['discovery_as_of_utc'])
        alpha = .05 / (batch['number'] * (batch['number'] + 1))
        reports, pvalues = [], []
        for r in tracked:
            row = dict(id=r['id'], rule=r['rule'], origin=r['origin'], status='insufficient',
                       reason='awaiting_independent_publication',
                       registration='awaiting_independent_publication', prospective_fixtures=0,
                       publication_sha256=ledger.digest(batch), mechanism=r.get('mechanism'),
                       refutation=r.get('refutation'), interpreter=semantics)
            p = None
            if not protocol:
                row.update(registration='unsupported_forward_protocol', reason='unsupported_forward_protocol')
            elif proof is not None:
                start = _next_day(max(proof.at, discover_at, DISCOVERY_FLOOR))
                end = start + study.WINDOW
                row.update(registration='independently_witnessed', receipt=proof.identifier, reason='forward_window_not_started',
                           born_utc=events.iso(proof.at), start_utc=events.iso(start), end_utc=events.iso(end),
                           closes_utc=events.iso(end + SETTLEMENT))
                if as_of >= start:
                    used = budget.used
                    if not budget.exhausted:
                        decisions, pairs = replay.decide(r['rule'], budget, start=start, end=end, discovery_cutoff=discover_at)
                    if budget.exhausted:
                        row.update(evaluation='work_budget_deferred', reason='work_budget_deferred')
                    else:
                        evidence = study.profile(replay, decisions, pairs, start=start, end=end)
                        row.update(prospective_fixtures=len(decisions), evidence=evidence, search_expansions=budget.used - used,
                                   reason='forward_window_open', window_closed=as_of >= end + SETTLEMENT)
                        p = evidence['matched']['sign_test_p']
            reports.append(row)
            pvalues.append(p)
        correction = study.holm(pvalues)
        # A family includes all registered candidates, including unsupported ones.
        for row, value, p in zip(reports, correction, pvalues):
            row['multiplicity'] = dict(method='holm_with_wave_alpha_spending', family_size=len(reports),
                                       family_number=batch['number'], alpha=alpha,
                                       adjusted_p=value if p is not None else None)
            if 'evidence' not in row:
                continue
            if semantics == 'changed_since_registration':
                row['reason'] = 'interpreter_changed_since_registration'
                continue
            row['status'], row['reason'] = terminal(row['evidence'], value, alpha, row['window_closed'])
        closed_reports.extend(reports)
    states = Counter(r['status'] for r in closed_reports)
    return dict(schema='temporal_discovery_report_v1', status='prospective_monitoring',
                as_of_utc=events.iso(as_of), input_sha256=replay.audit['input_sha256'],
                investigations=closed_reports, candidate_states=dict(states),
                reasons=dict(Counter(r['reason'] for r in closed_reports)),
                waves_deferred=max(0, len(families) - MAX_WAVES),
                work=dict(search_expansions=budget.used, search_truncated=budget.exhausted),
                price_basis='announced_only', real_execution='unproven',
                protocol_sha256=dsl.digest(PROTOCOL), interpreter_sha256=INTERPRETER_SHA256, verdict=study.VERDICT)

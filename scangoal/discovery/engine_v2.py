"""Versioned research pipeline. Existing registered protocols keep their interpreter."""

from collections import Counter
from datetime import timedelta
import hashlib
from pathlib import Path

from .. import chronology, event_audit, event_intelligence as events, opportunity_results, parser, scientific_receipts
from . import blocks_v2 as blocks, dsl, dsl_v2, engine, ledger, miner_v2, replay_v2, researcher, study
from .replay import Budget, SETTLEMENT

EVIDENCE_DOMAIN = 'temporal-day-contract-evidence-v2'
PROTOCOL = {
    'name': 'temporal-day-contract-v2', 'format': 2, 'dsl_version': 2,
    'window_days': study.WINDOW.days, 'settlement_days': SETTLEMENT.days,
    'discovery_floor_utc': events.iso(engine.DISCOVERY_FLOOR),
    'start': 'first_utc_midnight_after_verified_publication_and_discovery_cutoff',
    'reservation': 'first_observed_case_completion_or_first_determinate_control_check',
    'allocation_salt': replay_v2.ALLOCATION.hex(), 'control_modulus': replay_v2.CONTROL_MODULUS,
    'required_receipt': 'completed_page',
    'block': ['kickoff_day_utc', 'source', 'phase', 'native_market', 'period', 'line', 'selection'],
    'covariates': {'log_odds': blocks.LOG_ODDS_LIMIT, 'lead_minutes': blocks.PRE_MINUTES_LIMIT,
                   'live_minutes': blocks.LIVE_MINUTES_LIMIT, 'goals_and_difference': blocks.GOALS_LIMIT,
                   'weight': 'max_0_one_minus_max_scaled_distance'},
    'minimum_effective_arm': blocks.MIN_ARM, 'minimum_days': blocks.MIN_DAYS,
    'minimum_ft_weight_coverage': blocks.MIN_COVERAGE,
    'economics': {'winning_fraction_haircut': blocks.COST, 'unit_fee': blocks.COST, 'quote': 'announced_only'},
    'inference': 'one_sided_kickoff_day_sign_on_worst_case_missing_ft_holm',
    'alpha': '0.05/(wave*(wave+1))',
}
PROTOCOL_SHA256 = dsl.digest(PROTOCOL)
INTERPRETER_SHA256 = hashlib.sha256(Path(__file__).read_bytes() + b''.join(
    Path(module.__file__).read_bytes() for module in (
        dsl, dsl_v2, engine.interpreter, replay_v2, blocks, study, engine,
        chronology, event_audit, events, opportunity_results, parser))).hexdigest()


def model_candidates(replay, batches, model, reply):
    if model is None and reply is None:
        return [], [], {'status': 'not_configured'}
    memory = [r for b in ledger.visible(batches, replay.as_of) for r in b['records']]
    prompt = researcher.prompt(replay, memory, language=dsl_v2)
    raw, status = researcher.run_local(model, prompt) if model is not None else (reply, 'offline_response')
    proposed, rejected = (researcher.proposals(raw, language=dsl_v2, candidate=miner_v2.Candidate)
                          if raw is not None else ([], []))
    return proposed, rejected, dict(status=status, proposals=len(proposed), rejected=len(rejected),
                                   prompt_sha256=dsl.digest(prompt),
                                   response_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None)


def discover(replay, batches=(), *, model=None, reply=None, work=miner_v2.MAX_REPLAY_WORK, emissions=miner_v2.MAX_EMISSIONS):
    timeline = replay_v2.Timeline(replay)
    known = ledger.known(batches, replay.as_of, replay.snapshot_sha256)
    candidates, counters = miner_v2.mine(timeline, known, emissions=emissions)
    proposed, rejected, model_report = model_candidates(replay, batches, model, reply)
    records = list({r['id']: r for r in rejected if r['id'] not in known}.values())
    deterministic, optional = Budget(work), Budget(min(work // 4, engine.MAX_MODEL_REPLAY_WORK))
    compare = Budget(blocks.MAX_BLOCK_WORK)
    reserved_signatures = set()
    reports = []
    visited = set(known)
    probes = 0
    for p in [*candidates, *proposed]:
        if p.id in visited:
            continue
        visited.add(p.id)
        meter = optional if p.origin == 'llm' else deterministic
        if meter.exhausted or compare.exhausted:
            break
        cohort = timeline.run(p.rule, meter)
        if meter.exhausted:
            if p.origin == 'llm':
                continue
            break
        match = blocks.overlap(cohort.decisions, compare)
        if match.exhausted:
            break
        cases, controls = (match.counts.get(name, 0) for name in ('case', 'control'))
        signature = dsl.digest([(d.fixture, d.anchor['message_id'], d.state) for d in cohort.decisions])
        reason, state, track = 'small_arm', 'insufficient', False
        if cohort.withheld.get('search_incomplete'):
            reason = 'incomplete_search'
        elif min(cases, controls) >= engine.MIN_DISCOVERY_CONTROLS:
            if signature in reserved_signatures:
                reason = 'same_discovery_cohort'
            else:
                reserved_signatures.add(signature)
                track = True
                if match.blocks:
                    state, reason = 'experimental', 'discovery_overlap'
                else:
                    reason = 'no_common_support'
        record = engine.candidate_record(p, state, reason, cases, controls, len(match.blocks), track)
        record['discovery_support']['blocks'] = record['discovery_support'].pop('pairs')
        records.append(record)
        historical = blocks.profile(replay, cohort, match)
        report = dict(record, historical=historical, prospective_fixtures=0)
        if p.alternative and track and probes < engine.ALTERNATIVE_REPORTS:
            alternate = timeline.run(p.alternative, meter)
            design = blocks.overlap(alternate.decisions, compare)
            if not meter.exhausted and not design.exhausted:
                report['alternative_probe'] = blocks.profile(replay, alternate, design)
                probes += 1
        reports.append(report)
    counters.update(replay_operations=deterministic.used, replay_truncated=deterministic.exhausted,
                    model_replay_operations=optional.used, overlap_operations=compare.used,
                    overlap_truncated=compare.exhausted)
    return dict(schema='temporal_discovery_report_v2', status='discovery_only',
                as_of_utc=events.iso(replay.as_of), input_sha256=replay.audit['input_sha256'],
                snapshot_sha256=replay.snapshot_sha256, protocol_sha256=PROTOCOL_SHA256,
                interpreter_sha256=INTERPRETER_SHA256, work=counters,
                candidate_states=dict(Counter(r['status'] for r in records)),
                investigations=reports, model=model_report, model_rejections=rejected,
                prospective_fixtures=0, real_execution='unproven', price_basis='announced_only',
                verdict=study.VERDICT), records


def evaluate(replay, batches, receipts=None, *, verify=None, wave=None):
    """Replay only v2 proposals under the new independently witnessed protocol."""
    as_of = replay.as_of
    output, known, families = [], set(), []
    for batch in ledger.visible(batches, as_of):
        tracked = [r for r in batch['records'] if r.get('track_prospectively') and r.get('rule', {}).get('version') == 2
                   and r['id'] not in known]
        known.update(r['id'] for r in tracked)
        if tracked and (wave is None or batch['number'] == wave):
            families.append((batch, tracked))
    work, overlap = Budget(miner_v2.MAX_REPLAY_WORK), Budget(blocks.MAX_BLOCK_WORK)
    for batch, tracked in families[:engine.MAX_WAVES]:
        settings = batch['investigation']
        protocol_ok = settings.get('protocol_sha256') == PROTOCOL_SHA256
        code_ok = settings.get('interpreter_sha256') == INTERPRETER_SHA256
        proof = ledger.attest(batch, receipts, as_of, **({'verify': verify} if verify else {})) if protocol_ok and code_ok else None
        discovery = chronology.parse_utc(batch['discovery_as_of_utc'])
        alpha = .05 / (batch['number'] * (batch['number'] + 1))
        rows, observed = [], []
        if proof is not None:
            start = engine._next_day(max(proof.at, discovery, engine.DISCOVERY_FLOOR))
            end = start + timedelta(days=PROTOCOL['window_days'])
            closed = as_of >= end + SETTLEMENT
            timeline = replay_v2.Timeline(replay, start=start, end=end, discovery_cutoff=discovery)
        for r in tracked:
            row = dict(id=r['id'], rule=r['rule'], origin=r['origin'], publication_sha256=ledger.digest(batch),
                       status='insufficient', prospective_fixtures=0, protocol_sha256=PROTOCOL_SHA256,
                       mechanism=r.get('mechanism'), refutation=r.get('refutation'))
            p = None
            if not protocol_ok:
                row['reason'] = 'unsupported_forward_protocol'
            elif not code_ok:
                row['reason'] = 'interpreter_changed_since_registration'
            elif proof is None:
                row['reason'] = 'awaiting_independent_publication'
            else:
                row.update(receipt=proof.identifier, born_utc=events.iso(proof.at), start_utc=events.iso(start),
                           end_utc=events.iso(end), window_closed=closed)
                if as_of < start:
                    row['reason'] = 'forward_window_not_started'
                elif work.exhausted or overlap.exhausted:
                    row['reason'] = 'work_budget_deferred'
                else:
                    cohort = timeline.run(r['rule'], work)
                    design = blocks.overlap(cohort.decisions, overlap) if not work.exhausted else None
                    if design is None or design.exhausted:
                        row['reason'] = 'work_budget_deferred'
                    else:
                        evidence = blocks.profile(replay, cohort, design, end=end)
                        p = evidence['nominal_sign_p']
                        row.update(evidence=evidence, prospective_fixtures=len(cohort.decisions),
                                   reason='forward_window_closed' if closed else 'forward_window_open')
            rows.append(row)
            observed.append(p)
        adjusted = study.holm(observed)
        for row, p, corrected in zip(rows, observed, adjusted):
            row['multiplicity'] = dict(method='holm_with_wave_alpha_spending', alpha=alpha,
                                       family_size=len(rows), adjusted_p=corrected if p is not None else None)
            if row['reason'] != 'forward_window_closed':
                continue
            if p is not None and corrected <= alpha and (row['evidence']['mean_day_missing_ft_bounds'][0] or 0) > 0:
                row.update(status='experimental', reason='forward_difference_survived_holm')
            else:
                row.update(status='rejected', reason=('no_forward_support_at_close' if p is None else
                                                      'no_forward_difference_at_close'))
        output.extend(rows)
    return dict(schema='temporal_discovery_report_v2', status='prospective_monitoring', as_of_utc=events.iso(as_of),
                input_sha256=replay.audit['input_sha256'], snapshot_sha256=replay.snapshot_sha256,
                protocol_sha256=PROTOCOL_SHA256, interpreter_sha256=INTERPRETER_SHA256,
                investigations=output, candidate_states=dict(Counter(r['status'] for r in output)),
                waves_deferred=max(0, len(families) - engine.MAX_WAVES),
                work=dict(replay_operations=work.used, overlap_operations=overlap.used,
                          truncated=work.exhausted or overlap.exhausted), real_execution='unproven',
                price_basis='announced_only', verdict=study.VERDICT)


def archive_evidence(directory, report):
    """Opt-in immutable result snapshots, separately from proposal publication."""
    if report.get('schema') != 'temporal_discovery_report_v2' or report.get('status') != 'prospective_monitoring':
        raise ValueError('expected v2 prospective evidence')
    scientific_receipts.save_commitment(directory, EVIDENCE_DOMAIN, report)
    return scientific_receipts.digest(EVIDENCE_DOMAIN, report)

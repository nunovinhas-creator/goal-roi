"""Outcome-blind selection and independent prospective evaluation."""

from datetime import timedelta
import hashlib
from pathlib import Path

from ... import chronology, eligibility, event_intelligence as events, match_identity, scientific_receipts, store
from ...observability import contracts, extract, timeline
from .. import dsl, miner, miner_v2, researcher
from ..replay import Budget, SETTLEMENT
from ..v3 import engine as frozen
from . import language, mining, protocol, registry, riskset, source, tape

VERDICT = frozen.VERDICT
MAX_WORK = frozen.MAX_WORK
INTERPRETER_SHA256 = hashlib.sha256(
    frozen.INTERPRETER_SHA256.encode() + b''.join(Path(module.__file__).read_bytes() for module in
        (contracts, extract, timeline, eligibility, match_identity, scientific_receipts, store, language, mining,
         protocol, registry, riskset, source, tape, miner, miner_v2, researcher)) + Path(__file__).read_bytes()
).hexdigest()


def _report(tape, **values):
    return dict(schema='temporal_risk_set_report_v32', as_of_utc=events.iso(tape.as_of),
                protocol_sha256=protocol.PROTOCOL_SHA256, interpreter_sha256=INTERPRETER_SHA256,
                snapshot_sha256=tape.snapshot_sha256, confirmatory=False,
                executable_profitability='not_demonstrated', verdict=VERDICT) | values


def pair_signature(cohort):
    return dsl.digest([(events.iso(c.at), c.fixture, k.fixture, c.anchor['message_id'], k.anchor['message_id'])
                       for c, k, _ in cohort.pairs])


def discover(tape, *, model=None, reply=None, emissions=miner_v2.MAX_EMISSIONS, work=MAX_WORK, known=()):
    trajectory = mining.Timeline(tape)
    old_budget = emissions // 2
    legacy, legacy_stats = miner_v2.mine(trajectory, emissions=old_budget) if old_budget else ([], {})
    typed, typed_used = mining.enrich(trajectory, emissions=emissions - old_budget)
    lifted = [mining.Candidate(language.lift(item.rule), 'legacy_projection', item.mechanism,
                               item.refutation, language.lift(item.alternative) if item.alternative else None)
              for item in legacy]
    optional, refusals, model_status = [], [], 'not_configured'
    if model is not None:
        reply, model_status = researcher.run_local(model, mining.prompt(trajectory))
    elif reply is not None:
        model_status = 'offline_response'
    if reply is not None:
        optional, refusals = researcher.proposals(reply, language=language, candidate=mining.Candidate)

    design = riskset.RiskSet(tape)
    seen, rows = set(known), []
    for candidates, meter in (([*typed, *lifted], Budget(work)), (optional, Budget(work // 4))):
        for item in candidates:
            hid = registry.hypothesis_id(item.rule)
            if hid in seen:
                continue
            seen.add(hid)
            record = dict(id=hid, rule=item.rule, origin=item.origin, status='exploratory_only',
                          mechanism=item.mechanism, refutation=item.refutation, alternative=item.alternative)
            if meter.exhausted:
                rows.append(dict(record, reason='work_budget_deferred'))
                continue
            cohort = design.run(item.rule, meter)
            incomplete = bool(cohort.counts['budget_exhausted'] or cohort.counts['search_incomplete'])
            rows.append(dict(record, counts=dict(cohort.counts), pairing_sha256=pair_signature(cohort),
                             reason='incomplete_search' if incomplete else 'support_measured'))
    eligible = sorted((row for row in rows if row['reason'] == 'support_measured'
                       and row['counts'].get('matched_pairs', 0) >= protocol.MIN_DISCOVERY_PAIRS),
                      key=lambda row: (-row['counts']['matched_pairs'], row['id']))
    chosen, signatures = [], set()
    for row in eligible:
        signature = row['pairing_sha256']
        if signature not in signatures:
            signatures.add(signature)
            chosen.append({name: row[name] for name in ('id', 'rule', 'origin')})
        if len(chosen) == protocol.MAX_FAMILY:
            break
    return _report(tape, status='exploratory_only', investigations=rows, proposed_registration=chosen,
                   coverages=sum(map(len, tape.coverage.values())), model=dict(status=model_status, rejected=refusals),
                   mining=dict(legacy=legacy_stats, typed_profile=mining.GENERATOR, typed_operations=typed_used))


def evaluate(tape, plans, receipts=None, *, outcomes=None, verify=None, wave=None):
    """Run only witnessed, closed, prospective windows. No pre-close results are computed."""
    output, evaluated = [], False
    meter = Budget(MAX_WORK * 4)
    for plan in plans:
        # Readers validate the immutable chain. A programmatic caller still
        # cannot use a legacy plan or silently swap the frozen interpreter.
        published = chronology.parse_utc(plan['published_utc'])
        if published is None or published > tape.as_of or (wave is not None and plan['number'] != wave):
            continue
        proof = None
        start = end = None
        if plan['protocol_sha256'] != protocol.PROTOCOL_SHA256:
            reason = 'blocked_or_unsupported_protocol'
        elif plan['interpreter_sha256'] != INTERPRETER_SHA256:
            reason = 'interpreter_changed_since_registration'
        else:
            proof = registry.attest(plan, receipts, tape.as_of, **({'verify': verify} if verify else {}))
            reason = 'awaiting_independent_witness'
        if proof is not None:
            cutoff = chronology.parse_utc(plan['discovery_as_of_utc'])
            start = frozen._next_day(max(proof.at, cutoff))
            end = start + timedelta(days=protocol.WINDOW_DAYS)
            if tape.as_of < start:
                reason = 'forward_window_not_started'
            elif tape.as_of < end + SETTLEMENT:
                reason = 'forward_window_or_settlement_open'
            else:
                reason = 'forward_window_closed'
                design = riskset.RiskSet(tape, start=start, end=end, discovery_cutoff=cutoff)
        rows, evidences = [], []
        for hypothesis in plan['hypotheses']:
            row = dict(id=hypothesis['id'], rule=hypothesis['rule'], wave=plan['number'],
                       status='insufficient', reason=reason, plan_sha256=registry.digest(plan))
            if proof is not None:
                row.update(start_utc=events.iso(start), end_utc=events.iso(end), receipt=proof.identifier)
            evidence = None
            if reason == 'forward_window_closed' and outcomes is not None:
                assessment, cohort = frozen.assess(outcomes, hypothesis['rule'], budget=meter, design=design)
                row['evidence'] = assessment
                if assessment['truncated']:
                    row['reason'] = 'incomplete_search'
                else:
                    row['evidence']['pairing_sha256'] = pair_signature(cohort)
                    evidence = assessment
                    evaluated |= any(assessment['layers'][name]['p'] is not None for name in frozen.LAYERS)
            elif reason == 'forward_window_closed':
                row['reason'] = 'outcomes_archive_unavailable'
            rows.append(row)
            evidences.append(evidence)
        for row, verdict in zip(rows, frozen.decide(evidences, plan['number'], closed=reason == 'forward_window_closed')):
            row.update(verdict)
        output.extend(rows)
    return _report(tape, status='prospective_monitoring', confirmatory=evaluated, investigations=output)

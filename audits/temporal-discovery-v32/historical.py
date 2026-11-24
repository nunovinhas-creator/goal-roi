"""Reassess the frozen 13 v3.1 pair decisions without treating them as holdout."""

import argparse
from collections import Counter
import json
from pathlib import Path

from scangoal import chronology, event_audit, event_intelligence as events
from scangoal.discovery import dsl, engine_v2
from scangoal.discovery.replay import Budget, Replay
from scangoal.discovery.v3 import engine as old, riskset as frozen
from scangoal.discovery.v32 import language, protocol, riskset, source
from scangoal.observability import contract_key

CUTOFF = '2026-11-24T08:22:00Z'
SNAPSHOT = '1f2bf65d751dc735d2a0688c700e7ff24ad9b41474e87f7768efc38c9e102944'


def resolve(tape, sequence, decision):
    at = decision.at
    original = decision.anchor
    identity = tape.lookup_rows.get((original['record_sha256'], 'quote'))
    if identity is None or tape.blocked_at(identity[0], at):
        return 'indeterminate', 'identity_unproven', None, ()
    view = riskset.view_at(tape, identity[0], at)
    quote = view.quotes.get(contract_key(original))
    if quote is None:
        return 'indeterminate', 'contract_quote_unproven', None, ()
    if reason := riskset.quote_gate(tape, quote, view, at):
        state = 'excluded' if (quote['price_kind'] == 'interval'
                               or reason == 'quote_stale_at_decision') else 'indeterminate'
        return state, reason, None, ()
    match, _ = riskset.opportunity(tape, sequence, quote, view, at, Budget(old.MAX_WORK))
    return match.state, None, quote, match.witness


def run(directory, cutoff, *, verify=False):
    archived = Replay(event_audit.build(directory, cutoff, quote_only=True))
    _, records = engine_v2.discover(archived)
    candidates = [record for record in records if 'rule' in record]
    historical = frozen.RiskSet(archived)
    tape = source.journal(directory, cutoff, historical=True)
    rows, cases = [], 0
    for record in candidates:
        old_rule = record['rule']
        cohort = historical.run(old_rule, Budget(old.MAX_WORK))
        cases += len(cohort.cases)
        sequence = language.Sequence(language.lift(old_rule))
        for case, comparator, _ in cohort.pairs:
            case_state, case_reason, case_quote, _ = resolve(tape, sequence, case)
            control_state, control_reason, control_quote, witness = resolve(tape, sequence, comparator)
            after_quote = any(events.emitted(event) > events.emitted(comparator.anchor)
                              for event in witness[:-1])
            if control_state == 'present':
                status, reason = 'excluded', 'antecedent_present_at_case_instant'
            elif 'excluded' in (case_state, control_state):
                status, reason = 'excluded', case_reason if case_state == 'excluded' else control_reason
            elif 'ineligible_terminal' in (case_state, control_state):
                status, reason = 'excluded', 'terminal_not_satisfied_at_case_instant'
            elif case_quote and control_quote and riskset.comparable(tape, case_quote, control_quote, case.at) is None:
                status, reason = 'excluded', 'incompatible_contract_or_caliper'
            elif 'indeterminate' in (case_state, control_state) or 'search_budget' in (case_state, control_state):
                status, reason = 'indeterminate', case_reason or control_reason or 'coverage_or_state_unproven'
            else:
                status, reason = 'excluded', 'legacy_receipt_in_both_arms'
            rows.append(dict(rule_sha256=dsl.digest(old_rule),
                             pair_sha256=dsl.digest((case.anchor['record_sha256'], comparator.anchor['record_sha256'])),
                             at_utc=events.iso(case.at), case_state=case_state, control_state=control_state,
                             case_reason=case_reason, control_reason=control_reason, status=status, reason=reason,
                             control_witness_after_archived_quote=after_quote,
                             case_receipt=case.anchor['receipt'], control_receipt=comparator.anchor['receipt'],
                             confirmatory=False))
    if verify and (archived.snapshot_sha256 != SNAPSHOT or cases != 1851 or len(rows) != 13 or len(candidates) != 54):
        raise ValueError('frozen v3.1 snapshot did not reproduce')
    rows.sort(key=lambda row: (row['at_utc'], row['rule_sha256'], row['pair_sha256']))
    return dict(schema='temporal_risk_set_v32_historical_comparison', cutoff=events.iso(cutoff),
                archived_snapshot_sha256=archived.snapshot_sha256, protocol_sha256=protocol.PROTOCOL_SHA256,
                archived_hypotheses=len(candidates), archived_cases=cases, archived_pairs=len(rows),
                distinct_archived_pairs=len({row['pair_sha256'] for row in rows}),
                controls_with_witness_after_archived_quote=sum(row['control_witness_after_archived_quote'] for row in rows),
                pair_status=dict(Counter(row['status'] for row in rows)),
                reasons=dict(Counter(row['reason'] for row in rows)), rows=rows,
                v32_confirmatory_pairs=0, confirmatory=False, verdict=old.VERDICT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--as-of', default=CUTOFF)
    parser.add_argument('--verify-frozen', action='store_true')
    args = parser.parse_args()
    cutoff = chronology.parse_utc(args.as_of)
    if cutoff is None:
        parser.error('use an explicit timezone for --as-of')
    print(json.dumps(run(args.data_dir, cutoff, verify=args.verify_frozen),
                     sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

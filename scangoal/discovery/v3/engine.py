"""Risk-set assessment, outcome-blind registration and confirmatory evaluation."""

from collections import Counter
from datetime import datetime, time, timedelta, timezone
import hashlib
from math import fsum, log
from pathlib import Path

from ... import chronology, event_audit, event_intelligence as events, opportunity_results, parser
from .. import dsl, dsl_v2, replay, replay_v2
from ..replay import Budget, SETTLEMENT
from . import inference, layers, protocol, registry, riskset

VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'
MAX_WORK = 4_000_000
LAYERS = ('prediction', 'announced')
INTERPRETER_SHA256 = hashlib.sha256(b''.join(Path(m.__file__).read_bytes() for m in (
    inference, layers, protocol, riskset, registry, dsl, dsl_v2, replay, replay_v2, chronology,
    event_audit, events, opportunity_results, parser)) + Path(__file__).read_bytes()).hexdigest()


def hypothesis_id(rule):
    """v3 identity binds the rule to this protocol; the same rule in v2 is a different hypothesis."""
    return 'tr3:' + dsl.digest(dict(protocol=protocol.PROTOCOL_SHA256, rule=dsl_v2.canonical(rule)))


def _labels(case, control):
    week = (case.kickoff or case.at).isocalendar()
    leagues = sorted({str(riskset.competition(d.anchor)) for d in (case, control)})
    return dict(kickoff_day=(case.kickoff or case.at).date().isoformat(), kickoff_week=f'{week[0]}-W{week[1]:02d}',
                source=case.anchor['source'], competition='|'.join(leagues))


def _covariates(meta, arm):
    return log(meta[arm + '_odds']), meta[arm + '_clock']


def _mean(values):
    values = [v for v in values if v is not None]
    return fsum(values) / len(values) if values else None


def assess(source, rule, *, start=None, end=None, discovery_cutoff=None, budget=None,
           settings=protocol.SETTINGS, design=None):
    """All three layers of one rule over one window. No status is decided here."""
    budget = budget or Budget(MAX_WORK)
    design = design or riskset.RiskSet(source, start=start, end=end, discovery_cutoff=discovery_cutoff)
    cohort = design.run(rule, budget)
    rows, pairs = [], {name: [] for name in LAYERS}
    excluded = Counter()
    for case, control, meta in cohort.pairs:
        a = layers.values(source.label(case), meta['case_odds'])
        b = layers.values(source.label(control), meta['control_odds'])
        rows.append((case, control, meta, a, b, _labels(case, control)))
    # Pre-registered bias correction: a linear outcome model fitted on settled
    # comparators only removes the residual calipered gap in price and clock.
    correction = {}
    for name in LAYERS:
        fitted = [(_covariates(m, 'control'), b[name][0]) for _, _, m, _, b, _ in rows
                  if b[name] is not None and b[name][0] is not None]
        slopes = inference.linear_fit([x for x, _ in fitted], [y for _, y in fitted]) if fitted else None
        correction[name] = dict(slopes=slopes, fitted_on_controls=len(fitted))
        for case, control, meta, a, b, labels in rows:
            difference = layers.pair_difference(a, b, name)
            if difference is None:
                excluded[name + '_push'] += 1
                continue
            shift = fsum(s * (x - y) for s, x, y in zip(slopes or (), _covariates(meta, 'case'),
                                                        _covariates(meta, 'control')))
            pairs[name].append(dict(observed=None if difference['observed'] is None else difference['observed'] - shift,
                                    lower=difference['lower'] - shift, upper=difference['upper'] - shift,
                                    unadjusted=difference['observed'], clusters=labels))
    case_values = [a for _, _, _, a, _, _ in rows]
    absolute = inference.cluster_t([a['announced'][1] for a in case_values],
                                   [lab['kickoff_week'] for *_, lab in rows])
    layered = {name: inference.assess(pairs[name], settings) for name in LAYERS}
    for name in LAYERS:
        raw = [p['unadjusted'] for p in pairs[name] if p['unadjusted'] is not None]
        layered[name]['bias_correction'] = dict(correction[name], mean_unadjusted=_mean(raw))
    layered['announced']['case_absolute'] = dict(
        mean_observed=_mean(a['announced'][0] for a in case_values),
        mean_lower_bound=_mean(a['announced'][1] for a in case_values),
        cluster_t_on_lower_bound=absolute)
    layered['executable'] = layers.executable(case_values)
    balance = dict(
        log_odds_gap=_mean(log(m['case_odds']) - log(m['control_odds']) for _, _, m, *_ in rows),
        clock_gap=_mean(m['case_clock'] - m['control_clock'] for _, _, m, *_ in rows),
        same_competition_fraction=_mean(float(m['same_competition']) for _, _, m, *_ in rows),
        comparator_quote_age_median_seconds=sorted(m['comparator_quote_age_seconds'] for _, _, m, *_ in rows)[
            len(rows) // 2] if rows else None)
    reservation = dsl.digest([(c.fixture, c.anchor['message_id'], events.iso(c.at),
                               [e['record_sha256'] for e in c.witness]) for c in cohort.cases])
    pairing = dsl.digest([(c.anchor['message_id'], k.anchor['message_id'], events.iso(c.at))
                          for c, k, _ in cohort.pairs])
    controls = [b for *_, b, _ in rows]

    def stake_rate(group, field):
        decisive = fsum(r['decisive_stake'] for r in group if r['decisive'])
        return fsum(r[field] for r in group if r['decisive']) / decisive if decisive else None

    prediction = dict(
        case_hit_rate=stake_rate(case_values, 'won_stake'),
        case_mean_implied=_mean(1 / a['odds'] for a in case_values if a['decisive']),
        control_hit_rate=stake_rate(controls, 'won_stake'),
        half_settlements=dict(case=sum(bool(a['half']) for a in case_values),
                              control=sum(bool(b['half']) for b in controls)))
    return dict(counts=dict(cohort.counts), excluded=dict(excluded), balance=balance,
                reservation_sha256=reservation, pairing_sha256=pairing, work=budget.used,
                truncated=budget.exhausted or bool(cohort.counts.get('search_incomplete')),
                descriptive_prediction=prediction, layers=layered,
                examples=[dict(case=c.describe(), control=k.describe(), match=m) for c, k, m, *_ in rows[:3]]), cohort


def decide(evidences, wave, *, closed):
    """Holm over every hypothesis x {prediction, announced}; then layered statuses."""
    a = protocol.alpha(wave)
    flat = [(i, name, (ev or {}).get('layers', {}).get(name, {}).get('p'))
            for i, ev in enumerate(evidences) for name in LAYERS]
    adjusted = inference.holm([p for _, _, p in flat])
    q = inference.benjamini_hochberg([p for _, _, p in flat])
    results = [dict(layers={}) for _ in evidences]
    for (i, name, p), corrected, fdr in zip(flat, adjusted, q):
        ev = evidences[i]
        layer = (ev or {}).get('layers', {}).get(name, {})
        survived = (closed and p is not None and corrected <= a and layer.get('robust_leave_one_out')
                    and (layer.get('mean_bounds') or [None])[0] is not None and layer['mean_bounds'][0] > 0)
        if name == 'announced' and survived:
            low = layer['case_absolute']['cluster_t_on_lower_bound']['interval'][0]
            survived = low is not None and low > 0
        state = ('experimental' if survived else 'insufficient' if not closed or p is None else 'rejected')
        results[i]['layers'][name] = dict(status=state, nominal_p=p, holm_p=corrected, bh_q=fdr, alpha=a)
    for i, ev in enumerate(evidences):
        states = {n: results[i]['layers'][n]['status'] for n in LAYERS}
        claims = [n for n in LAYERS if states[n] == 'experimental']
        results[i].update(
            status='experimental' if claims else 'rejected' if all(s == 'rejected' for s in states.values())
            else 'insufficient',
            claims=[dict(prediction='predictive_increment_beyond_announced_price',
                         announced='return_increment_and_positive_case_return_at_announced_price')[n]
                    for n in claims],
            executable_profitability='not_demonstrated')
    return results


def explore(source, rules, *, settings=protocol.SETTINGS, work=MAX_WORK):
    """Historical, exploratory only. Never confirmatory, never registered automatically."""
    design = riskset.RiskSet(source)
    budget = Budget(work)
    rows = []
    for item in rules:
        rule = item['rule']
        if budget.exhausted:
            rows.append(dict(item, hypothesis_id=hypothesis_id(rule), reason='work_budget_deferred'))
            continue
        evidence, _ = assess(source, rule, budget=budget, settings=settings, design=design)
        rows.append(dict(item, hypothesis_id=hypothesis_id(rule), evidence=evidence))
    verdicts = decide([r.get('evidence') for r in rows], 1, closed=True)
    for row, verdict in zip(rows, verdicts):
        row['exploratory_statistics'] = verdict
        row['status'] = 'exploratory_only'
    return rows


def select_for_registration(explored, limit=protocol.MAX_FAMILY):
    """Outcome-blind: rank by matched support only, never by FT, hit rate or ROI."""
    eligible = [r for r in explored if 'evidence' in r and not r['evidence']['truncated']
                and r['evidence']['counts'].get('matched_pairs', 0) >= protocol.MIN_DISCOVERY_PAIRS]
    eligible.sort(key=lambda r: (-r['evidence']['counts']['matched_pairs'], r['hypothesis_id']))
    chosen, seen = [], set()
    for row in eligible:
        signature = row['evidence']['pairing_sha256']
        if signature in seen:
            continue
        seen.add(signature)
        chosen.append(row)
        if len(chosen) == limit:
            break
    return chosen


def discover(source, *, candidates, work=MAX_WORK):
    """Re-identify candidate rules (e.g. v2 mining output) under v3 and explore them."""
    unique = {}
    for item in candidates:
        try:
            rule = dsl_v2.canonical(item['rule'])
        except (dsl.Invalid, KeyError, TypeError):
            continue
        unique.setdefault(hypothesis_id(rule), dict(rule=rule, origin=item.get('origin', 'unknown'),
                                                     mechanism=item.get('mechanism'),
                                                     source_id=item.get('id')))
    explored = explore(source, list(unique.values()), work=work)
    chosen = select_for_registration(explored)
    hypotheses = [dict(id=r['hypothesis_id'], rule=r['rule'], origin=r['origin'], mechanism=r.get('mechanism'),
                       source_id=r.get('source_id')) for r in chosen]
    return dict(schema='temporal_risk_set_report_v3', status='discovery_exploratory_only',
                as_of_utc=events.iso(source.as_of), snapshot_sha256=source.snapshot_sha256,
                protocol_sha256=protocol.PROTOCOL_SHA256, interpreter_sha256=INTERPRETER_SHA256,
                confirmatory=False, investigations=explored, proposed_registration=hypotheses,
                real_execution='unproven', price_basis='announced_only', verdict=VERDICT)


def _next_day(at):
    return datetime.combine(at.date() + timedelta(days=1), time(), tzinfo=timezone.utc)


def evaluate(source, plans, receipts=None, *, verify=None):
    """Confirmatory evaluation of witnessed v3 plans; zero results before the window."""
    output = []
    budget = Budget(MAX_WORK * 4)
    for plan in registry.visible(plans, source.as_of):
        superseded = plan['protocol_sha256'] in protocol.SUPERSEDED
        ok_protocol = plan['protocol_sha256'] == protocol.PROTOCOL_SHA256
        ok_code = plan['interpreter_sha256'] == INTERPRETER_SHA256
        proof = (registry.attest(plan, receipts, source.as_of, **({'verify': verify} if verify else {}))
                 if ok_protocol and ok_code else None)
        discovery = chronology.parse_utc(plan['discovery_as_of_utc'])
        rows, evidences = [], []
        start = end = None
        closed = False
        if proof is not None:
            start = _next_day(max(proof.at, discovery))
            end = start + timedelta(days=protocol.WINDOW_DAYS)
            closed = source.as_of >= end + SETTLEMENT
            design = riskset.RiskSet(source, start=start, end=end, discovery_cutoff=discovery)
        for h in plan['hypotheses']:
            row = dict(id=h['id'], rule=h['rule'], origin=h['origin'], plan_sha256=registry.digest(plan),
                       wave=plan['number'], status='insufficient')
            evidence = None
            if superseded:
                row['reason'] = 'superseded_protocol_' + protocol.SUPERSEDED[plan['protocol_sha256']]
            elif not ok_protocol:
                row['reason'] = 'unsupported_protocol'
            elif not ok_code:
                row['reason'] = 'interpreter_changed_since_registration'
            elif proof is None:
                row['reason'] = 'awaiting_independent_witness'
            else:
                row.update(receipt=proof.identifier, witnessed_utc=events.iso(proof.at), start_utc=events.iso(start),
                           end_utc=events.iso(end), window_closed=closed)
                if source.as_of < start:
                    row['reason'] = 'forward_window_not_started'
                elif budget.exhausted:
                    row['reason'] = 'work_budget_deferred'
                else:
                    evidence, _ = assess(source, h['rule'], budget=budget, design=design)
                    row['evidence'] = evidence
                    row['reason'] = 'forward_window_closed' if closed else 'forward_window_open'
                    if evidence['truncated']:
                        evidence = None
                        row['reason'] = 'incomplete_search'
            rows.append(row)
            evidences.append(evidence)
        for row, verdict in zip(rows, decide(evidences, plan['number'], closed=closed)):
            row.update(verdict)
            if row['reason'] not in ('forward_window_closed',):
                row['status'] = 'insufficient'
        output.extend(rows)
    return dict(schema='temporal_risk_set_report_v3', status='prospective_monitoring',
                as_of_utc=events.iso(source.as_of), snapshot_sha256=source.snapshot_sha256,
                protocol_sha256=protocol.PROTOCOL_SHA256, interpreter_sha256=INTERPRETER_SHA256,
                confirmatory=True, investigations=output,
                candidate_states=dict(Counter(r['status'] for r in output)),
                real_execution='unproven', price_basis='announced_only', verdict=VERDICT)

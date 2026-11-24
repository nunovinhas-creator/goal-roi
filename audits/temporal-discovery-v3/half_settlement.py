"""Measure the Asian half-settlement defect of protocol v3.0 (fixed in v3.1).

1. Fair-price bias: for quarter lines under a Poisson goal law, the expected
   v3.0 binary residual (won - 1/odds, half results counted as full) versus
   the v3.1 stake residual (won stake - decisive stake/odds). At a fair price
   v3.1 is exactly zero; v3.0 is not.
2. Exposure: how many real and synthetic decisions settle as +0.5/-0.5, among
   all priced decision anchors, v3 case decisions and matched pairs.
"""

import argparse
from collections import Counter
from datetime import timedelta
import json
from math import exp, factorial
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'temporal-discovery-v2'))

from scangoal import chronology, event_audit  # noqa: E402
from scangoal.discovery import engine_v2  # noqa: E402
from scangoal.discovery.replay import Budget, Decision, Replay  # noqa: E402
from scangoal.discovery.v3 import engine, layers, protocol, riskset  # noqa: E402


def contract(line, direction):
    return dict(period='regulation', market_key='match_goals', selection=f'{direction.capitalize()} {line}',
                direction=direction, line=line, source='Stake', phase='pre', teams=['H', 'A'])


def fair_bias():
    rows = []
    for mean in (2.2, 2.6, 3.0):
        law = [exp(-mean) * mean ** g / factorial(g) for g in range(20)]
        for line in ('1.75', '2.25', '2.75', '3.25', '2.5', '3.0'):
            for direction in ('over', 'under'):
                event = contract(line, direction)
                outcomes = [(p, event_audit.payoff(event, (g, 0))) for g, p in enumerate(law)]
                win = sum(p * max(0., w) for p, w in outcomes)
                lose = sum(p * max(0., -w) for p, w in outcomes)
                odds = 1 + lose / win
                decisive = [(p, w) for p, w in outcomes if w != 0]
                total = sum(p for p, _ in decisive)
                v30 = sum(p * ((w > 0) - 1 / odds) for p, w in decisive) / total
                v31 = sum(p * layers.values(dict(status='settled', payoff=w), odds)['prediction'][0]
                          for p, w in decisive) / total
                rows.append(dict(goal_mean=mean, line=line, direction=direction, fair_odds=round(odds, 4),
                                 half_settlement_probability=round(sum(p for p, w in outcomes if abs(w) == .5), 4),
                                 v3_0_expected_residual=round(v30, 5), v3_1_expected_residual=round(v31, 12)))
    return rows


def count(replay, rules=()):
    anchors = Counter()
    for at, e in replay.anchors:
        payoff = replay.label(Decision(e, at, replay.kickoff_known(e['fixture_id'], at), 'case', (), ()))['payoff']
        anchors['half' if payoff in (.5, -.5) else 'settled_other' if payoff is not None else 'unsettled'] += 1
    cases = pairs = 0
    design = riskset.RiskSet(replay) if rules else None
    for rule in rules:
        cohort = design.run(rule, Budget(engine.MAX_WORK))
        cases += sum(replay.label(c)['payoff'] in (.5, -.5) for c in cohort.cases)
        pairs += sum(replay.label(d)['payoff'] in (.5, -.5) for p in cohort.pairs for d in p[:2])
    return dict(anchors=dict(anchors), half_in_v3_cases=cases if rules else None,
                half_in_v3_pair_decisions=pairs if rules else None)


def synthetic():
    import world
    import scientific_audit as v2audit
    out = {}
    for name in ('null', 'positive_20pp'):
        replay, _ = world.build(world.scenarios()[name])
        out['v3_world/' + name] = count(replay, [world.RULE])
    full, _, _ = v2audit.recorded_world(83)
    out['v1_v2_bank/seed_83'] = count(full)
    return out


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--as-of', required=True)
    args = cli.parse_args(argv)
    replay = Replay(event_audit.build(args.data_dir, chronology.parse_utc(args.as_of), quote_only=True))
    _, records = engine_v2.discover(replay)
    real = count(replay, [r['rule'] for r in records if 'rule' in r])
    print(json.dumps(dict(protocol_v3_0=protocol.V3_0_SHA256, protocol_v3_1=protocol.PROTOCOL_SHA256,
                          fair_price_bias=fair_bias(), real_snapshot=real, synthetic=synthetic(),
                          verdict=engine.VERDICT), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

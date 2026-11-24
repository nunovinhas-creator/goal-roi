"""Exploratory v3 assessment of the real offline snapshot (no confirmatory value).

Mines with the v2 miner, re-identifies every candidate under v3, and assesses
it with the protocol risk set and with the relaxed non-protocol design. Only
aggregates are printed: counts, layer estimates, p-values and gates. Nothing
is registered, published or written under data/.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reevaluate import RELAXED  # noqa: E402
from scangoal import chronology, event_audit  # noqa: E402
from scangoal.discovery import engine_v2  # noqa: E402
from scangoal.discovery.replay import Budget, Replay  # noqa: E402
from scangoal.discovery.v3 import engine, protocol, riskset  # noqa: E402


def summary(evidence):
    layers = evidence['layers']
    return dict(counts=evidence['counts'], balance=evidence['balance'],
                layers={n: dict(pairs=layers[n]['pairs'], mean=layers[n]['mean_observed'],
                                bounds=layers[n]['mean_bounds'], p=layers[n]['p'],
                                insufficiency=layers[n]['insufficiency']) for n in engine.LAYERS},
                executable=layers['executable']['status'])


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--as-of', required=True)
    args = cli.parse_args(argv)
    replay = Replay(event_audit.build(args.data_dir, chronology.parse_utc(args.as_of), quote_only=True))
    _, records = engine_v2.discover(replay)
    report = engine.discover(replay, candidates=[r for r in records if 'rule' in r])
    relaxed = riskset.RiskSet(replay, **RELAXED)
    rows = []
    totals = Counter()
    for row in report['investigations']:
        item = dict(id=row['hypothesis_id'], source_id=row.get('source_id'))
        if 'evidence' in row:
            item['protocol'] = summary(row['evidence'])
            totals['protocol_pairs'] += row['evidence']['counts'].get('matched_pairs', 0)
            for key, value in row['evidence']['counts'].items():
                totals['protocol_' + key] += value
        evidence, _ = engine.assess(replay, row['rule'], budget=Budget(engine.MAX_WORK), design=relaxed)
        item['relaxed_non_protocol'] = summary(evidence)
        totals['relaxed_pairs'] += evidence['counts'].get('matched_pairs', 0)
        item['relaxed_exploratory_statistics'] = None
        rows.append((item, evidence))
    verdicts = engine.decide([e for _, e in rows], 1, closed=True)
    for (item, _), verdict in zip(rows, verdicts):
        item['relaxed_exploratory_statistics'] = dict(status=verdict['status'], claims=verdict['claims'])
    print(json.dumps(dict(as_of_utc=report['as_of_utc'], snapshot_sha256=report['snapshot_sha256'],
                          protocol_sha256=protocol.PROTOCOL_SHA256, candidates=len(rows),
                          proposed_registration=len(report['proposed_registration']), totals=dict(totals),
                          relaxed_states=dict(Counter(v['status'] for v in verdicts)),
                          hypotheses=[item for item, _ in rows], confirmatory=False, verdict=engine.VERDICT),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str))


if __name__ == '__main__':
    main()

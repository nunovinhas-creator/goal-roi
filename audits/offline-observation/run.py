"""Reproduce the PR G audit, offline simulated observation and discovery holdout."""

import argparse
import hashlib
import json
from pathlib import Path

from scangoal.discovery.v32 import engine
from scangoal.offline_pilot import assurance, discovery, pilot

ROOT = Path(__file__).resolve().parents[2]


def frozen():
    record = json.loads(Path(__file__).with_name('frozen.json').read_bytes())
    changed = [path for path, claimed in record['sha256'].items()
               if not (ROOT / path).is_file() or hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != claimed]
    if changed:
        raise ValueError(f'frozen file differs: {changed}')
    return dict(commit=record['commit'], checked_files=len(record['sha256']))


def run(*, details=False, try_model=True):
    pinned = frozen()
    challenges = assurance.run()
    captures = json.loads(Path(__file__).with_name('recorded-books.json').read_bytes())
    if captures['schema'] != 'synthetic-book-capture-v1':
        raise ValueError('incompatible offline capture schema')
    sample = pilot.temporary_run(captures=captures['responses'])
    brain = discovery.run(try_model=try_model)
    slot_count = sample['slots']['planned']
    accounted = slot_count - sample['slots']['reasons'].get('lost_receipt', 0)
    coverage = accounted / slot_count if slot_count else 0.
    offline = (coverage >= .95 and challenges['check_count'] >= 10
               and sample['network_calls'] == sample['orders_sent'] == sample['notifications_sent'] == 0
               and sample['book_continuous_coverage_spans'] == 0 and sample['confirmatory_pairs'] == 0)
    gates = dict(
        offline_protocol_integrity=True, slot_accounting_threshold=.95, slot_accounting_fraction=coverage,
        proof_fail_closed_in_synthetic_replay=challenges['check_count'] >= 10,
        verified_real_catalogue_and_book_envelopes=False,
        real_terms_and_geography_reviewed=False,
        independent_real_stream_attestation=False,
        discovery_transfer_gate=brain['transfer_gate'],
    )
    if not details:
        sample = {key: value for key, value in sample.items() if key not in ('observations', 'comparisons')}
        for metrics in (brain['deterministic'], brain['llm']['metrics']):
            if metrics is not None:
                metrics.pop('all_candidates')
    real = all(gates[key] for key in ('verified_real_catalogue_and_book_envelopes',
                                     'real_terms_and_geography_reviewed'))
    return dict(schema='goal_roi_pr_g_executable_report_v1',
                reviews=['https://github.com/nunovinhas-creator/goal-roi/pull/87#pullrequestreview-6041697734'],
                immutable_protocols=pinned, adversarial_audit=challenges, offline_pilot=sample,
                discovery=brain, gates=gates, decision=dict(offline='GO' if offline else 'NO-GO',
                    request_real_pilot='GO' if offline and real else 'NO-GO',
                    confirmation='NO-GO', execution='not_demonstrated'),
                confirmatory=False, verdict=engine.VERDICT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--details', action='store_true', help='include every slot, comparison and candidate')
    parser.add_argument('--deterministic-only', action='store_true', help='replay the versioned benchmark without an optional local model')
    args = parser.parse_args()
    print(json.dumps(run(details=args.details, try_model=not args.deterministic_only),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))

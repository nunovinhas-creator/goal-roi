"""Run the sealed I miner over the repository's local journal in read-only mode."""

import argparse
import json
from pathlib import Path

from evaluate import ROOT, verified_lock
from scangoal import chronology, event_intelligence as events, store
from scangoal.discovery.experimental_i import miner
from scangoal.discovery.v32 import engine, source
from scangoal.observability.timeline import invalid_completed_receipt


def latest_receipt(directory):
    clocks = (chronology.parse_utc(row.get('collected_at_utc'))
              for _, row in store.message_rows(directory / 'messages.jsonl')
              if row.get('receipt_version') == 1 and not invalid_completed_receipt(row))
    return max((clock for clock in clocks if clock is not None), default=None)


def _timestamp(text):
    if (clock := chronology.parse_utc(text)) is None:
        raise argparse.ArgumentTypeError('timestamp requires an explicit timezone')
    return clock


def investigate(directory, as_of=None):
    lock, digest = verified_lock()
    cutoff = latest_receipt(directory) if as_of is None else as_of
    if cutoff is None:
        raise ValueError('no completed local receipt with an explicit clock')
    tape = source.journal(directory, cutoff)
    output = miner.discover(tape, work=lock['budgets']['mining'])
    return dict(output, cutoff_utc=events.iso(cutoff), lock_sha256=digest,
                journal_sha256=tape.journal_sha256, snapshot_sha256=tape.snapshot_sha256,
                verdict=engine.VERDICT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--as-of', type=_timestamp)
    args = parser.parse_args()
    print(json.dumps(investigate(args.data_dir, args.as_of), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))

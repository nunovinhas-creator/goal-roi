"""Run the sealed experimental miner over observations already stored locally."""

import argparse
import json
from pathlib import Path

from run import freeze
from scangoal import chronology, event_intelligence as events, store
from scangoal.discovery.experimental_h import miner, research
from scangoal.discovery.v32 import engine, source
from scangoal.observability.timeline import invalid_completed_receipt

ROOT = Path(__file__).resolve().parents[2]


def latest_completed(directory):
    clocks = (chronology.parse_utc(row.get('collected_at_utc'))
              for _, row in store.message_rows(directory / 'messages.jsonl')
              if row.get('receipt_version') == 1 and not invalid_completed_receipt(row))
    return max((clock for clock in clocks if clock is not None), default=None)


def investigate(directory, as_of=None, *, try_model=True):
    lock, fingerprint = freeze()
    cutoff = latest_completed(directory) if as_of is None else as_of
    if cutoff is None:
        raise ValueError('no completed receipt with a verified clock in the local journal')
    tape = source.journal(directory, cutoff)
    result = miner.discover(tape, work=lock['work']['miner'])
    local = research.investigate(tape, research.available_model() if try_model else None)
    return dict(result, algorithm_lock_sha256=fingerprint, as_of_utc=events.iso(cutoff),
                snapshot_sha256=tape.snapshot_sha256, journal_sha256=tape.journal_sha256,
                llm=local, verdict=engine.VERDICT)


def _timestamp(text):
    parsed = chronology.parse_utc(text)
    if parsed is None:
        raise argparse.ArgumentTypeError('timestamp requires an explicit timezone')
    return parsed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--as-of', type=_timestamp, help='override the last completed receipt, with timezone')
    parser.add_argument('--deterministic-only', action='store_true', help='omit optional local inference')
    args = parser.parse_args()
    print(json.dumps(investigate(args.data_dir, args.as_of, try_model=not args.deterministic_only),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))

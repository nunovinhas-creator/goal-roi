"""Single-run guard for the future blind evaluation (prepared in K0, not executed).

- the protocol file must match the hash recorded in the run request;
- each run writes an immutable record under runs/; a second run is only a
  reproduction and must produce byte-identical output, otherwise the
  evaluation is invalid and the verdict is NO-GO;
- results with abstentions, ineligible banks or zero detections are scored by
  the frozen rules below and can never be dropped or re-run.
"""

from fractions import Fraction
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Refused(RuntimeError):
    pass


def start(protocol_sha256, runs=ROOT / 'runs'):
    if sha(ROOT / 'protocol.json') != protocol_sha256:
        raise Refused('protocol_changed_after_commitment')
    runs = Path(runs)
    first = runs / 'run-001.json'
    return 'reproduction' if first.exists() else 'first'


def record(output_bytes, mode, runs=ROOT / 'runs'):
    runs = Path(runs)
    runs.mkdir(exist_ok=True)
    first = runs / 'run-001.json'
    if mode == 'first':
        if first.exists():
            raise Refused('first_run_already_recorded')
        first.write_bytes(output_bytes)
        return 'recorded'
    if first.read_bytes() != output_bytes:
        (runs / 'reproduction-mismatch.json').write_bytes(output_bytes)
        return 'invalid_reproduction_mismatch'
    return 'reproduced'


def gate(scores, thresholds):
    """Frozen scoring of one (family, mining seed, validation seed) evaluation.

    An abstention, an ineligible bank or zero detections fails the gate. Targets
    always stay in the denominator; precision without detections is undefined
    and never treated as perfect.
    """
    if scores.get('ineligible') or scores.get('abstained') or not scores['detections']:
        return False
    precision = Fraction(scores['exact_terminals'], scores['detections'])
    terminal = Fraction(scores['exact_terminals'], scores['targets'])
    paths = Fraction(scores['exact_paths'], scores['targets'])
    return (precision >= Fraction(str(thresholds['precision']))
            and terminal >= Fraction(str(thresholds['terminal_recall']))
            and paths >= Fraction(str(thresholds['exact_path_recall'])))


def decide(evaluations, matrix, thresholds):
    """GO only if every cell of the full matrix exists and passes. Missing cells count as failures."""
    expected = {(f['slot'], m, v) for f in matrix['families']
                for m in range(1, matrix['mining_seeds'] + 1) for v in range(1, matrix['validation_seeds'] + 1)}
    present = {(e['family'], e['mining'], e['validation']): e for e in evaluations}
    if set(present) - expected:
        raise Refused('evaluation_outside_registered_matrix')
    passed = all(cell in present and gate(present[cell], thresholds) for cell in sorted(expected))
    return 'GO' if passed else 'NO-GO'

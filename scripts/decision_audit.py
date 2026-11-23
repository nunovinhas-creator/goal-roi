"""Print the offline temporal audit from repository-local evidence."""
import argparse
import json
from pathlib import Path
import subprocess

from scangoal import chronology, decision_audit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='ISO 8601 cutoff with explicit UTC offset')
    parser.add_argument('--hypothesis', action='append', default=[], metavar='ID',
                        help='registered hypothesis to compare in addition to the two frozen strategies (repeatable)')
    parser.add_argument('--registration-ref', default='HEAD', help='Git commit/ref of the registry history')
    parser.add_argument('--summary', action='store_true', help='omit individual game decisions from JSON output')
    args = parser.parse_args()
    as_of = chronology.parse_utc(args.as_of)
    if as_of is None:
        parser.error('--as-of requires a valid explicit UTC offset')
    ref = subprocess.run(
        ['git', '--no-optional-locks', '--no-replace-objects', '-C', str(ROOT), 'rev-parse',
         '--verify', '--end-of-options', f'{args.registration_ref}^{{commit}}'],
        capture_output=True, text=True,
    )
    if ref.returncode:
        parser.error('--registration-ref must resolve to a Git commit')
    result = decision_audit.build(as_of, root=ROOT, hypotheses=args.hypothesis,
                                  registration_ref=ref.stdout.strip(), summary=args.summary)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

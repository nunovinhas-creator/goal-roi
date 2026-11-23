"""Compare complete production reports from two revisions on pinned data snapshots."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ('scangoal', 'audits/strategy-lab/shortlist-v1.json',
                'audits/opportunity-lab/polymarket-over25-v1.json')
RUN_REPORT = """
import json
import sys
from scangoal import chronology, report

print(json.dumps(report.build(as_of=chronology.parse_utc(sys.argv[1])), allow_nan=False))
"""


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def commit(ref):
    return git('rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').decode().strip()


def extract(ref, destination, *paths):
    archive = BytesIO(git('archive', ref, *paths))
    with tarfile.open(fileobj=archive) as files:
        files.extractall(destination, filter='data')


def build(source, data, as_of):
    raw = subprocess.check_output(
        [sys.executable, '-B', '-E', '-c', RUN_REPORT, as_of], cwd=source,
        env=dict(os.environ, SCANGOAL_DATA_DIR=str(data)),
    )
    return json.loads(raw)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', help='baseline source revision')
    parser.add_argument('after', help='candidate source revision')
    parser.add_argument('snapshots', nargs='+', help='data revisions with a published docs/report.json clock')
    args = parser.parse_args()

    before, after = commit(args.before), commit(args.after)
    snapshots = [commit(ref) for ref in args.snapshots]
    with TemporaryDirectory(prefix='goal-roi-report-parity-') as directory:
        root = Path(directory)
        sources = (root / 'before', root / 'after')
        for source, ref in zip(sources, (before, after)):
            extract(ref, source, *SOURCE_PATHS)

        with ThreadPoolExecutor(max_workers=4) as pool:
            comparisons = []
            for index, snapshot in enumerate(snapshots):
                archived_report = json.loads(git('show', f'{snapshot}:docs/report.json'))
                as_of = archived_report['generated_at_utc']
                fixture = root / f'snapshot-{index}'
                extract(snapshot, fixture, 'data')
                left, right = (pool.submit(build, source, fixture / 'data', as_of) for source in sources)
                comparisons.append((snapshot, as_of, left, right))

            for snapshot, as_of, left, right in comparisons:
                baseline, candidate = left.result(), right.result()
                if canonical(baseline) != canonical(candidate):
                    changed = sorted(key for key in baseline.keys() | candidate.keys()
                                     if key not in baseline or key not in candidate
                                     or canonical(baseline[key]) != canonical(candidate[key]))
                    raise SystemExit(f'{snapshot} at {as_of}: report differs in {", ".join(changed)}')
                shortlist = candidate['brain']['strategy_board']['shortlist']
                print(json.dumps({'snapshot': snapshot, 'as_of': as_of, 'fields_equal': len(candidate),
                                  'messages': candidate['messages'], 'shortlist_status': shortlist['status'],
                                  'report_sha256': hashlib.sha256(canonical(candidate)).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()

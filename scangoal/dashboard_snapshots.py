"""V6 dashboard snapshots, published together with the report. INACTIVE unless GOAL_ROI_V6_SNAPSHOTS is 'on'.

    GOAL_ROI_V6_SNAPSHOTS=on python -m scangoal.dashboard_snapshots

Reads docs/report.json (the copy that is published), data/prospective_report.json and the frozen protocol, and writes only
the seven files of docs/v6/snapshots/ (six snapshots and the manifest). Nothing here changes the report, the prospective
ledger, the protocol or any delivery state.

Safety argument:
- Stable names. Each snapshot keeps the same path in every revision (dashboard-<name>.json), so Git stores successive
  revisions as deltas of the same path. Measured on 60 real report revisions: +4.0 KiB per revision against +18.9 KiB with
  content-addressed names (audits/v6-intelligence/snapshots-integration.md).
- Coherence: the manifest records the SHA-256 of docs/report.json and of every snapshot. If the report changes while the
  snapshots are built, the build is discarded and the previous manifest stays.
- Atomicity per revision: files are written to temporary names and renamed; the manifest is renamed last. Report,
  snapshots and manifest are published in the same Git commit (scangoal.publication). A reader that sees files from
  another revision (CDN cache) detects the SHA-256 mismatch and uses the full report for that cycle; the dashboard asks
  for each file with the expected hash in the query string, so a cached copy of another revision is not reused.
- Concurrency: a file lock serialises the collection step and the fast reads.
- Failure: any error, including a malformed manifest, leaves the previous manifest in place. The dashboard compares its
  revision with the report's and falls back to the full report when the snapshots are behind.
- Exit codes: 0 done or inactive, 3 failed (the workflow step has continue-on-error and never blocks the collection).
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENV = 'GOAL_ROI_V6_SNAPSHOTS'
OUT = Path('docs/v6/snapshots')
MANIFEST = 'dashboard-manifest.json'
NAMES = ('summary', 'signals', 'performance', 'science', 'operations', 'learning')
FILES = tuple('dashboard-%s.json' % name for name in NAMES)
SCHEMA = 'goal_roi_dashboard_manifest_v6_1'
BUILDER = Path('scripts/build_dashboard_snapshots.cjs')
TIMEOUT_SECONDS = 90
SHA = re.compile(r'[0-9a-f]{64}')
# Files of an earlier layout (content-addressed names) that may be left in the directory; removed on the next build.
LEGACY = re.compile(r'dashboard-(%s)\.[0-9a-f]{16}\.json' % '|'.join(NAMES))


class SnapshotError(RuntimeError):
    pass


def enabled(environ=os.environ):
    return environ.get(ENV) == 'on'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def _locked(root):
    name = 'goal-roi-snapshots-%s.lock' % hashlib.sha256(str(root).encode()).hexdigest()[:16]
    with open(Path(tempfile.gettempdir()) / name, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def verify(manifest, directory, report_sha):
    """Raises SnapshotError for any manifest that is not exactly the expected shape, or that does not match the files."""
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise SnapshotError('unexpected manifest schema')
    if manifest.get('report_sha256') != report_sha:
        raise SnapshotError('manifest does not describe the published report')
    files = manifest.get('files')
    if not isinstance(files, dict) or sorted(files) != sorted(NAMES):
        raise SnapshotError('manifest does not list exactly the six snapshots')
    for name in NAMES:
        entry = files[name]
        if (not isinstance(entry, dict) or entry.get('path') != 'dashboard-%s.json' % name
                or not isinstance(entry.get('sha256'), str) or not SHA.fullmatch(entry['sha256'])
                or type(entry.get('bytes')) is not int or entry['bytes'] < 0):
            raise SnapshotError('malformed manifest entry: %s' % name)
        body = Path(directory) / entry['path']
        if not body.is_file() or _sha(body) != entry['sha256'] or body.stat().st_size != entry['bytes']:
            raise SnapshotError('snapshot does not match its manifest entry: %s' % name)


def build(root='.', *, runner=subprocess.run, node=None):
    """Builds and installs one revision of snapshots. Returns 'published', 'unchanged' or 'skipped_no_node'."""
    root = Path(root).resolve()
    node = node or shutil.which('node')
    if not node:
        return 'skipped_no_node'
    report = root / 'docs' / 'report.json'
    if not report.is_file():
        raise SnapshotError('docs/report.json is missing')
    out = root / OUT
    with _locked(root), tempfile.TemporaryDirectory(prefix='goal-roi-snapshots-') as tmp:
        staging = Path(tmp)
        before = _sha(report)
        result = runner([node, str(root / BUILDER), '--report', str(report),
                         '--prospective', str(root / 'data' / 'prospective_report.json'),
                         '--protocol', str(root / 'scangoal' / 'prospective_protocol.json'),
                         '--out', str(staging)],
                        cwd=root, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise SnapshotError('builder failed: %s' % (result.stderr or result.stdout).strip()[:300])
        try:
            manifest = json.loads((staging / MANIFEST).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise SnapshotError('builder wrote no readable manifest') from exc
        verify(manifest, staging, before)
        if _sha(report) != before:
            raise SnapshotError('the report changed while the snapshots were built')
        out.mkdir(parents=True, exist_ok=True)
        current = out / MANIFEST
        try:
            previous = json.loads(current.read_text(encoding='utf-8')) if current.is_file() else None
        except ValueError:
            previous = None
        if isinstance(previous, dict) and previous.get('report_sha256') == before and previous.get('files') == manifest['files']:
            return 'unchanged'
        # 1. the six snapshots, each through a temporary name and an atomic rename
        for name in FILES:
            temporary = out / (name + '.tmp')
            shutil.copyfile(staging / name, temporary)
            os.replace(temporary, out / name)
        # 2. the manifest last
        temporary = out / (MANIFEST + '.tmp')
        shutil.copyfile(staging / MANIFEST, temporary)
        os.replace(temporary, current)
        # 3. nothing else stays in the directory (earlier layouts, orphan temporaries)
        for path in out.iterdir():
            if path.name not in FILES and path.name != MANIFEST and (LEGACY.fullmatch(path.name) or path.name.endswith('.tmp')):
                path.unlink()
        return 'published'


def publish_quietly(root='.', environ=os.environ):
    """For the fast reads: never raises, never blocks the report or the notices."""
    if not enabled(environ):
        return 'inactive'
    try:
        return build(root)
    except Exception as exc:  # noqa: BLE001 - a snapshot failure must never stop the collection
        print('DASHBOARD_SNAPSHOTS_FAILED', type(exc).__name__, str(exc)[:200])
        return 'failed'


def main():
    if not enabled():
        print('DASHBOARD_SNAPSHOTS_SKIPPED inactive (set %s=on to enable)' % ENV)
        return 0
    try:
        print('DASHBOARD_SNAPSHOTS', build())
        return 0
    except Exception as exc:  # noqa: BLE001 - any failure is reported with exit code 3, never a traceback
        print('DASHBOARD_SNAPSHOTS_FAILED', type(exc).__name__, str(exc)[:300])
        return 3


if __name__ == '__main__':
    sys.exit(main())

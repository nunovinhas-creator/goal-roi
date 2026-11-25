"""V6 dashboard snapshots, published together with the report. INACTIVE unless GOAL_ROI_V6_SNAPSHOTS is 'on'.

    GOAL_ROI_V6_SNAPSHOTS=on python -m scangoal.dashboard_snapshots

Reads docs/report.json (the copy that is published), data/prospective_report.json and the frozen protocol, and writes only
docs/v6/snapshots/. Nothing here changes the report, the prospective ledger, the protocol or any delivery state.

Safety argument:
- Coherence: the manifest records the SHA-256 of docs/report.json. If the report changes while the snapshots are built,
  the build is discarded and the previous manifest stays.
- Atomicity per revision: snapshot files are content-addressed (dashboard-<name>.<sha256 prefix>.json) and written before
  the manifest, which is replaced last with an atomic rename. A reader sees either the old or the new manifest, and every
  file either names resolves. The previous generation is kept, so a manifest cached by the CDN still resolves.
- Concurrency: a file lock serialises the collection step and the fast reads. Git publication (scangoal.publication)
  commits snapshots, manifest and report in the same commit.
- Failure: any error leaves the previous manifest in place. The dashboard compares its revision with the report's and
  falls back to the full report when the snapshots are behind, so a failed build never shows wrong data.
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
BUILDER = Path('scripts/build_dashboard_snapshots.cjs')
TIMEOUT_SECONDS = 90
FILE = re.compile(r'dashboard-(%s)\.([0-9a-f]{16})\.json' % '|'.join(NAMES))


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


def _verify(manifest, staging, report_sha):
    if manifest.get('schema') != 'goal_roi_dashboard_manifest_v6_1':
        raise SnapshotError('unexpected manifest schema')
    if manifest.get('report_sha256') != report_sha:
        raise SnapshotError('manifest does not describe the published report')
    files = manifest.get('files') or {}
    if sorted(files) != sorted(NAMES):
        raise SnapshotError('manifest does not list every snapshot')
    for name, entry in files.items():
        match = FILE.fullmatch(entry.get('path', ''))
        if not match or match[1] != name or not entry['sha256'].startswith(match[2]):
            raise SnapshotError('snapshot name is not content-addressed: %s' % name)
        body = staging / entry['path']
        if _sha(body) != entry['sha256'] or body.stat().st_size != entry['bytes']:
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
                         '--out', str(staging), '--hashed'],
                        cwd=root, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise SnapshotError('builder failed: %s' % (result.stderr or result.stdout).strip()[:300])
        manifest = json.loads((staging / MANIFEST).read_text(encoding='utf-8'))
        _verify(manifest, staging, before)
        if _sha(report) != before:
            raise SnapshotError('the report changed while the snapshots were built')
        out.mkdir(parents=True, exist_ok=True)
        current = out / MANIFEST
        previous = None
        if current.is_file():
            try:
                previous = json.loads(current.read_text(encoding='utf-8'))
            except ValueError:
                previous = None
        if previous and previous.get('report_sha256') == before and previous.get('files') == manifest['files']:
            return 'unchanged'
        # 1. content-addressed files (never overwrite a different body under the same name)
        for entry in manifest['files'].values():
            target = out / entry['path']
            if target.is_file() and _sha(target) == entry['sha256']:
                continue
            temporary = target.with_name(target.name + '.tmp')
            shutil.copyfile(staging / entry['path'], temporary)
            os.replace(temporary, target)
        # 2. the manifest last, atomically
        temporary = current.with_name(MANIFEST + '.tmp')
        shutil.copyfile(staging / MANIFEST, temporary)
        os.replace(temporary, current)
        # 3. keep this generation and the previous one; remove older snapshot files only
        keep = {e['path'] for e in manifest['files'].values()}
        if previous and isinstance(previous.get('files'), dict):
            keep |= {e.get('path') for e in previous['files'].values() if isinstance(e, dict)}
        for path in out.iterdir():
            if FILE.fullmatch(path.name) and path.name not in keep:
                path.unlink()
            elif path.name.endswith('.tmp'):
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
    except (SnapshotError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('DASHBOARD_SNAPSHOTS_FAILED', type(exc).__name__, str(exc)[:300])
        return 3


if __name__ == '__main__':
    sys.exit(main())

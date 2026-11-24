"""Run a B process in an ephemeral Linux sandbox and record verifiable logs.

Isolation (Linux namespaces via util-linux `unshare`, run as root):
  - new network namespace: only a down loopback, no route, no proxy;
  - new mount namespace with a fresh tmpfs root, remounted read-only;
    /usr is bind-mounted read-only (interpreter and standard library only),
    /kit read-only (the exported kit), /out the only writable path;
  - new PID, IPC and UTS namespaces; no /proc, /home, /root, /tmp or repository;
  - empty environment (`env -i`), so no token, credential or proxy variable;
  - python -I (isolated mode: no user site, no PYTHON* variables).
The record holds hashes of the kit, runner, probe and outputs, the exact command,
timings and exit code, chained by SHA-256. It does not prove who wrote the code.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
K0 = HERE.parent
sys.path.insert(0, str(K0 / 'tools'))
import commitment  # noqa: E402

KIT_FILES = {'bank_author_kit': K0 / 'bank_author_kit', 'protocol.json': K0 / 'protocol.json',
             'tools/commitment.py': K0 / 'tools/commitment.py'}

SANDBOX = r'''
set -eu
R="$1"; KIT="$2"; OUT="$3"; ENTRY="$4"; PORT="$5"
mount --make-rprivate /
mount -t tmpfs -o size=16m,mode=0755 none "$R"
mkdir -p "$R/usr" "$R/kit" "$R/out"
ln -s usr/bin "$R/bin"; ln -s usr/lib "$R/lib"; ln -s usr/lib64 "$R/lib64"
mount --bind /usr "$R/usr"; mount -o remount,bind,ro,nosuid,nodev "$R/usr"
mount --bind "$KIT" "$R/kit"; mount -o remount,bind,ro,nosuid,nodev,noexec "$R/kit"
mount --bind "$OUT" "$R/out"; mount -o remount,bind,rw,nosuid,nodev,noexec "$R/out"
mount -o remount,ro "$R"
exec chroot "$R" /usr/bin/env -i PATH=/usr/local/bin:/usr/bin LANG=C.UTF-8 \
    /usr/local/bin/python3 -I "$ENTRY" /out "$PORT"
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_kit(target):
    for name, source in KIT_FILES.items():
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy2(source, dest)
    shutil.copy2(HERE / 'probe.py', target / 'probe.py')


def run(record_path, *, sandbox=True):
    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or 'http://127.0.0.1:8080'
    port = proxy.rsplit(':', 1)[-1].strip('/')
    with tempfile.TemporaryDirectory(prefix='goal-roi-b-') as tmp:
        tmp = Path(tmp)
        kit, out, root = tmp / 'kit', tmp / 'out', tmp / 'root'
        kit.mkdir(); out.mkdir(); root.mkdir()
        stage_kit(kit)
        kit_before, kit_files = commitment.tree_digest(kit)
        if sandbox:
            command = ['unshare', '--net', '--mount', '--pid', '--ipc', '--uts', '--fork', '--kill-child',
                       'sh', '-c', SANDBOX, 'sandbox', str(root), str(kit), str(out), '/kit/probe.py', port]
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'}
        else:  # negative control: same probe, no isolation, inherited environment
            command = [sys.executable, '-I', str(kit / 'probe.py'), str(out), port, 'control']
            env = dict(os.environ)
        started = datetime.now(timezone.utc)
        done = subprocess.run(command, capture_output=True, env=env, timeout=120,
                              cwd=str(out) if not sandbox else '/')
        ended = datetime.now(timezone.utc)
        kit_after, _ = commitment.tree_digest(kit)
        probe = json.loads((out / 'probe.json').read_text()) if (out / 'probe.json').exists() else None
        out_digest, out_files = commitment.tree_digest(out)
        record = dict(
            schema='goal_roi_b_isolation_run_v1', mode='sandbox' if sandbox else 'negative_control',
            started_utc=started.isoformat(), ended_utc=ended.isoformat(), exit_code=done.returncode,
            command=command[:-6] + ['<sandbox script>', 'sandbox', '<root>', '<kit>', '<out>', '/kit/probe.py', '<proxy port>']
            if sandbox else ['python', '-I', '<kit>/probe.py', '<out>', '<proxy port>', 'control'],
            sandbox_script_sha256=hashlib.sha256(SANDBOX.encode()).hexdigest(),
            runner_sha256=sha(__file__), probe_sha256=sha(HERE / 'probe.py'),
            kit_tree_sha256_before=kit_before, kit_tree_sha256_after=kit_after, kit_files=kit_files,
            output_tree_sha256=out_digest, output_files=out_files,
            stdout_sha256=hashlib.sha256(done.stdout).hexdigest(), stderr=done.stderr.decode(errors='replace')[-2000:],
            probe=probe)
        record['record_sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    Path(record_path).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


def verdict(sandboxed, control):
    p, c = sandboxed['probe'] or {}, control['probe'] or {}
    checks = dict(
        sandbox_ran=sandboxed['exit_code'] == 0 and bool(p),
        no_repository=not any(p.get('repo_paths_visible', {}).values()) and not p.get('git_directories'),
        no_credentials_in_environment=not p.get('sensitive_environment'),
        no_dns=not p.get('dns_github', {}).get('ok', True),
        no_tcp=not p.get('tcp_ip_literal', {}).get('ok', True) and not p.get('tcp_loopback_proxy', {}).get('ok', True),
        read_only_except_out=(not p.get('write_root', {}).get('ok', True) and not p.get('write_kit', {}).get('ok', True)
                              and not p.get('write_usr', {}).get('ok', True) and p.get('write_out', {}).get('ok', False)),
        kit_unchanged=sandboxed['kit_tree_sha256_before'] == sandboxed['kit_tree_sha256_after'],
        no_bank_generated=p.get('template') == 'refused_no_mechanism',
        control_detects_leaks=bool(c) and any(c.get('repo_paths_visible', {}).values())
        and c.get('sensitive_environment', 0) > 0 and c.get('dns_github', {}).get('ok', False)
        and bool(c.get('git_directories')),
    )
    return dict(checks=checks, technical_isolation='PASS' if all(checks.values()) else 'FAIL',
                scientific_independence='NOT DEMONSTRATED',
                reason='the probe and runner were written by the algorithm-side session; no external bank author exists')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(HERE / 'runs'))
    args = parser.parse_args()
    Path(args.out).mkdir(exist_ok=True)
    s = run(Path(args.out) / 'sandbox.json', sandbox=True)
    c = run(Path(args.out) / 'control.json', sandbox=False)
    v = verdict(s, c) | dict(sandbox_record_sha256=s['record_sha256'], control_record_sha256=c['record_sha256'])
    (Path(args.out) / 'verdict.json').write_text(json.dumps(v, indent=2, sort_keys=True) + '\n')
    print(json.dumps(v, indent=2, sort_keys=True))

"""Technical isolation probe, run inside the B sandbox in place of a bank generator.

It generates no bank. It tries every forbidden channel and records the result,
then checks that the kit template still refuses to produce banks. Standard
library only. Writes one JSON file to /out.
"""

import json
import os
import socket
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else '/out'
PROXY_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080  # host proxy port, passed by the runner
# In the unisolated control the probe must never write outside its output directory.
CONTROL = len(sys.argv) > 3 and sys.argv[3] == 'control'
REPO_MARKERS = ('/home/user/goal-roi', '/home', '/root', '/tmp', '/workspace', '/proc/1/root')
TOKEN_WORDS = ('TOKEN', 'SECRET', 'KEY', 'PASSWORD', 'CRED', 'AUTH', 'PROXY')


def attempt(fn):
    try:
        return dict(ok=True, value=fn())
    except Exception as error:
        return dict(ok=False, error=type(error).__name__)


def connect(host, port):
    with socket.create_connection((host, port), timeout=3):
        return 'connected'


def write(path):
    with open(path, 'w') as handle:
        handle.write('probe')
    return path


def find_git(root, depth=4):
    found = []
    for base, dirs, _ in os.walk(root):
        if base.count(os.sep) - root.count(os.sep) >= depth:
            dirs[:] = []
        if '.git' in dirs:
            found.append(os.path.join(base, '.git'))
        dirs[:] = [d for d in dirs if d not in ('proc', 'sys', 'dev', 'usr')]
    return found


def main():
    result = dict(
        schema='goal_roi_b_isolation_probe_v1',
        uid=os.getuid(), cwd=os.getcwd(),
        root_entries=sorted(os.listdir('/')),
        repo_paths_visible={p: os.path.exists(p) for p in REPO_MARKERS},
        git_directories=find_git('/'),
        # Counts only: variable names of the host must not end up in a published record.
        sensitive_environment=sum(1 for k in os.environ if any(w in k.upper() for w in TOKEN_WORDS)),
        environment_count=len(os.environ),
        interfaces=attempt(lambda: [name for _, name in socket.if_nameindex()]),
        dns_github=attempt(lambda: socket.getaddrinfo('github.com', 443)[0][4][0]),
        tcp_ip_literal=attempt(lambda: connect('140.82.112.3', 443)),
        tcp_loopback_proxy=attempt(lambda: connect('127.0.0.1', PROXY_PORT)),
        write_root=dict(ok=None, skipped='control') if CONTROL else attempt(lambda: write('/probe-root')),
        write_kit=dict(ok=None, skipped='control') if CONTROL else attempt(lambda: write('/kit/probe')),
        write_usr=dict(ok=None, skipped='control') if CONTROL else attempt(lambda: write('/usr/probe')),
        write_out=attempt(lambda: write(os.path.join(OUT, 'write-check'))),
    )
    sys.path.insert(0, '/kit/bank_author_kit')
    try:
        import template
        template.build('F1', 'mining', 1, 1)
        result['template'] = 'produced_output'
    except NotImplementedError:
        result['template'] = 'refused_no_mechanism'
    except Exception as error:
        result['template'] = 'error:' + type(error).__name__
    with open(os.path.join(OUT, 'probe.json'), 'w') as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

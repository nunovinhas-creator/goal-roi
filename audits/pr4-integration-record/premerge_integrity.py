"""Verificação de integridade pré-merge (só leitura): situações operacionais, Telegram, certificação e protocolos."""
import hashlib
import json
import subprocess
import sys

M, H = sys.argv[1], sys.argv[2]
ROOT = '/home/user/goal-roi'


def git(*args, check=True):
    return subprocess.run(['git', '-C', ROOT, *args], capture_output=True, check=check)


def show(ref, path):
    return git('show', '%s:%s' % (ref, path)).stdout


out = {'M': M, 'H': H}
report = json.loads(show(M, 'data/report.json'))
state = json.loads(show(M, 'data/notify_state.json'))
brain = report['brain']
status_list = brain['status_list']
out['operational'] = {
    'status_list_count': len(status_list), 'status_list': status_list,
    'status_rules': brain.get('status_rules'),
    'counts': brain.get('counts'),
    'validation_state': brain['validation']['state'], 'ledger_valid': brain['validation'].get('ledger_valid'),
    'certified': brain['validation'].get('certified'),
    'certification_ledger_in_main': git('cat-file', '-e', '%s:data/certification_ledger.json' % M, check=False).returncode == 0,
    'validated_statuses': sorted(k for k, v in status_list.items() if v in ('forte', 'confirmada')),
}
markers = sorted((state.get('migrations') or {}).keys())
out['telegram'] = {
    'sent_total': state.get('sent_total'), 'watch_sent': len(state.get('watch_sent') or []),
    'migration_markers': markers,
    'statuses_equal_report_status_list': state.get('statuses') == status_list,
    'hello': state.get('hello'), 'last_run_utc': state.get('last_run_utc'),
}
protocols = [('v1', '9f7b2aa', 'audits/pr4-llm-protocol.md', '8b108bf91effb6701bd38e4a582e840804b86e1075b8af55b7976872ff1a5cb0'),
             ('v2', '25cd2d2', 'audits/pr4-llm-protocol-v2.md', '1f4a1001b5eefd90c9f6676a40bd63915900395314bd2243a4162d9433081339'),
             ('v3', '0766a13', 'audits/pr4-llm-protocol-v3.md', 'e3190be054c982c901dec00099ef2a433f43fc5103d82446a5b0846f361989a4'),
             ('v4', 'd48e3b7', 'audits/pr4-llm-protocol-v4.md', '055d7a62a2cb1a7edd6aae1880aa6be56d8a84f9090d85430b1ef932f21db167'),
             ('v5', 'bb1b07c', 'audits/pr4-llm-protocol-v5.md', '3ca34e70b955ae881658f3f0fa9823dda94aa90891f91bc749d4f1a04c6a2103')]
rows, ok = [], True
for name, short, path, sha in protocols:
    full = git('rev-parse', '--verify', '%s^{commit}' % short).stdout.decode().strip()
    ancestor = git('merge-base', '--is-ancestor', full, H, check=False).returncode == 0
    intro = [l.split()[0] for l in git('log', '--reverse', '--format=%H', H, '--', path).stdout.decode().split('\n') if l.strip()]
    at_commit = hashlib.sha256(show(full, path)).hexdigest()
    at_head = hashlib.sha256(show(H, path)).hexdigest()
    touched_after = intro[1:]
    good = ancestor and intro[:1] == [full] and at_commit == sha and at_head == sha and not touched_after
    ok = ok and good
    rows.append({'protocol': name, 'commit': full, 'file': path, 'sha256_expected': sha, 'sha256_at_commit': at_commit,
                 'sha256_at_H': at_head, 'introduced_by_this_commit': intro[:1] == [full], 'ancestor_of_H': ancestor,
                 'later_commits_touching_file': touched_after, 'ok': good})
code = git('show', '%s:scangoal/llm_eval.py' % H).stdout.decode()
out['protocols'] = rows
out['protocols_ok'] = ok
out['code_pins_v5'] = "PROTOCOL_SHA256 = '3ca34e70b955ae881658f3f0fa9823dda94aa90891f91bc749d4f1a04c6a2103'" in code
print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))

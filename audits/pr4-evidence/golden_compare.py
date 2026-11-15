"""PR 4: o código da PR não muda estados, certificação, Telegram nem dados face a `main`, com os mesmos dados.

Uso (raiz de um clone): python3.11 audits/pr4-evidence/golden_compare.py <commit main> <commit PR> <commit dos dados> [saída.json]
Extrai o código de cada commit e duas cópias de `data/` (git archive) para pastas temporárias; em cada uma corre, como na
produção, `report.build(register=True)` e depois `notify.messages` sobre uma cópia do estado Telegram (só calcula os textos;
nada é enviado, não há credenciais nem rede). Compara o relatório inteiro, os ficheiros de dados resultantes e as mensagens.
Diferenças permitidas: só as chaves acrescentadas pela PR 4 (`brain.track`, reescrito, e `brain.llm_evaluation`, nova).
Código de saída: 0 = só diferenças permitidas; 1 = outra diferença.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {('brain', 'track'), ('brain', 'llm_evaluation')}
CHILD = r'''
import copy, json, sys
from datetime import datetime
from scangoal import report, notify
from scangoal.store import data_dir
now = datetime.fromisoformat(sys.argv[1])
rep = report.build(now=now, register=True)
state = json.loads((data_dir() / 'notify_state.json').read_text(encoding='utf-8'))
msgs = notify.messages(copy.deepcopy(rep), state, now)
print(json.dumps({'report': rep, 'messages': msgs, 'state_after': state}, ensure_ascii=False, sort_keys=True, default=str))
'''


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout


def extract(ref, paths, dest):
    with tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', ref, *paths))) as tar:
        tar.extractall(dest, filter='data')


def run(code_ref, data_ref, now, tmp, name):
    code, data = Path(tmp) / name / 'code', Path(tmp) / name
    extract(code_ref, ['scangoal'], code)
    extract(data_ref, ['data'], data)
    env = dict(os.environ, PYTHONPATH=str(code), SCANGOAL_DATA_DIR=str(data / 'data'))
    for k in ('TG_BOT_TOKEN', 'TG_CHAT_ID', 'CLOUDFLARE_ACCOUNT_ID', 'MASTER_BRAIN_LLM_API_KEY'):
        env.pop(k, None)
    out = subprocess.run([sys.executable, '-c', CHILD, now], cwd=str(code), env=env, capture_output=True, check=True, text=True).stdout
    res = json.loads(out.strip().split('\n')[-1])
    res['files'] = {p.relative_to(data / 'data').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((data / 'data').rglob('*')) if p.is_file()}
    return res


def diff(a, b, path=()):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            p = path + (k,)
            if p in ALLOWED:
                continue
            if k not in a or k not in b:
                out.append('/'.join(map(str, p)) + (' only in PR' if k not in a else ' only in main'))
            else:
                out += diff(a[k], b[k], p)
        return out
    return [] if a == b else ['/'.join(map(str, path))]


def main(argv):
    main_ref, pr_ref, data_ref = [git('rev-parse', '--verify', '%s^{commit}' % r).decode().strip() for r in argv[:3]]
    now = git('show', '-s', '--format=%cI', data_ref).decode().strip()
    with tempfile.TemporaryDirectory() as tmp:
        a, b = run(main_ref, data_ref, now, tmp, 'main'), run(pr_ref, data_ref, now, tmp, 'pr')
    rep_diff = diff(a['report'], b['report'])
    out = {'main': main_ref, 'pr': pr_ref, 'data': data_ref, 'now': now,
           'report_differences_outside_allowed': rep_diff,
           'allowed_differences': sorted('/'.join(p) for p in ALLOWED),
           'status_list_main': a['report']['brain']['status_list'], 'status_list_pr': b['report']['brain']['status_list'],
           'counts_main': a['report']['brain']['counts'], 'counts_pr': b['report']['brain']['counts'],
           'validation_state': [a['report']['brain']['validation']['state'], b['report']['brain']['validation']['state']],
           'certified': [a['report']['brain']['validation']['certified'], b['report']['brain']['validation']['certified']],
           'telegram_messages_identical': a['messages'] == b['messages'], 'telegram_messages_count': [len(a['messages']), len(b['messages'])],
           'notify_state_identical': a['state_after'] == b['state_after'],
           'data_files_identical': a['files'] == b['files'],
           'data_files_differing': sorted(k for k in set(a['files']) | set(b['files']) if a['files'].get(k) != b['files'].get(k)),
           'certification_ledger_created': ['certification_ledger.json' in a['files'], 'certification_ledger.json' in b['files']],
           'pr_llm_evaluation': b['report']['brain'].get('llm_evaluation')}
    ok = (not rep_diff and out['telegram_messages_identical'] and out['notify_state_identical'] and out['data_files_differing'] == []
          and out['status_list_main'] == out['status_list_pr'] and out['validation_state'] == ['not_activated', 'not_activated']
          and out['certified'] == [[], []] and out['certification_ledger_created'] == [False, False])
    out['ok'] = ok
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if len(argv) > 3:
        Path(argv[3]).write_text(text, encoding='utf-8')
    print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

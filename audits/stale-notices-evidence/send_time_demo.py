"""Demonstração com tempo REAL (sem relógio injetado): sinal ao vivo com 29 min 59,5 s no planeamento e uma publicação git
que demora 1 s. Corre o `notify.deliver` da revisão indicada (worktree temporária) e diz se o aviso saiu.
Nada é enviado ao Telegram (remetente injetado) e nada em data/ é tocado.
Uso: python3 audits/stale-notices-evidence/send_time_demo.py <commit> [<commit> ...]
"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = r'''
import io, json, time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from scangoal import notify, brain
now = datetime.now(timezone.utc)
hid = 'Betfair | Faixa de odd = odd acima de 3,00 & Momento = durante o jogo'
w = {'date_utc': (now - timedelta(minutes=30) + timedelta(seconds=0.5)).isoformat(), 'kickoff_utc': None, 'phase': 'live',
     'match': 'Casa — Fora', 'match_id': 'demo', 'type': 'BETFAIR', 'market': '1X2', 'selection': '1', 'odds': 3.4, 'finished': False,
     'reasons': [{'id': hid, 'status': 'promissora', 'n': 40, 'games': 40, 'certified': False}]}
rep = {'brain': {'evidence_scope': 'eligible_bets_only', 'status_list': {hid: 'promissora'}, 'watch': [w], 'counts': {},
       'validation': {'state': 'not_activated'}, 'presentation': {'hypotheses': {}}}}
st = {'hello': True, 'sent_total': 1, 'watch_sent': [], 'statuses': {hid: 'promissora'}, 'migrations': {notify.STATUS_MIGRATION: {}}}
sent = []
def publish(key):
    time.sleep(1.0)            # pull/push lento
    return True
with redirect_stdout(io.StringIO()):
    st, done = notify.deliver(notify.MemoryStore(st), rep, st, now, lambda *a: sent.append(a[2]), 't', 'c', None, publish)
print(json.dumps({'sent': len(sent), 'outcome': [r['state'] for r in st['deliveries'].values()], 'events': [r['events'] for r in st['deliveries'].values()]}))
'''


def main(revs):
    for rev in revs:
        with tempfile.TemporaryDirectory() as d:
            if rev == 'WORKTREE':
                src = ROOT
            else:
                src = Path(d) / 'src'
                subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '-q', '--detach', str(src), rev], check=True)
            try:
                out = subprocess.run([sys.executable, '-c', CODE], cwd=src, capture_output=True, text=True, check=True).stdout.strip()
            finally:
                if rev != 'WORKTREE':
                    subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(src)], check=False)
            print(rev, out)


if __name__ == '__main__':
    main(sys.argv[1:])

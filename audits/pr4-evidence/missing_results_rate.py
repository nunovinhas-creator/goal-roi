"""PR 4 (protocolo v4, §3): fração real de jogos sem resultado comprovado até 3 dias depois da decisão.

Uso (raiz de um clone): python3.11 audits/pr4-evidence/missing_results_rate.py <commit de main>
Só leitura e sem rede. Conta, por família, os jogos cuja aposta comprometida foi decidida há mais de 3 dias e cujo resultado
não foi observado (com hora provada) nesse prazo: são os jogos que o v4 imputa de forma desfavorável. Mede o custo de poder da
imputação; não é um critério.
"""
import collections, io, os, subprocess, sys, tarfile, tempfile
from datetime import timedelta, datetime, timezone
from pathlib import Path
sys.path.insert(0, '.')
ref = sys.argv[1]
archive = subprocess.run(['git', 'archive', '--format=tar', ref, 'data'], capture_output=True, check=True).stdout
now = datetime.fromisoformat(subprocess.run(['git', 'show', '-s', '--format=%cI', ref], capture_output=True, text=True).stdout.strip())
with tempfile.TemporaryDirectory() as tmp:
    tarfile.open(fileobj=io.BytesIO(archive)).extractall(tmp, filter='data')
    os.environ['SCANGOAL_DATA_DIR'] = str(Path(tmp) / 'data')
    from scangoal import report, chronology, brain, insights
    from scangoal.eligibility import game_identity
    report.build(now=now)
    sig = report.LAST['signals']
games = collections.defaultdict(list)
for b in sig:
    if game_identity(b):
        games[game_identity(b)].append(b)
by_fam = collections.Counter(); miss = collections.Counter()
for key, rows in games.items():
    bet = brain.committed_bet(rows)
    if bet is None:
        continue
    dt = chronology.decision_time(bet)
    if dt + timedelta(days=3) > now:
        continue                     # dia ainda não fechado
    fam = insights.family(bet)
    known = chronology.result_known(bet)
    ok = bet.get('w') is not None and known is not None and known <= dt + timedelta(days=3)
    by_fam[fam] += 1
    miss[fam] += not ok
tot, m = sum(by_fam.values()), sum(miss.values())
print('games with closed window', tot, 'missing within 3 days', m, 'rate %.3f' % (m / tot if tot else 0))
for f in sorted(by_fam):
    print('  %-26s %4d  missing %4d  %.3f' % (f, by_fam[f], miss[f], miss[f] / by_fam[f]))

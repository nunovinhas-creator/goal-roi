"""Auditoria de regressões: o relatório completo com o código ANTES e DEPOIS da correção, à mesma hora e com os mesmos dados.

Só `brain.watch` pode mudar (e só por remoção de sinais que já não são oportunidades atuais, mais os campos `phase` e
`finished`); ROI, liquidações, hipóteses, estados, certificação e tudo o resto têm de ser idênticos byte a byte.
Corre o código de cada versão num processo próprio sobre uma CÓPIA de data/ (nunca escreve em data/).
Uso: python3 audits/stale-notices-evidence/compare_reports.py <commit_antes> <ISO now> [<ISO now> ...]
"""
import json, os, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scangoal import freshness  # noqa: E402

# O relógio real (`datetime.now`, usado por report.accuracy, brain.agenda e outros fora do `as_of`) fica congelado no MESMO
# instante (sys.argv[3]) nas duas construções: sem isto, duas construções com segundos de diferença podem cair de lados
# diferentes de um limiar (ex.: agenda "a decorrer" até 30 min) e a diferença não teria nada a ver com o código comparado.
CODE = '''
import json, sys
from datetime import datetime as _dt
from scangoal import report, brain, certification
FROZEN = _dt.fromisoformat(sys.argv[3])
class Frozen(_dt):
    @classmethod
    def now(cls, tz=None):
        return FROZEN if tz is None else FROZEN.astimezone(tz)
report.datetime = brain.datetime = certification.datetime = Frozen
r = report.build(None, register=False, now=_dt.fromisoformat(sys.argv[1]))
json.dump(r, open(sys.argv[2], 'w'), ensure_ascii=False, sort_keys=True)
'''


def build(src, data, now, out, wall):
    d = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(data, d / 'data')
        subprocess.run([sys.executable, '-c', CODE, now, str(out), wall], cwd=src, env=dict(os.environ, SCANGOAL_DATA_DIR=str(d / 'data')), check=True)
    finally:
        shutil.rmtree(d)
    return json.loads(Path(out).read_text(encoding='utf-8'))


def main(before, nows):
    tmp = Path(tempfile.mkdtemp())
    old_src = tmp / 'old'
    subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '-q', '--detach', str(old_src), before], check=True)
    results = []
    wall = datetime.now(timezone.utc).isoformat()          # um só relógio real congelado para todas as construções
    print(json.dumps({'frozen_wall_clock_utc': wall, 'before': before}))
    try:
        for now in nows:
            a = build(old_src, ROOT / 'data', now, tmp / 'a.json', wall)
            b = build(ROOT, ROOT / 'data', now, tmp / 'b.json', wall)
            wa, wb = a['brain'].pop('watch'), b['brain'].pop('watch')
            for x in (a, b):
                x.pop('generated_at_utc', None)
                (x.get('brain') or {}).get('agenda', {}).pop('generated_utc', None)
            same = json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
            diff = sorted(k for k in set(a) | set(b) if json.dumps(a.get(k), sort_keys=True) != json.dumps(b.get(k), sort_keys=True))
            bdiff = sorted(k for k in set(a['brain']) | set(b['brain']) if json.dumps(a['brain'].get(k), sort_keys=True) != json.dumps(b['brain'].get(k), sort_keys=True))
            strip = lambda w: {k: v for k, v in w.items() if k not in ('phase', 'finished')}
            kept = [strip(w) for w in wb]
            removed = [w for w in wa if w not in kept]
            t = datetime.fromisoformat(now)
            reasons = {}
            for w in removed:
                full = dict(w, phase=None)
                r = freshness.check(full, t)[1]
                reasons[r] = reasons.get(r, 0) + 1
            res = {'now': now, 'report_identical_except_watch': same, 'differing_keys': diff, 'differing_brain_keys': bdiff,
                   'watch_before': len(wa), 'watch_after': len(wb), 'after_subset_of_before': all(w in wa for w in kept),
                   'removed': len(removed), 'removed_reasons_without_phase': reasons,
                   'kept': [{k: w.get(k) for k in ('date_utc', 'kickoff_utc', 'phase', 'finished', 'match', 'selection')} for w in wb],
                   'removed_sample': [{k: w.get(k) for k in ('date_utc', 'kickoff_utc', 'match', 'selection')} for w in removed[:12]]}
            results.append(res)
            print(json.dumps(res, ensure_ascii=False, indent=1))
    finally:
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(old_src)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    return results


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])

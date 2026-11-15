"""PR 4: invariância por truncagem em dados reais (fuga de informação futura).

Uso (raiz de um clone completo): python3.11 audits/pr4-evidence/truncation_check.py <commit antigo> <commit recente> <protocolo> <publicação UTC> <ligação> [saída.json]
Avalia no instante T = hora do commit antigo, uma vez com os dados desse commit e outra com os dados do commit recente (que
contêm tudo o que aconteceu depois de T). Sem fuga, os resultados são idênticos; só podem diferir as contagens de hipóteses
registadas depois de T (que existem no registo recente mas não são avaliadas). Código de saída: 0 = invariante; 1 = fuga.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {'/inputs', '/population/llm_hypotheses', '/population/excluded/born_after_as_of'}


def diff(x, y, p=''):
    if p in ALLOWED or any(p.startswith(a + '/') for a in ALLOWED):
        return []
    if isinstance(x, dict) and isinstance(y, dict):
        return [d for k in sorted(set(x) | set(y)) for d in (diff(x.get(k), y.get(k), '%s/%s' % (p, k)) if k in x and k in y else ['%s/%s' % (p, k)])]
    if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        return [d for i, (u, v) in enumerate(zip(x, y)) for d in diff(u, v, '%s[%d]' % (p, i))]
    return [] if x == y else [p]


def main(argv):
    old, new, protocol, published, evidence = argv[:5]
    t = subprocess.run(['git', '-C', str(ROOT), 'show', '-s', '--format=%cI', old], capture_output=True, check=True, text=True).stdout.strip()
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, ref in (('old', old), ('new', new)):
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'audit_pr4_llm.py'), '--ref', ref, '--protocol-commit', protocol, '--as-of', t,
                            '--out', str(Path(tmp) / name),
                            '--protocol-published-utc', published, '--protocol-publication-evidence', evidence], check=True, capture_output=True)
            res[name] = json.loads((Path(tmp) / name / 'pr4-llm-evaluation.json').read_text(encoding='utf-8'))
    d = diff(res['old'], res['new'])
    out = {'as_of': t, 'old': res['old']['inputs']['ref'], 'new': res['new']['inputs']['ref'], 'differences': d, 'invariant': not d,
           'llm_hypotheses_in_registry': [res['old']['population']['llm_hypotheses'], res['new']['population']['llm_hypotheses']],
           'evaluated': [res['old']['population']['evaluated'], res['new']['population']['evaluated']],
           'born_after_as_of': [res['old']['population']['excluded']['born_after_as_of'], res['new']['population']['excluded']['born_after_as_of']],
           'pre_protocol': res['old']['pre_protocol'], 'duplicates_at_birth': len(res['old']['population']['duplicates_at_birth'])}
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if len(argv) > 5:
        Path(argv[5]).write_text(text, encoding='utf-8')
    print(text)
    return 0 if not d else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

"""PR 4: avaliação científica reprodutível das hipóteses LLM (protocolo audits/pr4-llm-protocol.md).

Só leitura e sem rede: lê `data/` de um commit fixo por `git archive` para uma pasta temporária, constrói o relatório com o
relógio `--as-of` (sem registar nada), prova o nascimento de cada hipótese e o protocolo pelo histórico git completo, e escreve
`<out>/pr4-llm-evaluation.json` e `.md`. Duas execuções com os mesmos argumentos produzem ficheiros iguais byte a byte.

Uso (na raiz de um clone completo, `git fetch --unshallow` se preciso):
    python3.11 scripts/audit_pr4_llm.py --ref <commit de main> --protocol-commit <sha do protocolo> [--as-of <UTC>] [--out audits]

Códigos de saída: 0 = relatório escrito (qualquer veredicto); 2 = uso errado ou entradas ilegíveis.
"""
import argparse
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', required=True)
    ap.add_argument('--protocol-commit', required=True)
    ap.add_argument('--as-of')
    ap.add_argument('--out', default=str(ROOT / 'audits'))
    args = ap.parse_args(argv)
    from scangoal import brain, chronology, llm_eval
    try:
        ref = git('rev-parse', '--verify', '%s^{commit}' % args.ref).decode().strip()
        ref_time = chronology.parse_utc(git('show', '-s', '--format=%cI', ref).decode().strip())
        archive = git('archive', '--format=tar', ref, 'data')
    except subprocess.CalledProcessError:
        print('AUDIT_PR4_BAD_REF')
        return 2
    as_of = chronology.parse_utc(args.as_of) if args.as_of else ref_time
    if as_of is None:
        print('AUDIT_PR4_BAD_AS_OF')
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter='data')
        data = Path(tmp) / 'data'
        inputs = {'ref': ref, 'ref_commit_time_utc': chronology.iso(ref_time), 'python': '%d.%d' % sys.version_info[:2]}
        for f in sorted(p for p in data.rglob('*') if p.is_file()):
            inputs['sha256 data/%s' % f.relative_to(data).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
        os.environ['SCANGOAL_DATA_DIR'] = str(data)
        from scangoal import report
        report.build(now=as_of)                                    # register=False: nada é escrito
        signals = report.LAST['signals']
        registry = brain.load_registry(data / 'hypotheses.json')
    first, history = llm_eval.registry_history(ROOT, ref)
    protocol_time, protocol = llm_eval.protocol_proof(ROOT, args.protocol_commit)
    problems = history + protocol
    born = llm_eval.births(registry, first, not history)
    res = llm_eval.evaluate(signals, registry, as_of=as_of, born_eff=born, protocol_time=protocol_time, problems=problems)
    try:
        inputs['protocol_commit'] = git('rev-parse', '--verify', '%s^{commit}' % args.protocol_commit).decode().strip()
    except subprocess.CalledProcessError:
        inputs['protocol_commit'] = args.protocol_commit
    res['inputs'] = inputs
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'pr4-llm-evaluation.json').write_text(llm_eval.dumps(res), encoding='utf-8')
    (out / 'pr4-llm-evaluation.md').write_text(llm_eval.render(res), encoding='utf-8')
    print('AUDIT_PR4_OK', res['verdict'], 'evaluated=%d groups=%d llm_games=%d' % (res['population']['evaluated'], res['population']['groups'], res['primary']['games_test']))
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""PR 4: avaliação científica reprodutível das hipóteses LLM (protocolo audits/pr4-llm-protocol-v5.md).

Só leitura e sem rede: lê `data/` de um commit fixo por `git archive` para uma pasta temporária, constrói o relatório com o
relógio `--as-of` (sem registar nada), prova o nascimento de cada hipótese e o protocolo pelo histórico git completo, e escreve
`<out>/pr4-llm-evaluation.json` e `.md`. Duas execuções com os mesmos argumentos produzem ficheiros iguais byte a byte.

Uso (na raiz de um clone completo, `git fetch --unshallow` se preciso):
    python3.11 scripts/audit_pr4_llm.py --ref <commit de main> --protocol-commit <sha do protocolo> \
        --protocol-published-utc <hora do servidor GitHub> --protocol-publication-evidence <ligação github.com> [--as-of <UTC>] [--out audits]
Sem prova de publicação do protocolo (§0-A do protocolo v5), do nascimento ou das observações (§0-C), a avaliação sai "não
verificada". O e-processo é válido a qualquer momento; com --as-of em C ou depois, os dados são sempre os do primeiro commit de
main com hora >= C (registado em `inputs.ref`; o commit pedido fica em `run.requested_ref`, fora do resultado científico, cujo
`result_sha256` não muda entre execuções depois de C).

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
    ap.add_argument('--protocol-published-utc')
    ap.add_argument('--protocol-publication-evidence')
    ap.add_argument('--receipt-directory', type=Path)
    ap.add_argument('--out', default=str(ROOT / 'audits'))
    args = ap.parse_args(argv)
    from scangoal import brain, chronology, llm_eval
    try:
        requested = git('rev-parse', '--verify', '%s^{commit}' % args.ref).decode().strip()
        requested_time = chronology.parse_utc(git('show', '-s', '--format=%cI', requested).decode().strip())
    except subprocess.CalledProcessError:
        print('AUDIT_PR4_BAD_REF')
        return 2
    as_of = chronology.parse_utc(args.as_of) if args.as_of else requested_time
    if as_of is None:
        print('AUDIT_PR4_BAD_AS_OF')
        return 2
    protocol_time, protocol = llm_eval.protocol_proof(ROOT, args.protocol_commit, receipts=args.receipt_directory, as_of=as_of)
    # Protocolo v4, §0-C: em C ou depois, os dados são sempre os do primeiro commit de main com hora >= C (nunca um posterior).
    ref, problems = requested, list(protocol)
    _, final = llm_eval.windows(protocol_time)
    if final is not None and as_of >= final:
        snapshot = llm_eval.confirmation_snapshot(ROOT, requested, final)
        if snapshot is None:
            problems.append('final data commit (first commit at or after C) not available in %s' % requested[:12])
        else:
            ref = snapshot
    try:
        ref_time = chronology.parse_utc(git('show', '-s', '--format=%cI', ref).decode().strip())
        archive = git('archive', '--format=tar', ref, 'data')
    except subprocess.CalledProcessError:
        print('AUDIT_PR4_BAD_REF')
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter='data')
        data = Path(tmp) / 'data'
        inputs = {'ref': ref, 'ref_commit_time_utc': chronology.iso(ref_time)}
        for f in sorted(p for p in data.rglob('*') if p.is_file()):
            inputs['sha256 data/%s' % f.relative_to(data).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
        os.environ['SCANGOAL_DATA_DIR'] = str(data)
        from scangoal import report
        report.build(as_of=as_of)                                  # register=False: nada é escrito
        signals = report.LAST['research_signals']
        registry = brain.load_registry(data / 'hypotheses.json')
    first, history = llm_eval.registry_history(ROOT, ref)
    problems = history + problems + (llm_eval.observation_proof(ROOT, ref, protocol_time) if protocol_time is not None else [])
    born = llm_eval.births(registry, first, False)                   # só descrição, sem prova independente de nascimento
    res = llm_eval.evaluate(signals, registry, as_of=as_of, born_eff=born, protocol_time=protocol_time, problems=problems)
    try:
        inputs['protocol_commit'] = git('rev-parse', '--verify', '%s^{commit}' % args.protocol_commit).decode().strip()
    except subprocess.CalledProcessError:
        inputs['protocol_commit'] = args.protocol_commit
    inputs['protocol_receipt_utc'] = chronology.iso(protocol_time)
    cut = llm_eval.cutoff_of(as_of, protocol_time)
    inputs['llm_hypotheses_registered_after_window'] = sum(1 for hid, h in registry.items() if h.get('source') == 'llm'
                                                          and born.get(hid) is not None and born[hid] > cut)
    res['inputs'] = inputs
    # v5 (§10): metadados de execução fora do resultado científico; result_sha256 é igual em todas as execuções com relógio >= C.
    res['result_sha256'] = llm_eval.result_digest(res)
    res['run'] = {'requested_ref': requested, 'python': '%d.%d' % sys.version_info[:2]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'pr4-llm-evaluation.json').write_text(llm_eval.dumps(res), encoding='utf-8')
    (out / 'pr4-llm-evaluation.md').write_text(llm_eval.render(res), encoding='utf-8')
    print('AUDIT_PR4_OK', res['verdict'], 'evaluated=%d groups=%d llm_games=%d' % (res['population']['evaluated'], res['population']['groups'], res['primary']['games_test']))
    return 0


if __name__ == '__main__':
    sys.exit(main())

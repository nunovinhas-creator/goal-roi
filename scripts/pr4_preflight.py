"""PR 4: preflight de integração com o `main` atualizado (só leitura; não faz merge, não escreve em refs, não usa rede).

Uso (raiz de um clone completo, depois de `git fetch origin main`):
    python3.11 scripts/pr4_preflight.py <commit de main> <commit da PR> [saída.json]

1. Histórico completo (não raso).
2. Ficheiros alterados pela PR desde o merge-base: nenhum em `data/`, `.github/`, `docs/report.json` ou `docs/bets.csv`.
3. Merge simulado (`git merge-tree --write-tree`): sem conflitos. O resultado é um objeto de árvore solto; nenhuma ref muda.
4. Na árvore resultante: `data/`, `.github/`, `docs/report.json` e `docs/bets.csv` são exatamente os do `main` (dados de produção
   preservados); não existe `data/certification_ledger.json`.
5. Testes (o comando do workflow `tests.yml`) numa cópia da árvore resultante, com Python 3.11.
6. Comparação com `main` sobre os dados do `main` (audits/pr4-evidence/golden_compare.py, com o commit solto da árvore resultante):
   estados, certificação, mensagens Telegram, estado de notificações e dados idênticos; só `brain.track` e `brain.llm_evaluation` diferem.
Código de saída: 0 = pronto para integrar (sujeito a autorização); 1 = alguma verificação falhou; 2 = uso errado.
"""
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ('data/', '.github/', 'docs/report.json', 'docs/bets.csv')


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=check)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    main_ref, pr_ref = (git('rev-parse', '--verify', '%s^{commit}' % r).stdout.strip() for r in argv[:2])
    out = {'main': main_ref, 'pr': pr_ref, 'checks': {}}
    c = out['checks']
    c['full_history'] = git('rev-parse', '--is-shallow-repository').stdout.strip() == 'false'
    base = git('merge-base', main_ref, pr_ref).stdout.strip()
    changed = [f for f in git('diff', '--name-only', base, pr_ref).stdout.split('\n') if f]
    out['merge_base'], out['pr_changed_files'] = base, changed
    c['pr_touches_no_production_files'] = not [f for f in changed if f.startswith(PROTECTED)]
    merge = git('merge-tree', '--write-tree', '--messages', main_ref, pr_ref, check=False)
    c['merge_without_conflicts'] = merge.returncode == 0
    tree = merge.stdout.split('\n')[0].strip()
    out['merged_tree'] = tree
    if not c['merge_without_conflicts']:
        out['merge_messages'] = merge.stdout.split('\n')[1:40]
        out['ready'] = False
        return finish(out, argv)
    commit = git('commit-tree', tree, '-p', main_ref, '-p', pr_ref, '-m', 'preflight (not referenced)').stdout.strip()
    out['merged_commit_unreferenced'] = commit
    diff_prod = [f for f in git('diff', '--name-only', main_ref, commit, '--', *PROTECTED).stdout.split('\n') if f]
    c['production_files_identical_to_main'] = not diff_prod
    out['production_files_differing'] = diff_prod
    c['no_certification_ledger'] = git('cat-file', '-e', '%s:data/certification_ledger.json' % commit, check=False).returncode != 0
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', commit], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter='data')
        tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=tmp, capture_output=True, text=True)
        tail = [line for line in tests.stderr.strip().split('\n') if line.startswith(('Ran ', 'OK', 'FAILED'))]
        c['tests_pass_on_merged_tree'] = tests.returncode == 0
        out['tests'] = tail
    golden = subprocess.run([sys.executable, str(ROOT / 'audits' / 'pr4-evidence' / 'golden_compare.py'), main_ref, commit, main_ref],
                            cwd=str(ROOT), capture_output=True, text=True)
    try:
        g = json.loads(golden.stdout)
        out['golden'] = {k: g[k] for k in ('ok', 'report_differences_outside_allowed', 'status_list_pr', 'validation_state',
                                            'telegram_messages_identical', 'telegram_messages_count', 'notify_state_identical',
                                            'data_files_identical', 'certification_ledger_created', 'pr_llm_evaluation')}
        c['golden_comparison_with_main'] = g['ok']
    except (ValueError, KeyError):
        out['golden'] = {'error': golden.stderr[-2000:]}
        c['golden_comparison_with_main'] = False
    out['ready'] = all(c.values())
    return finish(out, argv)


def finish(out, argv):
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if len(argv) > 2:
        Path(argv[2]).write_text(text, encoding='utf-8')
    print(text)
    return 0 if out['ready'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

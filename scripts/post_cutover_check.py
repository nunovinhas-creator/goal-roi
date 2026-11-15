"""Verificação pós-corte da PR 3 (fase D): só leitura, nunca escreve, nunca usa rede, nunca envia Telegram.

Compara dois commits de `main` depois do merge do corte: `antes` (o merge, ou a execução anterior do coletor) e `depois`
(o commit da execução a verificar). Lê `data/notify_state.json`, `data/report.json` e a existência de
`data/certification_ledger.json` diretamente dos objetos git (`git show`), sem tocar no checkout.

Uso (na raiz do repositório, depois de `git fetch origin main`):
    python3.11 scripts/post_cutover_check.py <commit_antes> <commit_depois>

Código de saída: 0 = todas as verificações passam; 1 = alguma falha (parar e seguir a reversão R3 do runbook);
2 = uso errado ou ficheiros ilegíveis.

Verificações (todas obrigatórias):
- markers_unchanged: as três chaves de migração (PR 1, PR 2, PR 3) existem nos dois commits e são iguais;
- delivery_monotonic: `sent_total` e o número de `watch_sent` nunca diminuem (histórico de entregas preservado);
- status_rules: o relatório foi produzido pelas regras da PR 3;
- certification_off: `validation.state = not_activated`, sem `certified`, sem `ledger_valid`, sem registo de certificação;
- no_validated_statuses: nenhuma tática `forte` ou `confirmada`;
- statuses_follow_report: a referência Telegram (`statuses`, sem "em observação") é igual ao `status_list` do relatório.

`status_list_changes` não é uma falha: lista as mudanças reais de estado entre os dois commits, que têm de corresponder
uma a uma aos avisos de estado recebidos no Telegram nessa execução (verificação manual do proprietário).
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKERS = ('eligible_statuses_v1', 'status_rules:independent_games_hysteresis_v1', 'status_rules:chronological_eprocess_v3')
RULES = 'chronological_eprocess_v3'
LEDGER = 'data/certification_ledger.json'
VALIDATED = ('forte', 'confirmada')
WATCHING = 'em observação'


class Unreadable(Exception):
    pass


def show(root, rev, path):
    r = subprocess.run(['git', '-C', str(root), 'show', '%s:%s' % (rev, path)], capture_output=True)
    if r.returncode:
        raise Unreadable('%s not readable at %s' % (path, rev))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Unreadable('%s is not valid JSON at %s' % (path, rev))


def exists(root, rev, path):
    return subprocess.run(['git', '-C', str(root), 'cat-file', '-e', '%s:%s' % (rev, path)], capture_output=True).returncode == 0


def check(root, before, after):
    na, nb = show(root, before, 'data/notify_state.json'), show(root, after, 'data/notify_state.json')
    rb = show(root, after, 'data/report.json')
    ra = show(root, before, 'data/report.json')
    ma, mb = na.get('migrations') or {}, nb.get('migrations') or {}
    brain_a, brain_b = ra.get('brain') or {}, rb.get('brain') or {}
    la, lb = brain_a.get('status_list') or {}, brain_b.get('status_list') or {}
    v = brain_b.get('validation') or {}
    sent = [na.get('sent_total') or 0, nb.get('sent_total') or 0]
    watch = [len(na.get('watch_sent') or []), len(nb.get('watch_sent') or [])]
    following = {h: s for h, s in (nb.get('statuses') or {}).items() if s != WATCHING}
    checks = {
        'markers_unchanged': all(k in ma and k in mb and ma[k] == mb[k] for k in MARKERS),
        'delivery_monotonic': sent[1] >= sent[0] and watch[1] >= watch[0],
        'status_rules': brain_b.get('status_rules') == RULES,
        'certification_off': (v.get('state') == 'not_activated' and not v.get('certified') and not v.get('ledger_valid')
                              and not exists(root, after, LEDGER)),
        'no_validated_statuses': not any(s in VALIDATED for s in lb.values()),
        'statuses_follow_report': following == lb,
    }
    return {
        'before': before, 'after': after, 'checks': checks, 'ok': all(checks.values()),
        'migration_keys_after': sorted(mb),
        'sent_total': sent, 'watch_sent_count': watch, 'sent_in_run': sent[1] - sent[0],
        'status_rules_after': brain_b.get('status_rules'), 'validation_state_after': v.get('state'),
        'status_list_changes': {h: [la.get(h), lb.get(h)] for h in sorted(set(la) | set(lb)) if la.get(h) != lb.get(h)},
    }


def main(argv=None, root=ROOT):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(json.dumps({'ok': False, 'error': 'usage: post_cutover_check.py <commit_before> <commit_after>'}))
        return 2
    try:
        result = check(root, *argv)
    except Unreadable as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())

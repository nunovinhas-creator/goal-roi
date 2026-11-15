"""Registo append-only das seleções para certificação (PR 3, revisão #5911603577). Sem rede.

Porquê: a garantia do teste de confirmação (brain.confirmation) exige que a ordem de seleção j, o α_j e o instante a partir do
qual os jogos contam sejam fixados UMA vez, em tempo real, e nunca recalculados com resultados posteriores. Reconstituir a
ordem a cada relatório não chega: um resultado ou um sinal tardio poderia reordenar seleções já feitas.

Regras:
- O registo só existe depois de uma ATIVAÇÃO explícita (`python -m scangoal.certification --activate`). Sem registo, ou com
  um registo inválido, não há certificados nem táticas validadas: fail-closed, a validação está bloqueada por defeito.
- Na ativação, as hipóteses já promissoras ficam LEGADAS: nunca são certificáveis (a sua seleção foi reconstruída do passado,
  não feita em tempo real) e não gastam α.
- Depois, cada relatório com register=True acrescenta, com a hora real, as hipóteses certificáveis que estão promissoras e
  ainda não constam: rank = posição na lista, α = FWER_ALPHA/(rank(rank+1)). Nada é reescrito nem apagado.
- Cada entrada guarda o hash das condições da hipótese e o hash da entrada anterior (cadeia verificável); o histórico git do
  ficheiro é o relógio externo.
"""
import hashlib, json, os, sys, tempfile
from datetime import datetime, timezone

from . import chronology

FILE = 'certification_ledger.json'
METHOD = 'eprocess_online_alpha_v1'
ALPHA = 0.05


def alpha_for_rank(j):
    return ALPHA / (j * (j + 1))


def conditions_hash(h):
    data = json.dumps({'family': h.get('family'), 'conditions': sorted(map(list, h.get('conditions') or []))}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def entry_hash(e):
    return hashlib.sha256(json.dumps(e, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def load(path):
    """-> registo (dict) ou None se não existir. Um ficheiro ilegível conta como inválido (verify devolve problemas)."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {'invalid': 'unreadable'}


def verify(ledger, registry=None):
    """-> lista de problemas (vazia = válido). Qualquer problema desliga TODA a certificação."""
    if not isinstance(ledger, dict):
        return ['not a ledger']
    problems = []
    if ledger.get('method') != METHOD or ledger.get('alpha') != ALPHA:
        problems.append('method or alpha')
    start = chronology.parse_utc(ledger.get('activated_utc'))
    if start is None:
        problems.append('activated_utc')
    legacy, entries = ledger.get('legacy'), ledger.get('entries')
    if not isinstance(legacy, list) or not isinstance(entries, list):
        return problems + ['structure']
    legacy_ids = {x.get('id') for x in legacy if isinstance(x, dict)}
    seen, prev_time, prev_hash = set(), start, ledger.get('genesis_sha256')
    if prev_hash != entry_hash({k: ledger.get(k) for k in ('method', 'alpha', 'activated_utc', 'legacy')}):
        problems.append('genesis hash')
    for j, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            problems.append('entry %d' % j)
            continue
        t = chronology.parse_utc(e.get('selected_utc'))
        if e.get('rank') != j or e.get('alpha') != alpha_for_rank(j):
            problems.append('rank/alpha %d' % j)
        if t is None or prev_time is None or t < prev_time:
            problems.append('time %d' % j)
        if e.get('id') in seen or e.get('id') in legacy_ids or not isinstance(e.get('id'), str):
            problems.append('id %d' % j)
        if e.get('prev_sha256') != prev_hash:
            problems.append('chain %d' % j)
        if registry is not None and (e.get('id') not in registry or conditions_hash(registry[e['id']]) != e.get('conditions_sha256')):
            problems.append('conditions %d' % j)
        seen.add(e.get('id'))
        prev_time, prev_hash = t or prev_time, entry_hash(e)
    return problems


def selections(ledger, registry):
    """-> ({id: (rank, α, datetime)}, problemas). Com problemas, devolve {}: nenhuma certificação (fail-closed)."""
    if ledger is None:
        return {}, ['not activated']
    problems = verify(ledger, registry)
    if problems:
        return {}, problems
    return {e['id']: (e['rank'], e['alpha'], chronology.parse_utc(e['selected_utc'])) for e in ledger['entries']}, []


def activate(registry_statuses, now):
    """Novo registo. registry_statuses: {id: estado atual}; as promissoras (ou melhores) ficam legadas, não certificáveis."""
    ledger = {'method': METHOD, 'alpha': ALPHA, 'activated_utc': now.isoformat(timespec='seconds'),
              'legacy': [{'id': hid, 'status_at_activation': st} for hid, st in sorted(registry_statuses.items())
                         if st in ('promissora', 'forte', 'confirmada')], 'entries': []}
    ledger['genesis_sha256'] = entry_hash({k: ledger[k] for k in ('method', 'alpha', 'activated_utc', 'legacy')})
    return ledger


def append(ledger, candidates, registry, now):
    """Acrescenta, por ordem de identidade, as candidatas novas (já promissoras e certificáveis). -> quantas entraram."""
    known = {e['id'] for e in ledger['entries']} | {x['id'] for x in ledger['legacy']}
    added = 0
    for hid in sorted(set(candidates) - known):
        prev = ledger['entries'][-1] if ledger['entries'] else None
        j = len(ledger['entries']) + 1
        ledger['entries'].append({'rank': j, 'id': hid, 'alpha': alpha_for_rank(j), 'selected_utc': now.isoformat(timespec='seconds'),
                                  'conditions_sha256': conditions_hash(registry[hid]),
                                  'prev_sha256': entry_hash(prev) if prev else ledger['genesis_sha256']})
        added += 1
    return added


def save(path, ledger):
    """Substituição atómica."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.ledger-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(ledger, f, indent=1, ensure_ascii=False, sort_keys=True)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    """Ativação manual e única; nunca chamada pela recolha. Exige autorização do proprietário (corte controlado)."""
    from .store import data_dir
    if '--activate' not in sys.argv:
        print('usage: python -m scangoal.certification --activate')
        sys.exit(2)
    path = data_dir() / FILE
    if path.exists():
        print('CERTIFICATION_ALREADY_ACTIVE')
        sys.exit(1)
    report = json.loads((data_dir() / 'report.json').read_text(encoding='utf-8'))
    statuses = (report.get('brain') or {}).get('status_list') or {}
    save(path, activate(statuses, datetime.now(timezone.utc)))
    print('CERTIFICATION_ACTIVATED legacy', sum(1 for v in statuses.values() if v in ('promissora', 'forte', 'confirmada')))


if __name__ == '__main__':
    main()

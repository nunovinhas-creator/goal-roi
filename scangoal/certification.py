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
- Cada entrada guarda o hash das condições da hipótese e o hash da entrada anterior.

Alcance da verificação (revisão #5911747123, P2): a cadeia de hashes DENTRO do ficheiro só prova consistência interna — um
ficheiro inteiramente reescrito, com hashes recalculados, também a satisfaz. A prova de que o registo só cresceu é externa:
verify_history() confirma que cada versão publicada (histórico git do ficheiro, com as horas dos commits do GitHub) prolonga a
anterior sem alterar nada. O relatório expõe a cabeça da cadeia (ledger_head_sha256), que fica também no histórico git do
relatório. verify() nunca levanta exceções: qualquer entrada malformada é só um problema, que desliga a certificação.
"""
import hashlib, json, os, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _str(x):
    return isinstance(x, str) and bool(x)


def verify(ledger, registry=None):
    """-> lista de problemas (vazia = válido). Nunca levanta exceções. Qualquer problema desliga TODA a certificação."""
    try:
        return _verify(ledger, registry)
    except Exception as exc:          # defesa em profundidade: um registo adulterado nunca derruba o relatório
        return ['malformed: %s' % type(exc).__name__]


def _verify(ledger, registry):
    if not isinstance(ledger, dict):
        return ['not a ledger']
    problems = []
    if ledger.get('method') != METHOD or ledger.get('alpha') != ALPHA or isinstance(ledger.get('alpha'), bool):
        problems.append('method or alpha')
    start = chronology.parse_utc(ledger.get('activated_utc'))
    if start is None:
        problems.append('activated_utc')
    legacy, entries = ledger.get('legacy'), ledger.get('entries')
    if not isinstance(legacy, list) or not isinstance(entries, list):
        return problems + ['structure']
    if any(not isinstance(x, dict) or not _str(x.get('id')) for x in legacy):
        return problems + ['legacy entries']
    legacy_ids = [x['id'] for x in legacy]
    if len(set(legacy_ids)) != len(legacy_ids) or legacy_ids != sorted(legacy_ids):
        problems.append('legacy ids')
    if ledger.get('genesis_sha256') != entry_hash({k: ledger.get(k) for k in ('method', 'alpha', 'activated_utc', 'legacy')}):
        problems.append('genesis hash')
    seen, prev_time, prev_hash = set(), start, ledger.get('genesis_sha256')
    fields = {'rank', 'id', 'alpha', 'selected_utc', 'conditions_sha256', 'prev_sha256'}
    for j, e in enumerate(entries, 1):
        if not isinstance(e, dict) or set(e) != fields or not all(_str(e.get(k)) for k in ('id', 'selected_utc', 'conditions_sha256', 'prev_sha256')):
            problems.append('entry %d' % j)
            break
        t = chronology.parse_utc(e['selected_utc'])
        if type(e['rank']) is not int or e['rank'] != j or type(e['alpha']) is not float or e['alpha'] != alpha_for_rank(j):
            problems.append('rank/alpha %d' % j)
        if t is None or prev_time is None or t < prev_time:
            problems.append('time %d' % j)
        if e['id'] in seen or e['id'] in legacy_ids:
            problems.append('id %d' % j)
        if e['prev_sha256'] != prev_hash:
            problems.append('chain %d' % j)
        if registry is not None and (e['id'] not in registry or conditions_hash(registry[e['id']]) != e['conditions_sha256']):
            problems.append('conditions %d' % j)
        seen.add(e['id'])
        prev_time, prev_hash = t or prev_time, entry_hash(e)
    return problems


def head(ledger):
    """Cabeça da cadeia (hash da última entrada, ou do génesis): publicada no relatório como âncora."""
    entries = ledger.get('entries') or []
    return entry_hash(entries[-1]) if entries else ledger.get('genesis_sha256')


def verify_history(versions):
    """Âncora externa: versões publicadas do registo, por ordem (por exemplo, o histórico git do ficheiro). Cada uma tem de ser
    válida e prolongar a anterior sem alterar o génesis, as legadas nem nenhuma entrada já publicada. -> lista de problemas."""
    if not versions:
        return ['no published version: nothing proves the history']
    problems, prev = [], None
    for i, led in enumerate(versions):
        p = verify(led)
        if p:
            problems.append('version %d invalid: %s' % (i, ', '.join(p)))
            continue
        if prev is not None:
            if any(led.get(k) != prev.get(k) for k in ('method', 'alpha', 'activated_utc', 'legacy', 'genesis_sha256')):
                problems.append('version %d rewrote the genesis' % i)
            elif led['entries'][:len(prev['entries'])] != prev['entries']:
                problems.append('version %d is not an append-only extension' % i)
        prev = led
    return problems


PUBLISH_TOLERANCE_MIN = 30   # um registo escrito por um relatório é publicado no commit do mesmo job (timeout do job: 15 min)


def _git(root, *args, data=None):
    return subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True, check=True).stdout


def published_versions(path):
    """Histórico publicado do registo (git): -> ([(hora do commit, registo)], problemas). Fail-closed em qualquer dúvida:
    sem git, repositório shallow (histórico truncado), ficheiro nunca publicado, apagado ou ilegível numa versão."""
    path = Path(path)
    try:
        root = Path(_git(path.parent, 'rev-parse', '--show-toplevel').decode().strip())
        if _git(root, 'rev-parse', '--is-shallow-repository').decode().strip() != 'false':
            return [], ['history unavailable: shallow repository']
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        log = _git(root, 'log', '--reverse', '--format=%H %cI', '--', rel).decode().split('\n')
    except (OSError, ValueError, subprocess.CalledProcessError):
        return [], ['history unavailable: no git repository']
    commits = [line.split() for line in log if line.strip()]
    if not commits:
        return [], ['ledger never published']
    try:
        out = _git(root, 'cat-file', '--batch', data=''.join('%s:%s\n' % (sha, rel) for sha, _ in commits).encode())
    except subprocess.CalledProcessError:
        return [], ['history unavailable: cannot read versions']
    versions, problems, pos = [], [], 0
    for sha, when in commits:
        header_end = out.index(b'\n', pos)
        header = out[pos:header_end].decode().split()
        if header[-1] == 'missing':
            problems.append('ledger deleted at %s' % sha[:12])
            pos = header_end + 1
            continue
        size = int(header[2])
        body = out[header_end + 1:header_end + 1 + size]
        pos = header_end + 1 + size + 1
        try:
            versions.append((chronology.parse_utc(when), json.loads(body)))
        except ValueError:
            problems.append('unreadable version %s' % sha[:12])
    return versions, problems


def history_proof(path, current, as_of=None, versions=None):
    """Prova operacional de integridade histórica (revisão #5911845265, P1-H). -> lista de problemas (vazia = provado).

    1. o registo foi publicado pelo menos uma vez, num repositório com histórico completo (published_versions);
    2. cada versão publicada prolonga a anterior e a cópia atual prolonga a última publicada (verify_history);
    3. coerência temporal: o génesis e cada entrada foram publicados no commit que os introduziu entre o seu carimbo e
       PUBLISH_TOLERANCE_MIN depois (nunca antes); entradas ainda não publicadas não são anteriores à última publicação
       nem posteriores a `as_of`.
    Qualquer problema desliga toda a certificação e impede novas entradas.
    """
    if versions is None:
        versions, problems = published_versions(path)
        if problems:
            return problems
    if not versions:
        return ['ledger never published']
    problems = verify_history([v for _, v in versions] + [current])
    if problems:
        return problems
    tol = timedelta(minutes=PUBLISH_TOLERANCE_MIN)
    coherent = lambda stamp, commit: stamp is not None and commit is not None and stamp <= commit <= stamp + tol
    first_time, first = versions[0]
    if not coherent(chronology.parse_utc(first['activated_utc']), first_time):
        problems.append('genesis published out of time')
    seen = 0
    for when, led in versions:
        for e in led['entries'][seen:]:
            if not coherent(chronology.parse_utc(e['selected_utc']), when):
                problems.append('entry %d published out of time' % e['rank'])
        seen = len(led['entries'])
    last_time = versions[-1][0]
    for e in current['entries'][seen:]:
        t = chronology.parse_utc(e['selected_utc'])
        if t is None or t < last_time or (as_of is not None and t > as_of):
            problems.append('unpublished entry %d out of time' % e['rank'])
    return problems


def selections(ledger, registry):
    """-> ({id: (rank, α, datetime)}, problemas). Com problemas, devolve {}: nenhuma certificação (fail-closed)."""
    if ledger is None:
        return {}, ['not activated']
    problems = verify(ledger, registry)
    if problems:
        return {}, problems
    return {e['id']: (e['rank'], e['alpha'], chronology.parse_utc(e['selected_utc'])) for e in ledger['entries']}, []


PROMISING = ('promissora', 'forte', 'confirmada')
REPORT_MAX_AGE_MIN = 20


def activation_plan(report, notify_state, fresh_rows, status_rules, now, *, collection_paused=False, ledger_exists=False):
    """Pré-condições da ativação (revisão #5911747123, P2) e o registo a criar. -> (registo ou None, problemas).

    Exige: confirmação explícita de que a recolha está parada (sem escritores concorrentes); nenhum registo existente; o
    report.json publicado gerado sob as regras atuais, há no máximo REPORT_MAX_AGE_MIN minutos, com o mesmo status_list do
    cálculo fresco; a migração Telegram das regras atuais já feita. Legadas = TODAS as hipóteses que estão, estiveram ou estão
    a caminho de ser promissoras (estado, estado pendente e qualquer mudança passada), no cálculo fresco e no relatório.
    """
    problems = []
    if not collection_paused:
        problems.append('collection not confirmed paused')
    if ledger_exists:
        problems.append('ledger already exists')
    brain_ = (report or {}).get('brain') or {}
    if brain_.get('status_rules') != status_rules:
        problems.append('report not produced under %s' % status_rules)
    made = chronology.parse_utc((report or {}).get('generated_at_utc'))
    if made is None or not 0 <= (now - made).total_seconds() <= REPORT_MAX_AGE_MIN * 60:
        problems.append('report is not fresh')
    fresh = {r['id']: r['status'] for r in fresh_rows if r.get('status') != 'em observação'}
    if brain_.get('status_list') != fresh:
        problems.append('report statuses differ from a fresh evaluation')
    if 'status_rules:%s' % status_rules not in ((notify_state or {}).get('migrations') or {}):
        problems.append('telegram migration for %s not done' % status_rules)
    if (brain_.get('validation') or {}).get('state') != 'not_activated':
        problems.append('validation is not in the not_activated state')
    if problems:
        return None, problems
    legacy = {}
    for r in fresh_rows:
        ever = r.get('status') in PROMISING or r.get('pending_status') in PROMISING or any(c.get('to') in PROMISING for c in r.get('status_changes') or [])
        if ever:
            legacy[r['id']] = r['status']
    for hid, st in (brain_.get('status_list') or {}).items():
        if st in PROMISING:
            legacy.setdefault(hid, st)
    ledger = activate({h: 'promissora' if st not in PROMISING else st for h, st in legacy.items()}, now)
    missing = [h for h, st in fresh.items() if st in PROMISING and h not in {x['id'] for x in ledger['legacy']}]
    return (None, ['legacy incomplete: %s' % missing]) if missing else (ledger, [])


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
    """Ativação manual e única; nunca chamada pela recolha. Só no corte autorizado, com a recolha parada:
    python -m scangoal.certification --activate --confirm-collection-paused
    """
    from . import brain, report as report_mod
    from .store import data_dir
    if '--activate' not in sys.argv:
        print('usage: python -m scangoal.certification --activate --confirm-collection-paused')
        sys.exit(2)
    path = data_dir() / FILE
    now = datetime.now(timezone.utc)
    published = json.loads((data_dir() / 'report.json').read_text(encoding='utf-8'))
    notify_state = json.loads((data_dir() / 'notify_state.json').read_text(encoding='utf-8'))
    report_mod.build(None, register=False)
    registry = brain.load_registry(data_dir() / 'hypotheses.json')
    rows = brain.evaluate(report_mod.LAST['signals'], registry, report_mod.MIN_SETTLED)[0]
    ledger, problems = activation_plan(published, notify_state, rows, brain.STATUS_RULES, now,
                                       collection_paused='--confirm-collection-paused' in sys.argv, ledger_exists=path.exists())
    if problems:
        print('CERTIFICATION_NOT_ACTIVATED', '; '.join(problems))
        sys.exit(1)
    save(path, ledger)
    check = verify(load(path), registry)
    if check:
        print('CERTIFICATION_ACTIVATION_FAILED_VERIFY', check)
        sys.exit(1)
    print('CERTIFICATION_ACTIVATED legacy', len(ledger['legacy']), 'head', head(ledger))


if __name__ == '__main__':
    main()

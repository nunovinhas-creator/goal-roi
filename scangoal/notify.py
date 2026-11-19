"""Avisos pelo Telegram, por um bot do utilizador (API de bots; não usa a conta que lê o ScanGoal).

Envia: aviso de jogo (indício, ou ALERTA só com certificado ativo e verificado da própria hipótese), um por jogo, com as
seleções novas desse jogo, em formato curto ("A seguir" / "Encaixa em" / "Aposta"); mudanças de comportamento de um tipo de
sinal; e um ÚNICO resumo diário às 22:00 de Lisboa (Europe/Lisbon, hora de verão e de inverno), que é também o único sítio
onde saem as mudanças de estado das hipóteses (promoções, abandonos, ganho ou perda de certificação), em blocos curtos.

PR 5 (formato `telegram_format_v2`):
- Entrega "no máximo uma vez": cada acontecimento tem uma chave determinística. A intenção é gravada (estado atómico e
  registo `notify_log.jsonl`, só a crescer) ANTES do envio; depois da resposta grava-se o resultado. Estados: pendente,
  entregue (resposta positiva do Telegram), falha confirmada (o Telegram recusou ou a ligação nunca se fez), incerta (não se
  sabe se saiu). Uma entrega incerta nunca é reenviada nem contada em `sent_total`.
- Consolidação: um aviso por jogo; reforços do mesmo (jogo, mercado, seleção) nunca geram outro aviso; hipóteses
  equivalentes (Jaccard >= 0,85 nos jogos, ligação completa e compatibilidade da PR 4) saem numa só mensagem.
- Mudanças de estado só com relatório saudável, hipótese presente, sem estado pendente e com a mudança registada pelo
  cérebro (a perda de certificação passa mesmo com relatório degradado); só no resumo diário. A elegibilidade das apostas
  (indício ou alerta) segue o certificado do relatório em cada recolha, sem esperar pelo resumo.
- Afirmações positivas só com certificado verificado da hipótese citada (`scientific_label` + barreira final `guard_claims`):
  um ALERTA leva o rótulo exato da tática certificada; um indício não faz nenhuma afirmação de certificação.
Segredos: TG_BOT_TOKEN e TG_CHAT_ID (o token só vai no endereço do pedido e nunca é impresso nem gravado). Sem eles, não faz
nada. `--preview` nunca os lê, nunca abre ligações e nunca grava estado.
"""
import copy, hashlib, json, os, re, socket, subprocess, sys, tempfile, time, unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import equivalence, freshness
from .eligibility import text_identity
from .results_bsd import load_json
from .store import data_dir

STATUS_MIGRATION = 'eligible_statuses_v1'
FORMAT_MIGRATION = 'telegram_format_v2'
EVIDENCE_SCOPE = 'eligible_bets_only'
SUMMARY_HOUR_LISBON = 22            # resumo diário (e únicas mensagens de mudança de estado) às 22:00 de Lisboa
MAX_WATCH_PER_RUN = 5             # jogos (não sinais) por execução
MAX_GROUPS_PER_MESSAGE = 8
MAX_TEXT = 3900
VALID = ('forte', 'confirmada')   # tática validada: só com certificado do teste de confirmação (PR 3)
VALIDATION_METHOD = 'eprocess_online_alpha_v1'   # único método aceite para anunciar uma tática como validada (fail-closed)
ALERT_MAX_AGE_MIN = freshness.LIVE_MAX_AGE_MIN   # 30: idade máxima de um sinal ao vivo, para QUALQUER aviso de aposta
TAU = 0.85                        # Jaccard mínimo entre os jogos de duas hipóteses para as agrupar (só apresentação)
GROUP_MIN_GAMES = 8
OSCILLATION_HOURS = 24            # só para mostrar registos antigos de oscilações (o resumo diário já as absorve)
DEGRADED_ACCEPT_HOURS = 24        # degradação estável há 24 h: passa a ser a nova referência (atrasa, nunca descarta)
COUNT_DROP_TOLERANCE = 0.02       # queda relativa tolerada no universo elegível, hipóteses e apostas com resultado
LEGACY_PAIR_DAYS = 3
LOG_NAME = 'notify_log.jsonl'
STATE_NAME = 'notify_state.json'
TERMINAL = ('delivered', 'failed', 'uncertain', 'blocked', 'expired')
COUNTERS = ('delivered', 'failed', 'uncertain', 'blocked', 'expired')
LISBON = ZoneInfo('Europe/Lisbon')
PAGE = 'https://nunovinhas-creator.github.io/goal-roi/'
KIND = {'BETFAIR/pre': 'Betfair antes do jogo', 'BETFAIR/live': 'Betfair durante o jogo', 'STAKE_LOAD': 'Stake', 'POLYMARKET': 'Polymarket', 'OPEN_GAME': 'Jogo aberto',
        'TOTALS_FORM': 'Forma de golos', 'WEAKENED_LINEUP': 'Alinhamento enfraquecido', 'GOAL_BREWING': 'Golo a caminho',
        'OPEN_GAME_NO_ODDS': 'Jogo aberto sem odd', 'TOTALS_FORM_NO_ODDS': 'Forma de golos sem odd'}
UTC = timezone.utc


class NotifyError(RuntimeError):
    pass


class StateMissing(NotifyError):
    """`notify_state.json` não existe: uma instalação com histórico nunca recomeça do zero (revisão #5920565094, P2)."""


class StateRegressed(NotifyError):
    """O estado é anterior ao registo de entregas (reposto ou reiniciado): recusar em vez de reenviar."""


class DeliveryFailed(NotifyError):
    """Falha confirmada: a mensagem não foi publicada (o Telegram recusou, ou a ligação nunca chegou a fazer-se)."""


class DeliveryUncertain(NotifyError):
    """Não se sabe se a mensagem foi publicada (tempo esgotado, ligação cortada, resposta ilegível, erro 5xx)."""


class DeliveryIncomplete(NotifyError):
    """A execução terminou com avisos falhados, incertos ou retirados. Já estão gravados no estado e no registo; nada é
    reenviado por causa disto. Serve só para a falha não passar por sucesso (código de saída do passo)."""


FAILED_EXIT = 3     # falha efetiva: o passo fica assinalado (continue-on-error no workflow), a recolha continua
PROBLEMS = ('failed', 'uncertain', 'withdrawn')


def send(token, chat, text):
    """Um só pedido. Devolve só com confirmação positiva (`ok: true`); senão DeliveryFailed ou DeliveryUncertain."""
    req = Request('https://api.telegram.org/bot%s/sendMessage' % token,
                  data=urlencode({'chat_id': chat, 'text': text[:MAX_TEXT], 'disable_web_page_preview': 'true'}).encode('utf-8'))
    try:
        with urlopen(req, timeout=20) as r:
            body = json.loads(r.read(200_000))
    except HTTPError as exc:                     # nunca imprimir o endereço: contém o token
        if 400 <= exc.code < 500:
            raise DeliveryFailed('HTTP %d' % exc.code) from None
        raise DeliveryUncertain('HTTP %d' % exc.code) from None
    except URLError as exc:
        if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
            raise DeliveryFailed('no connection') from None
        raise DeliveryUncertain('connection interrupted') from None
    except (TimeoutError, OSError, ValueError):
        raise DeliveryUncertain('no readable answer') from None
    if not isinstance(body, dict) or body.get('ok') is not True:
        raise DeliveryFailed('not ok')


def pct(v):
    return '—' if v is None else ('%.0f %%' % (v * 100))


def dec(v):
    return ('%+.2f' % v).replace('.', ',')


def odd(v):
    return str(v).replace('.', ',')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def iso(t):
    return t.isoformat(timespec='seconds')


def parse_time(v):
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def lisbon(v):
    t = parse_time(v)
    return t.astimezone(LISBON).strftime('%H:%M de %d/%m') if t else None


def still_bettable(w, now):
    """Oportunidade ainda atual em `now` (P1 de 17/11/2026): a regra única de `freshness` (hora ORIGINAL do sinal; ao vivo
    até 30 minutos; pré-jogo só antes de um início conhecido; jogo terminado, datas ausentes, inválidas ou incoerentes:
    não). Vale para todos os avisos de aposta, indícios e alertas."""
    return freshness.current(w, now)


# ---------------------------------------------------------------------------------------------------------------------
# Certificação: verificação estrutural (fail-closed)

def validation_of(report):
    v = (report.get('brain') or {}).get('validation')
    return v if isinstance(v, dict) else {}


def certification_active(report):
    v = validation_of(report)
    return (v.get('method') == VALIDATION_METHOD and v.get('state') == 'active' and v.get('ledger_valid') is True
            and isinstance(v.get('certified'), list))


def hypothesis_row(report, hid):
    """Linha da hipótese: o bloco de apresentação (PR 5) ou, em relatórios anteriores, o `top`."""
    brain = report.get('brain') or {}
    rows = (brain.get('presentation') or {}).get('hypotheses')
    if isinstance(rows, dict) and isinstance(rows.get(hid), dict):
        return rows[hid]
    return next((t for t in brain.get('top') or [] if isinstance(t, dict) and t.get('id') == hid), None)


def certificate(report, hid):
    """Certificado ATIVO, VÁLIDO e da PRÓPRIA hipótese, ou None. Exige: método aceite, registo ativo e verificado, o id na
    lista de certificadas e, na linha da hipótese, uma confirmação com seleção (rank) e instante de certificação."""
    if not isinstance(hid, str) or not certification_active(report):
        return None
    v = validation_of(report)
    if hid not in [i for i in v['certified'] if isinstance(i, str)]:
        return None
    row = hypothesis_row(report, hid)
    conf = row.get('confirmation') if isinstance(row, dict) else None
    if not isinstance(conf, dict) or conf.get('certifiable') is not True:
        return None
    rank, at = conf.get('rank'), parse_time(conf.get('certified_utc'))
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1 or at is None:
        return None
    return {'rank': rank, 'certified_utc': iso(at.astimezone(UTC)), 'ledger_head': v.get('ledger_head_sha256')}


def certified_ids(report):
    """Táticas com certificado verificado; qualquer outro relatório não tem nenhuma (fail-closed)."""
    v = validation_of(report)
    if not certification_active(report):
        return set()
    return {i for i in v['certified'] if isinstance(i, str) and certificate(report, i)}


def eligible_statuses(report):
    """Estados elegíveis. Uma "forte"/"confirmada" sem certificado verificado é tratada como promissora: nunca é anunciada
    como validada (PR 3, revisão #5911487915; PR 5: o certificado tem de pertencer à própria hipótese)."""
    brain = report.get('brain') or {}
    statuses = brain.get('status_list')
    if brain.get('evidence_scope') != EVIDENCE_SCOPE or not isinstance(statuses, dict):
        return None
    allowed = {'em observação', 'promissora', 'forte', 'confirmada', 'abandonada'}
    if any(not isinstance(k, str) or v not in allowed for k, v in statuses.items()):
        return None
    certified = certified_ids(report)
    return {k: ('promissora' if v in VALID and k not in certified else v) for k, v in statuses.items()}


INACTIVE = {'not_activated': 'certificação desligada', 'ledger_invalid': 'certificação inválida',
            'history_unverified': 'histórico da certificação por verificar'}


def scientific_label(report, hid=None):
    """A ÚNICA origem de afirmações sobre certificação. -> (texto, positivo). Positivo só com certificado verificado da hipótese
    (e o texto termina no próprio id, para que a afirmação fique associada à hipótese certa)."""
    if not certification_active(report):
        why = INACTIVE.get(validation_of(report).get('state'), 'certificação indisponível')
        if hid is None:
            return 'Estado científico: nenhuma tática certificada — %s.' % why, False
        return 'Estado científico: NÃO CERTIFICADA — %s.' % why, False
    if hid is None:
        return 'Estado científico: certificação ativa (registo verificado, %d entradas).' % (validation_of(report).get('ledger_entries') or 0), False
    c = certificate(report, hid)
    if c is None:
        return 'Estado científico: NÃO CERTIFICADA — sem certificado para esta hipótese.', False
    return ('Estado científico: CERTIFICADA — método %s, registo verificado, seleção n.º %d, certificada em %s UTC: %s'
            % (VALIDATION_METHOD, c['rank'], c['certified_utc'][:16].replace('T', ' '), hid)), True


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()


CLAIM = re.compile(r'(valid|certific|confirm)[a-z]*|fortes?')
NEGATED_BY = ((('nao',), ('sem',), ('nunca',)),                                   # "não certificada", "sem certificado"
              (('nunca', 'foi'), ('nao', 'foi'), ('nao', 'esta'), ('nao', 'e')),   # "nunca foi validada"
              (('deixou', 'de', 'estar'), ('deixou', 'de', 'ser')))                # "deixou de estar validada"
NOUNS = {'tatica', 'taticas', 'hipotese', 'hipoteses'}
SYSTEM_STATES = {'desligada', 'invalida', 'indisponivel', 'inativa'}


def positive_claims(line, report):
    """Palavras de afirmação (validada, certificada, confirmada, forte; qualquer flexão, acentos e maiúsculas) que não estão
    negadas por uma das formas fechadas de NEGATED_BY (ou "nenhuma tática ...") nem descrevem o estado do sistema de
    certificação. Qualquer outra construção conta como afirmação (fail-closed)."""
    toks, out = re.findall(r'[a-z0-9]+', fold(line)), []
    for i, tok in enumerate(toks):
        if not CLAIM.fullmatch(tok):
            continue
        nxt = toks[i + 1:i + 3]
        if tok == 'certificacao' and nxt[:1] and (nxt[0] in SYSTEM_STATES or nxt == ['por', 'verificar']
                                                    or (nxt[0] == 'ativa' and certification_active(report))):
            continue
        if any(i >= len(neg) and tuple(toks[i - len(neg):i]) in forms for forms in NEGATED_BY for neg in forms[:1]):
            continue
        if i >= 2 and toks[i - 2] in ('nenhuma', 'nenhum') and toks[i - 1] in NOUNS:
            continue
        out.append(tok)
    return out


def guard_claims(text, report, claims=()):
    """Barreira final (fail-closed): True só se cada hipótese reivindicada tem certificado verificado, o seu rótulo exato está
    no texto, e cada linha com uma afirmação positiva termina no id de uma dessas hipóteses."""
    verified = []
    for hid in claims:
        label, positive = scientific_label(report, hid)
        if not positive or label not in text:
            return False
        verified.append(hid)
    for line in text.split('\n'):
        if positive_claims(line, report) and not any(line.rstrip().endswith(' ' + hid) for hid in verified):
            return False
    return True


# ---------------------------------------------------------------------------------------------------------------------
# Migrações silenciosas (PR 1-3 e PR 5)

def migration_key(report):
    """Marker of the status reference a report belongs to.

    PR1 reports (no brain.status_rules) keep the original 'eligible_statuses_v1' key, so the PR1 migration already
    recorded in production is never repeated. A report produced under new status rules (PR2: independent games and
    hysteresis) gets its own key, so a rule change re-baselines the reference silently exactly once.
    """
    rules = (report.get('brain') or {}).get('status_rules')
    return STATUS_MIGRATION if not rules else 'status_rules:%s' % rules


def migrate_status_reference(report, state, now):
    """One-time silent baseline change per reference key. Does not touch delivery history or send."""
    new = eligible_statuses(report)
    if new is None:
        return False
    key = migration_key(report)
    migrations = state.get('migrations') or {}
    if key in migrations:
        return False
    record = {'at_utc': iso(now),
              'report_generated_at_utc': report.get('generated_at_utc'),
              'previous_statuses': copy.deepcopy(state.get('statuses')),
              'baseline_statuses': copy.deepcopy(new)}
    if FORMAT_MIGRATION in migrations:      # PR 5: a nova referência de regras também é a nova referência anunciada
        record['previous_announced_statuses'] = copy.deepcopy(state.get('announced_statuses'))
        state['announced_statuses'] = copy.deepcopy(new)
        state.setdefault('health', {})['reference'] = health_metrics(report)
        state['health'].pop('degraded_since', None)
    state.setdefault('migrations', {})[key] = record
    state['statuses'] = copy.deepcopy(new)
    return True


def migrate_format(report, state, now):
    """PR 5, uma só vez e sem enviar nada: semeia a referência anunciada com a referência atual, marca como avisados os sinais
    em aberto já avisados no formato antigo e fixa a referência de saúde do relatório. Nenhum campo existente muda."""
    if FORMAT_MIGRATION in (state.get('migrations') or {}):
        return False
    new = eligible_statuses(report)
    if new is None:
        return False
    announced = copy.deepcopy(state['statuses']) if isinstance(state.get('statuses'), dict) else copy.deepcopy(new)
    sent, items = set(state.get('watch_sent') or []), {}
    for w in (report.get('brain') or {}).get('watch') or []:
        if legacy_key(w) in sent:
            items[item_key(w)] = {'at_utc': iso(now), 'key': 'migration:' + FORMAT_MIGRATION}
    state.setdefault('migrations', {})[FORMAT_MIGRATION] = {
        'at_utc': iso(now), 'report_generated_at_utc': report.get('generated_at_utc'),
        'sent_total': state.get('sent_total', 0), 'watch_sent': len(state.get('watch_sent') or []),
        'previous_state_keys': sorted(state), 'announced_statuses': copy.deepcopy(announced), 'seeded_items': sorted(items)}
    state['announced_statuses'] = announced
    state.setdefault('notified_items', {}).update(items)
    state.setdefault('health', {})['reference'] = health_metrics(report)
    return True


def resync_after_foreign_writer(state, now):
    """Reversão seguida de nova integração: se outro escritor (o código anterior à PR 5) correu depois da última execução
    desta versão, ele anunciou as mudanças com a sua referência (`statuses`). A referência anunciada passa a essa, em silêncio
    e com registo, para nada ser anunciado duas vezes. Os avisos de jogos já estão protegidos pelas chaves antigas
    (`watch_sent`), o resumo por `summary_day` e os regimes por `operational_regime`, que o código anterior mantém."""
    mine = state.get('format_last_run_utc')
    if FORMAT_MIGRATION not in (state.get('migrations') or {}) or not mine or state.get('last_run_utc') == mine:
        return False
    state.setdefault('format_resyncs', []).append({'at_utc': iso(now), 'foreign_last_run_utc': state.get('last_run_utc'),
                                                   'own_last_run_utc': mine, 'previous_announced': copy.deepcopy(state.get('announced_statuses'))})
    state['announced_statuses'] = copy.deepcopy(state.get('statuses') or {})
    state['format_last_run_utc'] = state.get('last_run_utc')
    return True


def save_state(path, state):
    """Atomic local replacement; caller must keep the existing single-writer policy."""
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.notify-state-', delete=False) as f:
            temp = f.name
            json.dump(state, f, indent=1, ensure_ascii=False, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def migrate_file(now=None):
    """Offline CLI: migrate only; no credentials, sender, report generation or counters (PR 1-3; unchanged by PR 5)."""
    report = load_json(data_dir() / 'report.json', None)
    if not report or eligible_statuses(report) is None:
        raise NotifyError('eligible report required')
    path = data_dir() / STATE_NAME
    # Fail closed: a missing/corrupt existing state must not erase delivery history.
    state = json.loads(path.read_text(encoding='utf-8'))
    changed = migrate_status_reference(report, state, now or datetime.now(UTC))
    if changed:
        save_state(path, state)
    print('NOTIFY_STATUS_MIGRATED' if changed else 'NOTIFY_STATUS_UNCHANGED')
    return changed


def migrate_format_file(now=None):
    """PR 5, offline (`--migrate-format`): só a migração silenciosa `telegram_format_v2`. Sem credenciais, sem envio, sem
    registo de entregas e sem contadores. Exige que a referência de regras do relatório já esteja migrada."""
    report = load_json(data_dir() / 'report.json', None)
    if not report or eligible_statuses(report) is None:
        raise NotifyError('eligible report required')
    path = data_dir() / STATE_NAME
    state = json.loads(path.read_text(encoding='utf-8'))
    log = path.parent / LOG_NAME
    check_integrity(state, parse_log(log.read_bytes() if log.exists() else b'')[0])     # P2-R: nunca migrar um estado reiniciado
    if migration_key(report) not in (state.get('migrations') or {}):
        raise NotifyError('status reference of this report not migrated')
    changed = migrate_format(report, state, now or datetime.now(UTC))
    if changed:
        save_state(path, state)
    print('NOTIFY_FORMAT_MIGRATED' if changed else 'NOTIFY_FORMAT_UNCHANGED')
    return changed


# ---------------------------------------------------------------------------------------------------------------------
# Saúde do relatório (E4)

def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def health_metrics(report):
    brain, audit = report.get('brain') or {}, report.get('audit') or {}
    return {'evidence_scope': brain.get('evidence_scope'), 'status_rules': brain.get('status_rules'),
            'validation_state': validation_of(report).get('state'),
            'eligible': _count((brain.get('eligibility') or {}).get('eligible')), 'hypotheses': _count(brain.get('hypotheses')),
            'settled_bets': _count(report.get('settled_bets')), 'hl_results': _count(audit.get('hl_results')),
            'bsd_results': _count(audit.get('bsd_results')),
            'statuses': len(brain.get('status_list')) if isinstance(brain.get('status_list'), dict) else None}


def health_problems(ref, cur, explained=False):
    out = []
    for k, why in (('evidence_scope', 'âmbito da evidência mudou'), ('status_rules', 'regras de estado mudaram')):
        if ref.get(k) != cur.get(k):
            out.append(why)
    if ref.get('validation_state') != cur.get('validation_state') and not (ref.get('validation_state') == 'not_activated'
                                                                          and cur.get('validation_state') == 'active'):
        out.append('estado de verificação do registo mudou')   # sem palavras de afirmação: o resumo cita este texto
    for k, why in (('eligible', 'universo elegível caiu'), ('hypotheses', 'hipóteses em falta'), ('settled_bets', 'resultados em falta')):
        if ref.get(k) is not None and (cur.get(k) is None or cur[k] < ref[k] * (1 - COUNT_DROP_TOLERANCE)):
            out.append(why)
    for k in ('hl_results', 'bsd_results'):     # resultados externos só crescem: uma queda é uma fonte em falha
        if ref.get(k) is not None and (cur.get(k) is None or cur[k] < ref[k]):
            out.append('fonte de resultados em falha (%s)' % k.split('_')[0].upper())
    if ref.get('statuses') and not cur.get('statuses') and not explained:
        out.append('lista de estados vazia')          # sem que o relatório diga, hipótese a hipótese, que voltaram a observação
    return out


def check_health(report, state, now):
    """-> (saudável, motivos). Só relatórios com estados elegíveis mexem na referência; uma degradação estável há 24 h passa a
    referência (a mudança retida sai nessa altura: atrasa, nunca descarta)."""
    cur, h = health_metrics(report), state.setdefault('health', {})
    ref = h.get('reference')
    if ref is None:
        h['reference'] = cur
        return True, []
    current = eligible_statuses(report) or {}
    explained = all(explicit_status(report, hid, current) is not None for hid in state.get('announced_statuses') or {})
    problems = health_problems(ref, cur, explained)
    if not problems:
        if ref != cur:
            h['reference'] = cur
        h.pop('degraded_since', None)
        h.pop('problems', None)
        return True, []
    since = parse_time(h.get('degraded_since'))
    if since is None:
        h['degraded_since'], since = iso(now), now
    h['problems'] = problems
    if now - since >= timedelta(hours=DEGRADED_ACCEPT_HOURS):
        h.setdefault('accepted', []).append({'at_utc': iso(now), 'since_utc': h.pop('degraded_since'), 'problems': h.pop('problems')})
        h['reference'] = cur
        return True, []
    return False, problems


# ---------------------------------------------------------------------------------------------------------------------
# Agrupamento de hipóteses equivalentes (só apresentação)

def groups_of(report, hids):
    """Grupos de hipóteses equivalentes entre `hids` (ligação completa, compatibilidade e Jaccard >= TAU nos jogos; PR 4).
    Representante: a com mais jogos independentes (depois o id). Sem jogos conhecidos: grupo próprio."""
    def games(hid):
        row = hypothesis_row(report, hid) or {}
        return row.get('games') if isinstance(row.get('games'), int) else 0
    order = sorted(set(hids), key=lambda h: (-games(h), h))
    items, alone = [], []
    for hid in order:
        row = hypothesis_row(report, hid) or {}
        ids, fam, conds = row.get('game_ids'), row.get('family'), row.get('conditions')
        if isinstance(ids, list) and isinstance(fam, str) and isinstance(conds, list):
            try:
                items.append((hid, {'family': fam, 'conditions': [list(c) for c in conds], 'expect': row.get('expect')}, frozenset(ids)))
                continue
            except TypeError:
                pass
        alone.append([hid])
    out = equivalence.groups(items, TAU, GROUP_MIN_GAMES) + alone
    rank = {h: i for i, h in enumerate(order)}
    return sorted(out, key=lambda g: rank[g[0]])


# ---------------------------------------------------------------------------------------------------------------------
# Avisos

class Notice:
    """bets: só nos avisos de aposta, uma lista por aposta (jogo, mercado, seleção) com os sinais que a sustentam; serve para
    revalidar a oportunidade com o relógio real imediatamente antes do envio (`still_current`). Vazio nos outros avisos."""

    def __init__(self, key, kind, lines, effects, claims=(), hids=(), bets=()):
        self.key, self.kind, self.effects, self.claims, self.hids = key, kind, effects, list(claims), list(hids)
        self.bets = [list(b) for b in bets]
        self.text = fit(lines)

    def still_current(self, t):
        """Todas as apostas do aviso continuam a ser oportunidades atuais em `t` (cada uma por pelo menos um dos seus
        sinais). Conservador: basta uma aposta expirada para o aviso inteiro não sair, porque o texto já fixado na intenção
        a apresentaria como atual. Avisos sem apostas (resumo, estado, regime, boas-vindas): sempre True."""
        return all(any(still_bettable(w, t) for w in bet) for bet in self.bets)


def fit(lines):
    """<= MAX_TEXT caracteres sem cortar o topo (estado científico e título): corta linhas do fim e assinala o corte."""
    lines = [x for x in lines if x is not None]           # '' é uma linha em branco intencional (blocos do resumo)
    text = '\n'.join(lines)
    if len(text) <= MAX_TEXT:
        return text
    keep, dropped = list(lines), 0
    while len(keep) > 2 and len('\n'.join(keep)) + 40 > MAX_TEXT:
        keep.pop()
        dropped += 1
    text = '\n'.join(keep + ['… (mensagem encurtada: %d linhas omitidas)' % dropped])
    return text[:MAX_TEXT]


def labels(report, hids):
    """Linhas do estado científico (topo da mensagem): uma por rótulo distinto, pela ordem das hipóteses."""
    out, claims = [], []
    for hid in hids or [None]:
        text, positive = scientific_label(report, hid)
        if positive:
            claims.append(hid)
        if text not in out:
            out.append(text)
    return out, claims


def legacy_key(w):
    return '%s|%s|%s' % (w.get('date_utc'), w.get('match'), w.get('selection'))


def game_key(w):
    mid = text_identity(w.get('match_id'))
    if mid:
        return 'id:' + mid
    return 'legacy:%s|%s' % (w.get('match'), (w.get('kickoff_utc') or w.get('date_utc') or '')[:10])


def alert_reason(report, w):
    return next((x for x in w.get('reasons') or [] if isinstance(x, dict) and x.get('status') in VALID and x.get('certified') is True
                 and certificate(report, x.get('id'))), None)


def item_level(report, w):
    return 'alerta' if alert_reason(report, w) else 'indício'


def item_key(w):
    """Identidade da APOSTA (jogo, mercado, seleção). O nível (indício ou alerta) não faz parte dela: a mesma aposta nunca é
    avisada duas vezes, nem quando a hipótese que a sustenta passa a certificada (essa mudança é outro acontecimento, o aviso
    de estado "✅ Tática validada"). Revisão #5920565094, P1."""
    return 'item|%s|%s|%s' % (game_key(w), w.get('market') or '', w.get('selection') or '')


def legacy_pairs(watch_sent):
    out = {}
    for k in watch_sent:
        parts = k.split('|') if isinstance(k, str) else []
        if len(parts) >= 3:
            t = parse_time(parts[0])
            if t:
                out.setdefault(('|'.join(parts[1:-1]), parts[-1]), []).append(t)
    return out


def legacy_hit(pairs, w):
    t = parse_time(w.get('date_utc'))
    return bool(t) and any(abs(t - x) <= timedelta(days=LEGACY_PAIR_DAYS) for x in pairs.get((w.get('match'), w.get('selection')), ()))


def game_notices(report, state, now):
    """Contrato (revisão #5920565094): cada APOSTA (jogo, mercado, seleção) é avisada no máximo uma vez; as apostas novas
    de um mesmo jogo numa recolha saem numa só mensagem. Reforços (outra hora de sinal, outra multiplicidade) e a passagem de
    indício a alerta da mesma aposta nunca geram outro aviso. Uma seleção genuinamente nova num jogo já avisado gera uma
    atualização desse jogo, só com a(s) aposta(s) nova(s), identificada como tal."""
    notified, sent = state.get('notified_items') or {}, set(state.get('watch_sent') or [])
    pairs, games = legacy_pairs(sent), {}
    for w in (report.get('brain') or {}).get('watch') or []:
        if not isinstance(w, dict) or not isinstance(w.get('reasons'), list) or not w['reasons']:
            continue
        if not still_bettable(w, now):
            continue          # P1 17/11/2026: fora de tempo, em QUALQUER nível (indício ou alerta). Barreira final, independente
            #                   de `brain.watch`: nunca é marcado como avisado, por isso nunca fica em fila para execuções seguintes.
        level = item_level(report, w)
        ik = item_key(w)
        if ik in notified or legacy_key(w) in sent or legacy_hit(pairs, w):
            continue          # aposta já avisada (também no formato antigo, com outra hora de sinal)
        g = games.setdefault(game_key(w), {'items': {}, 'alert': False, 'last': w.get('date_utc') or '', 'sample': w})
        it = g['items'].setdefault(ik, {'level': 'indício', 'entries': []})
        it['entries'].append(w)
        if level == 'alerta':
            it['level'] = 'alerta'
            g['alert'] = True
        g['last'] = max(g['last'], w.get('date_utc') or '')
    for gk, g in games.items():      # apostas deste jogo já avisadas antes: esta mensagem é uma atualização
        own = {k for k in notified if k.startswith('item|%s|' % gk)}
        old = {(m, sel) for (m, sel), ts in pairs.items() if m == g['sample'].get('match') and legacy_hit({(m, sel): ts}, dict(g['sample'], selection=sel))}
        g['previous'] = max(len(own), len(old))
    ordered = sorted(games.items(), key=lambda kv: (not kv[1]['alert'], -_epoch(kv[1]['last']), kv[0]))
    return [game_notice(report, state, now, gk, g) for gk, g in ordered[:MAX_WATCH_PER_RUN]]


def _epoch(v):
    t = parse_time(v)
    return t.timestamp() if t else 0.0


def game_notice(report, state, now, gk, g):
    """Aviso curto para apostar (formato `telegram_bet_short_v1`). Uma aposta simples dá exatamente três linhas:
        A seguir: <jogo>
        Encaixa em: <tática> (<estado>)
        Aposta: <seleção> @ <odd>
    Várias táticas ou seleções: cada APOSTA aparece uma só vez, por baixo das táticas que a sustentam (as apostas com o
    mesmo conjunto de táticas ficam juntas). Um ALERTA (só com certificado ativo, verificado e da própria tática) acrescenta,
    no fim, o rótulo científico exato dessa tática, que `guard_claims` exige para qualquer afirmação positiva. Nada de ROI,
    probabilidades, jogos independentes, evidência ou textos finais: essa informação vai para o resumo diário."""
    items = sorted(g['items'].items(), key=lambda kv: (kv[1]['level'] != 'alerta', kv[0]))
    first = items[0][1]['entries'][0]
    reasons, covers, alerted, bets = {}, {}, set(), []
    for ik, it in items:
        latest = max(it['entries'], key=lambda w: w.get('date_utc') or '')
        sel = '%s%s' % (latest.get('selection') or 'mais um golo', (' @ ' + odd(latest['odds'])) if latest.get('odds') else '')
        if sel not in bets:
            bets.append(sel)
        for w in it['entries']:
            for x in w['reasons']:
                if isinstance(x, dict) and isinstance(x.get('id'), str):
                    reasons.setdefault(x['id'], x)
                    if sel not in covers.setdefault(x['id'], []):
                        covers[x['id']].append(sel)
        if it['level'] == 'alerta':
            alerted.add(alert_reason(report, latest)['id'])
    # Certificadas e não certificadas nunca no mesmo grupo: o alerta só fala das certificadas, as outras são indício.
    grouped = groups_of(report, [h for h in reasons if h in alerted]) + groups_of(report, [h for h in reasons if h not in alerted])
    shown = grouped[:MAX_GROUPS_PER_MESSAGE]
    match = first.get('match') or 'Jogo'
    update = ' (nova seleção num jogo já avisado)' if g.get('previous') else ''
    lines = [('🟢 ALERTA%s: %s' % (update, match)) if g['alert'] else ('A seguir%s: %s' % (update, match))]

    def tactic(gr):
        rep = gr[0]
        r = reasons.get(rep) or next(reasons[h] for h in gr if h in reasons)
        more = len(gr) - 1
        if rep in alerted:
            return '🟢 Tática validada (%s%s): %s' % (r['status'], ('; e mais %d equivalente%s' % (more, 's' if more > 1 else '')) if more else '', rep)
        return 'Encaixa em: %s (%s)%s' % (rep, 'promissora' if r.get('status') in VALID else r.get('status'),
                                          (' e mais %d equivalente%s' % (more, 's' if more > 1 else '')) if more else '')
    # Cada aposta uma só vez, agrupada pelas táticas (mostradas) que a sustentam.
    clusters = {}
    for sel in bets:
        support = tuple(i for i, gr in enumerate(shown) if any(sel in covers.get(h, ()) for h in gr))
        clusters.setdefault(support, []).append(sel)
    unsupported = clusters.pop((), [])
    for support, sels in clusters.items():
        lines += [tactic(shown[i]) for i in support]
        lines.append('Aposta: %s' % '; '.join(sels))
    if len(grouped) > len(shown):
        lines.append('… e mais %d grupos de táticas.' % (len(grouped) - len(shown)))
    if unsupported:
        lines.append('Aposta: %s' % '; '.join(unsupported))
    claims = [gr[0] for gr in shown if gr[0] in alerted]
    for hid in claims:                       # rótulo exato (fail-closed): só para a tática do alerta, verificada agora
        text, positive = scientific_label(report, hid)
        if positive:
            lines.append(text)
    effects = []
    for gr in shown:
        for h in gr:
            row = hypothesis_row(report, h) or {}
            if row.get('prospective_games') is not None:
                effects.append(['notice_games', h, row['prospective_games']])
    for ik, it in items:
        effects.append(['item', ik])
        for w in it['entries']:
            effects.append(['watch_sent', legacy_key(w)])
    key = 'game|%s|%s' % (gk, sha('\n'.join(k for k, _ in items))[:12])
    return Notice(key, 'alert' if g['alert'] else 'hint', lines, effects, claims, list(reasons), bets=[it['entries'] for _, it in items])


def explicit_status(report, hid, current):
    """Estado da hipótese no relatório; None = desconhecido (ausente). "em observação" só quando o relatório o diz para ela."""
    if hid in current:
        return current[hid]
    row = hypothesis_row(report, hid)
    if isinstance(row, dict) and row.get('status') == 'em observação':
        return 'em observação'
    return None


def change_verified(report, hid, new):
    """A mudança consta das mudanças fixadas pelo cérebro (histerese da PR 2) e não há outra pendente."""
    row = hypothesis_row(report, hid)
    if not isinstance(row, dict) or row.get('pending_status') is not None:
        return False
    last = row.get('last_change')
    raw = ((report.get('brain') or {}).get('status_list') or {}).get(hid, 'em observação')
    return isinstance(last, dict) and last.get('to') == raw and raw in (new, 'forte', 'confirmada')


def status_changes(report, state, now, current, healthy):
    """Mudanças de estado ainda não anunciadas, prontas para o resumo diário: [(antigo, novo, grupo de equivalentes)].
    Nada é anunciado fora do resumo: este cálculo só corre quando o resumo do dia está em curso. As barreiras são as da PR 5
    (relatório saudável, hipótese presente, sem estado pendente, mudança fixada pelo cérebro; a perda de certificação passa
    mesmo com relatório degradado). Uma reversão no mesmo dia cancela-se sozinha (compara-se só o anunciado com o atual)."""
    announced = state.setdefault('announced_statuses', {})
    transitions = {}
    for hid in sorted(set(announced) | set(current)):
        old, new = announced.get(hid, 'em observação'), explicit_status(report, hid, current)
        if new is None and old in VALID and certificate(report, hid) is None:
            new = 'em observação'                         # validada que desaparece sem certificado: perda, nunca escondida
        if new is None or new == old:
            continue
        if old in VALID and new not in VALID:
            pass                                          # perda de certificação: anunciada mesmo com relatório degradado
        elif not healthy or not change_verified(report, hid, new):
            continue
        transitions.setdefault((old, new), []).append(hid)
    out = []
    for (old, new), hids in sorted(transitions.items(), key=lambda kv: (not (kv[0][1] in VALID or kv[0][0] in VALID), kv[0])):
        for gr in groups_of(report, hids):
            out.append((old, new, gr))
    return out


SHORT_INACTIVE = {'not_activated': 'desligada', 'ledger_invalid': 'inválida', 'history_unverified': 'por verificar'}


def roi_short(v):
    if v is None:
        return None
    return ('%s%.1f%%' % ('+' if v >= 0 else '−', abs(v) * 100)).replace('.', ',')


def change_block(report, old, new, gr):
    """Bloco curto de uma mudança de estado. -> (linhas, afirmações). Afirmação positiva só para uma tática que passou a
    validada, com o rótulo exato de `scientific_label` (a barreira `guard_claims` continua a ser a última palavra)."""
    rep, row = gr[0], hypothesis_row(report, gr[0]) or {}
    cap = lambda s_: s_[:1].upper() + s_[1:]
    if new in VALID:
        lines = ['✅ Tática validada, passou a %s (antes: %s): %s' % (new, old, rep)]
    elif old in VALID:
        lines = [rep, '⛔ Estado: deixou de estar validada, %s' % ('voltou a em observação' if new == 'em observação' else 'passou a %s' % new)]
    else:
        lines = [rep, 'Estado: %s (antes: %s)' % (cap(new), old)]
    games = row.get('games') if isinstance(row.get('games'), int) and not isinstance(row.get('games'), bool) else None
    roi = row.get('game_roi') if row.get('game_roi') is not None else row.get('roi')
    parts = (['%d jogos independentes' % games] if games is not None else []) + (['ROI %s' % roi_short(roi)] if roi is not None else [])
    if len(gr) > 1:
        parts.append('e mais %d equivalente%s' % (len(gr) - 1, 's' if len(gr) > 2 else ''))
    if parts:
        lines.append(' · '.join(parts))
    label, positive = scientific_label(report, rep)
    if positive:
        lines.append(label)
    elif not certification_active(report):
        lines.append('Certificação: %s' % SHORT_INACTIVE.get(validation_of(report).get('state'), 'indisponível'))
    else:
        lines.append('Certificação ativa; tática não certificada')
    return lines, ([rep] if positive else [])          # o rótulo positivo (exato, desta tática) é a afirmação a verificar


def regime_notices(report, state, now):
    operational = report.get('operational') or {}
    if operational.get('scope') != 'eligible_bets_only':
        return []
    old_reg = state.get('operational_regime')
    regime = [r for r in operational.get('regime') or [] if isinstance(r, dict)]
    out, held = [], {}
    if old_reg is not None:
        done = state.get('deliveries') or {}
        for r in regime:
            if r['state'] in ('piorou', 'melhorou') and old_reg.get(r['type']) != r['state']:
                key = 'regime|%s|%s>%s|%s' % (r['type'], old_reg.get(r['type']), r['state'], now.date().isoformat())
                if key in done and done[key].get('state') != 'pending':
                    continue
                held[r['type']] = old_reg.get(r['type'])
                head, _ = labels(report, [])
                out.append(Notice(key, 'regime', head + ['Mudança de comportamento (evidência elegível): %s %s. Acerto recente %s, antes %s.'
                                                         % (KIND.get(r['type'], r['type']), r['state'], pct((r.get('recent') or {}).get('hit_rate')),
                                                            pct((r.get('before') or {}).get('hit_rate')))], [['regime', r['type'], r['state']]]))
    new_reg = {}
    for r in regime:
        if r['type'] in held:
            if held[r['type']] is not None:
                new_reg[r['type']] = held[r['type']]
        else:
            new_reg[r['type']] = r['state']
    if old_reg != new_reg:
        state['operational_regime'] = new_reg
    return out


def summary_day(now):
    """Dia (Lisboa) cujo resumo já é devido: a partir das 22:00 de Lisboa (hora de verão e de inverno), senão None."""
    local = now.astimezone(LISBON)
    return local.date().isoformat() if local.hour >= SUMMARY_HOUR_LISBON else None


def summary_notice(report, state, now, changes=()):
    """O ÚNICO resumo diário (22:00 de Lisboa): as mudanças de estado das táticas desde o último resumo, em blocos curtos, e
    depois o resumo do dia. Cada mudança mostrada fica anunciada quando a intenção de envio é gravada (efeito `announce`); se
    não couberem todas, as restantes ficam para o resumo seguinte (nunca se perdem)."""
    local = now.astimezone(LISBON)
    day = local.date().isoformat()
    brain = report.get('brain') or {}
    today = next((d for d in report.get('daily') or [] if d.get('day') == day), {'bets': 0, 'settled': 0, 'wins': 0, 'units': 0.0})
    tl, c, t = report.get('timeline') or [], brain.get('counts') or {}, brain.get('llm_today') or {}
    current = eligible_statuses(report) or {}
    promising = [h for h, s in current.items() if s == 'promissora']
    certified = sorted(certified_ids(report))
    head, claims = labels(report, certified)
    tail = ['', 'Apostas de hoje: %d, com resultado %d, ganhas %d, saldo do dia %s un.' % (today['bets'], today['settled'], today['wins'], dec(today['units'])),
            'Saldo total: %s un. em %d apostas com resultado.' % (dec(tl[-1]['u'] if tl else 0.0), report.get('settled_bets') or 0),
            'Hipóteses: %d em teste; %d promissoras (%d grupos de equivalentes), %d abandonadas.'
            % (brain.get('hypotheses') or 0, c.get('promissora', 0), len(groups_of(report, promising)) if promising else 0, c.get('abandonada', 0))]
    counts, base = state.get('delivery_counts') or {}, state.get('summary_counts') or {}
    d = {k: counts.get(k, 0) - base.get(k, 0) for k in COUNTERS}
    if any(d.values()):
        tail.append('Avisos desde o último resumo: %d entregues, %d com falha, %d incertos (não reenviados), %d bloqueados, %d caducados.'
                    % (d['delivered'], d['failed'], d['uncertain'], d['blocked'], d['expired']))
    osc = state.get('oscillations') or []           # registos anteriores a este formato (já não se acrescentam)
    if osc:
        tail.append('Oscilações em menos de %d h (não anunciadas): %s.' % (OSCILLATION_HOURS, '; '.join(
            '%s: %s' % (o['id'], ' → '.join(o['path'])) for o in osc[:10])))
    health = state.get('health') or {}
    if health.get('degraded_since'):
        tail.append('Relatório degradado desde %s (%s): mudanças de estado retidas.' % (lisbon(health['degraded_since']), ', '.join(health.get('problems') or [])))
    tail += head + ['Modelo de linguagem hoje: %s chamadas, cerca de %s neurónios.' % (t.get('calls_today') or 0, int(t.get('neurons_today') or 0)), PAGE]
    lines, effects, hids, budget = ['Resumo das táticas — %s' % local.strftime('%d/%m')], [], list(certified), MAX_TEXT - len('\n'.join(tail)) - 120
    shown = hidden = 0
    for old, new, gr in changes:
        block, block_claims = change_block(report, old, new, gr)
        visible = guard_claims('\n'.join(block), report, block_claims)
        if visible and shown and len('\n'.join(lines + [''] + block)) > budget:
            break
        if visible:                       # um bloco recusado pela barreira não bloqueia o resumo inteiro
            lines += [''] + block
            claims += [h for h in block_claims if h not in claims]
            hids += [h for h in gr if h not in hids]
            shown += 1
        else:                             # como um aviso bloqueado: conta como tratado (no máximo uma vez), não é mostrado
            hidden += 1
        for h in gr:
            effects.append(['announce', h, old, new])
            row = hypothesis_row(report, h) or {}
            if row.get('prospective_games') is not None:
                effects.append(['notice_games', h, row['prospective_games']])
    if not changes:
        lines.append('Sem alterações de estado das táticas desde o último resumo.')
    if hidden:
        lines += ['', '%d alterações de estado não mostradas: texto recusado pela barreira de afirmações.' % hidden]
    if shown + hidden < len(changes):
        lines += ['', '… e mais %d alterações de estado: ficam para o próximo resumo.' % (len(changes) - shown - hidden)]
    effects.append(['summary', day])
    return Notice('summary|%s' % day, 'summary', lines + tail, effects, claims, hids)


def hello_notice(report):
    head, _ = labels(report, [])
    return Notice('hello', 'hello', head + ['Avisos do goal-roi ligados. Vais receber: indícios de jogos que encaixam em hipóteses promissoras, '
                                            'alertas de apostas e, às 22:00 (Lisboa), um resumo diário com as mudanças de estado das táticas.', PAGE], [['hello']])


def plan(report, state, now):
    """Relatório + estado -> avisos por esta ordem. Aplica ao estado só o que não depende de uma entrega (migrações
    silenciosas, referência de saúde, `statuses` a seguir o relatório, regimes sem aviso, oscilações). Os efeitos de cada
    aviso (o que fica marcado como avisado) só são aplicados quando a intenção de envio é gravada."""
    migrate_status_reference(report, state, now)
    migrate_format(report, state, now)
    resync_after_foreign_writer(state, now)
    out = [] if state.get('hello') else [hello_notice(report)]
    out += game_notices(report, state, now)
    new = eligible_statuses(report)
    changes = []
    due = summary_day(now)
    due = due if due and state.get('summary_day') != due else None
    if new is not None:
        healthy, _ = check_health(report, state, now)          # corre sempre: a referência de saúde não pode esperar pelas 22h
        if due:
            changes = status_changes(report, state, now, new, healthy)
        if state.get('statuses') != new:
            state['statuses'] = copy.deepcopy(new)
    out += regime_notices(report, state, now)
    if due:
        out.append(summary_notice(report, state, now, changes))
    return out


def apply_effects(state, effects, now):
    for e in effects:
        op = e[0]
        if op == 'hello':
            state['hello'] = True
        elif op == 'item':
            state.setdefault('notified_items', {})[e[1]] = {'at_utc': iso(now)}
        elif op == 'watch_sent':
            ws = state.setdefault('watch_sent', [])
            if e[1] not in ws:
                ws.append(e[1])
                ws.sort()
        elif op == 'notice_games':
            state.setdefault('notice_games', {})[e[1]] = e[2]
        elif op == 'announce':
            _, hid, old, new = e
            ann = state.setdefault('announced_statuses', {})
            if new == 'em observação':
                ann.pop(hid, None)
            else:
                ann[hid] = new
            state.setdefault('announced_changes', {})[hid] = {'from': old, 'to': new, 'at_utc': iso(now)}
        elif op == 'regime':
            state.setdefault('operational_regime', {})[e[1]] = e[2]
        elif op == 'summary':
            state['summary_day'] = e[1]
            state['summary_counts'] = dict(state.get('delivery_counts') or {})
            state['oscillations'] = []


def messages(report, state, now):
    """Compatibilidade (testes e ensaios): os textos que sairiam, com os efeitos aplicados como se todos fossem entregues.
    Não mexe em entregas, contadores nem registo. Avisos bloqueados por `guard_claims` não são devolvidos."""
    out = []
    for n in plan(report, state, now):
        apply_effects(state, n.effects, now)
        if guard_claims(n.text, report, n.claims):
            out.append(n.text)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Entrega "no máximo uma vez", com registo persistente

class FileStore:
    """Estado atómico (`notify_state.json`) + registo só a crescer (`notify_log.jsonl`), no mesmo diretório."""

    def __init__(self, directory):
        self.state_path, self.log_path = Path(directory) / STATE_NAME, Path(directory) / LOG_NAME

    def load_state(self):
        # Fail closed: a missing or corrupt state raises instead of being replaced by an empty history. A new installation
        # creates it explicitly (`--init-state`), never implicitly.
        if not self.state_path.exists():
            raise StateMissing('notify_state.json missing')
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def save_state(self, state):
        save_state(self.state_path, state)

    def read_log(self):
        return parse_log(self.log_path.read_bytes() if self.log_path.exists() else b'')

    def append_log(self, entries):
        if not entries:
            return
        data = ''.join(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n' for e in entries).encode('utf-8')
        fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size:
                with open(self.log_path, 'rb') as f:
                    f.seek(size - 1)
                    if f.read(1) != b'\n':
                        data = b'\n' + data      # linha cortada por uma queda: fica isolada, nada é reescrito
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)


class MemoryStore:
    """Para a pré-visualização: começa numa CÓPIA do estado e do registo e nunca escreve em disco."""

    def __init__(self, state=None, log=b''):
        self.state, self.log = copy.deepcopy(state or {}), bytes(log)

    def load_state(self):
        return copy.deepcopy(self.state)

    def save_state(self, state):
        self.state = json.loads(json.dumps(state))

    def read_log(self):
        return parse_log(self.log)

    def append_log(self, entries):
        if entries:
            self.log += ''.join(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n' for e in entries).encode('utf-8')


def parse_log(raw):
    entries, bad = [], 0
    for line in raw.decode('utf-8', errors='replace').split('\n'):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(e, dict) and isinstance(e.get('key'), str) and isinstance(e.get('event'), str):
            entries.append(e)
        else:
            bad += 1
    return entries, bad


def log_entry(now, key, event, rec, **extra):
    e = {'v': 1, 'at_utc': iso(now), 'key': key, 'event': event, 'kind': rec.get('kind')}
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


def _bump(state, outcome):
    counts = state.setdefault('delivery_counts', {})
    counts[outcome] = counts.get(outcome, 0) + 1


INSTALLATION = 'installation'          # marca explícita escrita só por `--init-state` (P2-R)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def check_integrity(state, entries):
    """Fail-closed (P2 e P2-R, revisões #5920565094 e #5921136533). O estado só é aceite se for:
    - uma instalação nova registada por `--init-state` (marca `installation`), ou
    - um histórico de produção: campos essenciais (`sent_total`, `watch_sent`, `statuses`) e pelo menos a marca da PR 1
      (`eligible_statuses_v1`; os estados das PR 1, 2 e 3 têm-na), com ou sem a marca PR 5.
    Um `{}` (ou outro estado sem história nem marca de instalação) é um estado reiniciado, mesmo sem registo de entregas.
    Ao lado de um registo de entregas, o estado tem de ter a marca PR 5 e não pode ter menos entregas do que na migração.
    Um estado mais antigo mas já PR 5 é reparado a partir do registo (`reconcile`)."""
    if not isinstance(state, dict):
        raise StateRegressed('state is not an object')
    migrations = state.get('migrations') if isinstance(state.get('migrations'), dict) else {}
    installed = isinstance(state.get(INSTALLATION), dict) and state[INSTALLATION].get('method') == 'init-state'
    history = (_int(state.get('sent_total')) and isinstance(state.get('watch_sent'), list) and isinstance(state.get('statuses'), dict)
               and STATUS_MIGRATION in migrations)
    if not installed and not history:
        raise StateRegressed('state is neither a recorded new installation nor a production history')
    marker = migrations.get(FORMAT_MIGRATION)
    if entries and not marker:
        raise StateRegressed('delivery log present but state has no %s marker' % FORMAT_MIGRATION)
    if isinstance(marker, dict) and _int(marker.get('sent_total')) and (state.get('sent_total') or 0) < marker['sent_total']:
        raise StateRegressed('sent_total below the value recorded at migration')


def init_state_file(now=None):
    """`--init-state`: só para uma instalação NOVA (sem estado e sem registo de entregas). Nunca substitui nada. Escreve a
    marca explícita `installation`, a única forma de um estado sem histórico ser aceite."""
    store = FileStore(data_dir())
    if store.state_path.exists() or (store.log_path.exists() and store.log_path.stat().st_size):
        raise NotifyError('refusing to initialise: state or delivery log already present')
    store.state_path.parent.mkdir(parents=True, exist_ok=True)
    save_state(store.state_path, {INSTALLATION: {'method': 'init-state', 'at_utc': iso(now or datetime.now(UTC))}})
    print('NOTIFY_STATE_INITIALISED')
    return True


def reconcile(store, state, now):
    """Recupera de uma queda em qualquer ponto, sem reenviar e sem perder contadores:
    1) uma intenção sem resultado (no estado ou no registo) passa a INCERTA (nunca é reenviada);
    2) um resultado que só o registo conhece (estado anterior reposto) é reposto no estado, com os seus efeitos e contadores;
    3) o que o estado conhece e o registo não (queda entre as duas gravações) é acrescentado ao registo, marcado reconstruído.
    Ordem das gravações: estado primeiro, depois o registo; repetir a reconciliação é inofensivo."""
    entries, bad = store.read_log()
    check_integrity(state, entries)
    recs = state.setdefault('deliveries', {})
    changed = False
    for key, r in recs.items():
        if r.get('state') == 'in_flight':
            r['state'], r['reason'] = 'uncertain', 'interrupted'
            r.setdefault('events', []).append('uncertain')
            _bump(state, 'uncertain')
            changed = True
    by_key, seen = {}, set()
    for e in entries:
        by_key.setdefault(e['key'], []).append(e)
        seen.add((e['key'], e['event']))
    for key, evs in by_key.items():
        r = recs.get(key)
        if r is not None and r.get('state') not in (None, 'pending'):
            continue
        names = [e['event'] for e in evs]
        terminal = [x for x in names if x in TERMINAL]
        open_intent = 'intent' in names and names[::-1].index('intent') < (names[::-1].index('withdrawn') if 'withdrawn' in names else len(names))
        outcome = terminal[-1] if terminal else ('uncertain' if open_intent else None)
        if outcome is None or (r is not None and (r.get('events') or [''])[-1] == 'withdrawn' and not terminal):
            continue                       # intenção retirada (não publicada, nada enviado): continua pendente
        intent = next((e for e in evs if e['event'] == 'intent'), {})
        r = recs.setdefault(key, {'kind': evs[0].get('kind'), 'created_utc': evs[0].get('at_utc')})
        r.update(state=outcome, reconstructed=True, events=[x for x in names if x != outcome] + [outcome], sha256=intent.get('sha256'))
        if isinstance(intent.get('effects'), list):
            apply_effects(state, intent['effects'], now)
        _bump(state, outcome)
        if outcome == 'delivered':
            state['sent_total'] = state.get('sent_total', 0) + 1
        changed = True
    missing, logged = [], {}
    for e in entries:
        logged[(e['key'], e['event'])] = logged.get((e['key'], e['event']), 0) + 1
    for key, r in recs.items():
        wanted = {}
        for ev in r.get('events') or []:
            wanted[ev] = wanted.get(ev, 0) + 1
            if wanted[ev] > logged.get((key, ev), 0):
                logged[(key, ev)] = logged.get((key, ev), 0) + 1
                missing.append(log_entry(now, key, ev, r, reconstructed=True, sha256=r.get('sha256') if ev in ('intent', 'delivered') else None))
    if changed:
        store.save_state(state)
    store.append_log(missing)
    return {'malformed_log_lines': bad, 'reconstructed_log_entries': len(missing), 'changed_state': changed}


def run_clock(now):
    """Relógio real da execução: `now` mais o tempo decorrido (monótono, imune a acertos do relógio do sistema). Em produção
    `now` é a hora real do início, por isso isto é a hora real; nos testes avança a partir do `now` simulado."""
    start = time.monotonic()
    return lambda: now + timedelta(seconds=time.monotonic() - start)


def _expire(store, state, now, n, rec, t, reason):
    """Estado terminal `expired` de um aviso de aposta cuja oportunidade expirou antes do envio. Nunca é uma entrega: não
    conta em `sent_total` nem é problema de entrega (`PROBLEMS`). Não mexe nos efeitos: antes da intenção não há nenhum; depois
    da intenção ficam os já gravados e publicados com ela (como em `failed`/`uncertain`/`blocked`), coerentes com o que
    `reconcile` reconstrói a partir do registo — a mesma aposta nunca volta a ser planeada nem enviada."""
    rec.update(state='expired', done_utc=iso(now), expired_check_utc=iso(t), reason=reason)
    rec['events'].append('expired')
    _bump(state, 'expired')
    store.save_state(state)
    store.append_log([log_entry(now, n.key, 'expired', rec, reason=reason, checked_utc=iso(t))])
    print('NOTIFY_EXPIRED_BEFORE_SEND' if reason == 'signal_expired_before_send' else 'NOTIFY_EXPIRED_BEFORE_INTENT')


def deliver(store, report, state, now, sender, token, chat, checkpoint=None, publish=None, clock=None):
    """Planeia e envia. Para cada aviso: intenção (efeitos + `in_flight`) gravada no estado e no registo ANTES do envio;
    resultado gravado depois. Uma falha ou incerteza para os envios desta execução (os seguintes ficam pendentes e só saem
    se ainda forem válidos na execução seguinte). publish (P0, `GitPublisher`): depois de gravar a intenção e ANTES de contactar
    o Telegram, publica-a no repositório remoto; se não conseguir, a intenção é retirada (nada foi enviado, o aviso fica
    pendente) e esta execução pára de enviar. -> (estado, entregues).

    Validade temporal (P1 17/11/2026, ressalva da auditoria da #41): o plano usa `now`, mas entre o plano e o envio passam
    os envios anteriores e a publicação git (até 3 × pull/push). Por isso cada aviso de aposta é revalidado com o relógio real
    (`clock`, por omissão `run_clock(now)`) (1) antes de gravar a intenção — expirado: `pending -> expired`, sem efeitos, sem
    publicação, sem envio — e (2) depois de a intenção estar publicada, imediatamente antes de contactar o Telegram —
    expirado: `intent -> expired`, sem envio. A barreira de publicação mantém-se: nada é enviado sem intenção publicada."""
    cp = checkpoint or (lambda name: None)
    clock = clock or run_clock(now)
    reconcile(store, state, now)
    recs = state.setdefault('deliveries', {})
    notices = [n for n in plan(report, state, now) if recs.get(n.key, {}).get('state') in (None, 'pending')]
    keys, new_log = {n.key for n in notices}, []
    for key, r in recs.items():
        if r.get('state') == 'pending' and key not in keys:
            r['state'] = 'expired'
            r['events'].append('expired')
            _bump(state, 'expired')
            new_log.append(log_entry(now, key, 'expired', r))
    for n in notices:
        if n.key not in recs:
            recs[n.key] = {'kind': n.kind, 'state': 'pending', 'events': ['pending'], 'created_utc': iso(now)}
            new_log.append(log_entry(now, n.key, 'pending', recs[n.key]))
    store.save_state(state)
    store.append_log(new_log)
    cp('planned')
    delivered = 0
    for n in notices:
        rec = recs[n.key]
        cp('before_intent')
        t = clock()
        if not n.still_current(t):
            _expire(store, state, now, n, rec, t, 'signal_expired_before_intent')
            continue
        if not guard_claims(n.text, report, n.claims):
            apply_effects(state, n.effects, now)
            rec.update(state='blocked', done_utc=iso(now), sha256=sha(n.text))
            rec['events'].append('blocked')
            _bump(state, 'blocked')
            store.save_state(state)
            store.append_log([log_entry(now, n.key, 'blocked', rec, sha256=rec['sha256'], reason='guard_claims')])
            continue
        before = copy.deepcopy(state)
        apply_effects(state, n.effects, now)
        rec.update(state='in_flight', attempt_utc=iso(now), sha256=sha(n.text), chars=len(n.text))
        rec['events'].append('intent')
        store.save_state(state)
        cp('after_intent_state')
        store.append_log([log_entry(now, n.key, 'intent', rec, sha256=rec['sha256'], effects=n.effects)])
        cp('after_intent')
        if publish is not None:
            published = publish(n.key)
            cp('after_publish')
            if not published:
                # Nada foi enviado: desfaz os efeitos, o aviso volta a pendente (pode sair numa execução seguinte, uma vez).
                state.clear()
                state.update(before)
                rec = state['deliveries'][n.key]
                rec['events'] += ['intent', 'withdrawn']
                _bump(state, 'withdrawn')
                store.save_state(state)
                store.append_log([log_entry(now, n.key, 'withdrawn', rec, reason='intent not published')])
                print('NOTIFY_PUBLISH_FAILED')
                break
        t = clock()                        # imediatamente antes da tentativa efetiva de envio
        if not n.still_current(t):
            _expire(store, state, now, n, rec, t, 'signal_expired_before_send')
            cp('after_expired')
            continue
        cp('before_send')
        try:
            sender(token, chat, n.text)
            outcome = 'delivered'
        except DeliveryFailed:
            outcome = 'failed'
        except Exception:                 # qualquer outra exceção: não se sabe se saiu
            outcome = 'uncertain'
        if outcome == 'delivered':
            cp('after_response')
        rec.update(state=outcome, done_utc=iso(now))
        rec['events'].append(outcome)
        _bump(state, outcome)
        if outcome == 'delivered':
            state['sent_total'] = state.get('sent_total', 0) + 1
            delivered += 1
        store.save_state(state)
        cp('after_record_state')
        store.append_log([log_entry(now, n.key, outcome, rec, sha256=rec['sha256'] if outcome == 'delivered' else None)])
        cp('after_record')
        if outcome != 'delivered':
            print('NOTIFY_DELIVERY_%s' % outcome.upper())
            break
    state['last_run_utc'] = state['format_last_run_utc'] = iso(now)
    store.save_state(state)
    return state, delivered


class GitPublisher:
    """Barreira P0 (revisão #5920565094): torna a intenção de envio visível a qualquer runner seguinte ANTES de contactar o
    Telegram — commit só de `notify_state.json` e `notify_log.jsonl`, `pull --rebase --autostash` e `push` para o ramo do
    workflow. Devolve True só quando o push foi aceite. Qualquer falha (comando, conflito, tempo esgotado) devolve False e o
    aviso NÃO é enviado. Um push aceite mas dado como falhado (resposta perdida) também devolve False: no pior caso o
    acontecimento fica `in_flight` no remoto e a execução seguinte marca-o como incerto — nunca é enviado duas vezes."""

    def __init__(self, root, branch, paths, timeout=60, attempts=3, runner=subprocess.run):
        self.root, self.branch, self.paths, self.timeout, self.attempts, self.runner = str(root), branch, list(paths), timeout, attempts, runner

    @classmethod
    def for_store(cls, store, branch, **kw):
        top = subprocess.run(['git', '-C', str(store.state_path.parent), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=30)
        if top.returncode:
            raise NotifyError('NOTIFY_DURABLE=git needs a git checkout')
        root = Path(top.stdout.strip())
        paths = [os.path.relpath(p.resolve(), root.resolve()) for p in (store.state_path, store.log_path)]
        return cls(root, branch, paths, **kw)

    def git(self, *args):
        r = self.runner(['git', '-C', self.root, '-c', 'user.name=scangoal-collector', '-c', 'user.email=actions@users.noreply.github.com',
                         '-c', 'commit.gpgsign=false', *args], capture_output=True, timeout=self.timeout)
        return r.returncode == 0

    def __call__(self, key):
        try:
            if not (self.git('add', '--', *self.paths)
                    and self.git('commit', '-q', '-m', 'notify: intent %s [skip ci]' % sha(key)[:12], '--', *self.paths)):
                return False
            for _ in range(self.attempts):
                if not self.git('pull', '-q', '--rebase', '--autostash', 'origin', self.branch):
                    self.git('rebase', '--abort')
                    return False
                if self.git('push', '-q', 'origin', 'HEAD:%s' % self.branch):
                    return True
            return False
        except (OSError, subprocess.SubprocessError):
            return False


def durability(env, store):
    """NOTIFY_DURABLE=git (variável do workflow, a autorizar à parte): barreira de publicação remota antes de cada envio.
    Sem ela, "no máximo uma vez" vale só dentro do mesmo armazenamento (o runner), não entre runners."""
    mode = (env.get('NOTIFY_DURABLE') or '').strip()
    if not mode:
        print('NOTIFY_DURABILITY local')
        return None
    branch = (env.get('GITHUB_REF_NAME') or '').strip()
    if mode != 'git' or not branch:
        raise NotifyError('NOTIFY_DURABLE must be "git" with GITHUB_REF_NAME set')    # mal configurado: não envia nada
    print('NOTIFY_DURABILITY git')
    return GitPublisher.for_store(store, branch)


def run(sender=send, now=None, env=None, checkpoint=None):
    env = os.environ if env is None else env
    token, chat = env.get('TG_BOT_TOKEN', '').strip(), env.get('TG_CHAT_ID', '').strip()
    if not token or not chat:
        print('NOTIFY_NOT_CONFIGURED')
        return None
    now = now or datetime.now(UTC)
    report = load_json(data_dir() / 'report.json', None)
    if not report:
        print('NOTIFY_NO_REPORT')
        return None
    store = FileStore(data_dir())
    publish = durability(env, store)
    try:
        state = store.load_state()
    except StateMissing:
        print('NOTIFY_STATE_MISSING')        # nada é enviado; repor o estado do histórico git ou, só numa instalação nova, --init-state
        raise
    state, done = deliver(store, report, state, now, sender, token, chat, checkpoint, publish)
    print('NOTIFY_OK sent', done)
    return state


def run_checked():
    """run() dos pontos de entrada (CLI e leituras rápidas). Igual a run(), mas, depois de tudo gravado, levanta
    DeliveryIncomplete se esta execução acrescentou entregas falhadas, incertas ou retiradas. Nunca reenvia nada."""
    saved = load_json(data_dir() / STATE_NAME, None)
    before = saved.get('delivery_counts') if isinstance(saved, dict) else None
    before = before if isinstance(before, dict) else {}
    state = run()
    after = state.get('delivery_counts') if isinstance(state, dict) else None
    after = after if isinstance(after, dict) else {}
    count = lambda counts, k: counts[k] if _int(counts.get(k)) else 0
    added = {k: count(after, k) - count(before, k) for k in PROBLEMS if count(after, k) > count(before, k)}
    if added:
        print('NOTIFY_INCOMPLETE', json.dumps(added, sort_keys=True))
        raise DeliveryIncomplete(', '.join(sorted(added)))
    return state


def failure(sender=send, now=None, env=None):
    """Chamado só quando a recolha falha. No máximo um aviso por hora (primeiros 10 minutos de cada hora), exceto quando
    falhou o lançamento da execução seguinte (NEXT_COLLECTION_FAILED=1): aí a cadeia parou e não haverá outra oportunidade."""
    env = os.environ if env is None else env
    token, chat, now = env.get('TG_BOT_TOKEN', '').strip(), env.get('TG_CHAT_ID', '').strip(), now or datetime.now(UTC)
    if token and chat and (now.minute < 10 or env.get('NEXT_COLLECTION_FAILED', '').strip() == '1'):
        sender(token, chat, 'A recolha do goal-roi falhou às %s UTC. Vê as execuções em https://github.com/nunovinhas-creator/goal-roi/actions' % now.strftime('%H:%M'))
        return True
    return False


# ---------------------------------------------------------------------------------------------------------------------
# Pré-visualização offline

@contextmanager
def no_network():
    """Qualquer tentativa de abrir uma ligação falha (pré-visualização)."""
    def refuse(*args, **kwargs):
        raise NotifyError('network disabled in preview')
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = refuse, refuse, refuse, refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved


def preview(state_path=None, report_path=None, now=None, log_path=None):
    """Os avisos que a próxima execução produziria. Nunca lê credenciais, nunca abre ligações, nunca grava estado nem registo.
    -> (textos com metadados, estado simulado depois da execução)."""
    with no_network():
        state_path = Path(state_path) if state_path else data_dir() / STATE_NAME
        log_path = Path(log_path) if log_path else state_path.parent / LOG_NAME
        report = json.loads(Path(report_path or data_dir() / 'report.json').read_text(encoding='utf-8'))
        if not state_path.exists():
            raise StateMissing('notify_state.json missing')     # a pré-visualização segue a mesma regra que o envio
        state = json.loads(state_path.read_text(encoding='utf-8'))
        store = MemoryStore(state, log_path.read_bytes() if log_path.exists() else b'')
        shown = []

        def record(token, chat, text):
            shown.append(text)
        state, _ = deliver(store, report, store.load_state(), now or datetime.now(UTC), record, 'preview', 'preview')
        return shown, state


def preview_main(argv):
    def opt(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    now = parse_time(opt('--now')) if opt('--now') else datetime.now(UTC)
    texts, _ = preview(opt('--state'), opt('--report'), now, opt('--log'))
    lines = ['# goal-roi — pré-visualização offline dos avisos (nada foi enviado, nada foi gravado)', '# agora: %s; avisos: %d' % (iso(now), len(texts))]
    for i, t in enumerate(texts, 1):
        lines += ['', '----- aviso %d (%d caracteres, sha256 %s) -----' % (i, len(t), sha(t)[:12]), t]
    body = '\n'.join(lines) + '\n'
    out = opt('--out')
    if out:
        Path(out).write_text(body, encoding='utf-8')
    else:
        sys.stdout.write(body)
    return texts


def main():
    if '--preview' in sys.argv:
        try:
            preview_main(sys.argv)
        except Exception as exc:
            print('NOTIFY_PREVIEW_FAILED', type(exc).__name__)
            sys.exit(1)
        return
    if '--init-state' in sys.argv:
        try:
            init_state_file()
        except Exception as exc:
            print('NOTIFY_INIT_REFUSED', type(exc).__name__)
            sys.exit(1)
        return
    if '--migrate-format' in sys.argv:
        try:
            migrate_format_file()
        except Exception as exc:
            print('NOTIFY_MIGRATION_FAILED', type(exc).__name__)
            sys.exit(1)
        return
    if '--migrate-statuses' in sys.argv:
        try:
            migrate_file()
        except Exception as exc:
            print('NOTIFY_MIGRATION_FAILED', type(exc).__name__)
            sys.exit(1)
        return
    try:
        failure() if '--failure' in sys.argv else run_checked()
    except Exception as exc:      # só o tipo do erro; nunca o token
        print('NOTIFY_FAILED', type(exc).__name__)
        sys.exit(FAILED_EXIT)     # o estado e o registo já estão gravados; o commit de dados continua (continue-on-error)
    if '--failure' not in sys.argv and (os.environ.get('TG_BOT_TOKEN') or '').strip() and (os.environ.get('TG_CHAT_ID') or '').strip():
        try:                      # avisos das linhas 1, 6 e 9 (Stake): independentes dos de cima; nunca mudam o código de saída
            from . import report, stake_alerts
            report.build(None, register=False)
            stake_alerts.deliver(report.LAST.get('signals') or [])
        except Exception as exc:
            print('STAKE_ALERTS_FAILED', type(exc).__name__)
    sys.exit(0)                   # avisos enviados, nada a enviar ou sem chaves


if __name__ == '__main__':
    main()

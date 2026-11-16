"""Avisos pelo Telegram, por um bot do utilizador (API de bots; não usa a conta que lê o ScanGoal).

Envia: aviso de jogo (indício, ou ALERTA só com certificado ativo e verificado da própria hipótese), um por jogo, com as
seleções novas desse jogo; mudanças de estado de hipóteses, uma mensagem por grupo de hipóteses equivalentes; mudanças de
comportamento de um tipo de sinal; e um resumo diário depois das 21:00 UTC.

PR 5 (formato `telegram_format_v2`):
- Entrega "no máximo uma vez": cada acontecimento tem uma chave determinística. A intenção é gravada (estado atómico e
  registo `notify_log.jsonl`, só a crescer) ANTES do envio; depois da resposta grava-se o resultado. Estados: pendente,
  entregue (resposta positiva do Telegram), falha confirmada (o Telegram recusou ou a ligação nunca se fez), incerta (não se
  sabe se saiu). Uma entrega incerta nunca é reenviada nem contada em `sent_total`.
- Consolidação: um aviso por jogo; reforços do mesmo (jogo, mercado, seleção) nunca geram outro aviso; hipóteses
  equivalentes (Jaccard >= 0,85 nos jogos, ligação completa e compatibilidade da PR 4) saem numa só mensagem.
- Mudanças de estado só com relatório saudável, hipótese presente, sem estado pendente e com a mudança registada pelo
  cérebro; reversões em menos de 24 h ficam para o resumo, exceto as críticas (entrada ou saída de "validada").
- Estado científico no topo de cada mensagem; afirmações positivas só com certificado verificado da hipótese citada
  (`scientific_label` + barreira final `guard_claims`).
Segredos: TG_BOT_TOKEN e TG_CHAT_ID (o token só vai no endereço do pedido e nunca é impresso nem gravado). Sem eles, não faz
nada. `--preview` nunca os lê, nunca abre ligações e nunca grava estado.
"""
import copy, hashlib, json, os, re, socket, sys, tempfile, unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import equivalence
from .eligibility import text_identity
from .results_bsd import load_json
from .store import data_dir

STATUS_MIGRATION = 'eligible_statuses_v1'
FORMAT_MIGRATION = 'telegram_format_v2'
EVIDENCE_SCOPE = 'eligible_bets_only'
SUMMARY_HOUR_UTC = 21
MAX_WATCH_PER_RUN = 5             # jogos (não sinais) por execução
MAX_GROUPS_PER_MESSAGE = 8
MAX_TEXT = 3900
VALID = ('forte', 'confirmada')   # tática validada: só com certificado do teste de confirmação (PR 3)
VALIDATION_METHOD = 'eprocess_online_alpha_v1'   # único método aceite para anunciar uma tática como validada (fail-closed)
ALERT_MAX_AGE_MIN = 30            # alerta só se o jogo ainda não começou ou se o sinal tem no máximo 30 minutos
TAU = 0.85                        # Jaccard mínimo entre os jogos de duas hipóteses para as agrupar (só apresentação)
GROUP_MIN_GAMES = 8
OSCILLATION_HOURS = 24            # reversão de uma mudança anunciada há menos de 24 h: vai para o resumo
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


class DeliveryFailed(NotifyError):
    """Falha confirmada: a mensagem não foi publicada (o Telegram recusou, ou a ligação nunca chegou a fazer-se)."""


class DeliveryUncertain(NotifyError):
    """Não se sabe se a mensagem foi publicada (tempo esgotado, ligação cortada, resposta ilegível, erro 5xx)."""


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


def pts(v):
    return '—' if v is None else ('%+.0f' % (v * 100))


def dec(v):
    return ('%+.2f' % v).replace('.', ',')


def spct(v):
    return '—' if v is None else ('%+.1f' % (v * 100)).replace('.', ',') + ' %'


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
    ko = parse_time(w.get('kickoff_utc'))
    if ko and ko > now:
        return True
    t = parse_time(w.get('date_utc'))
    return t is not None and now - t <= timedelta(minutes=ALERT_MAX_AGE_MIN)


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
            items[item_key(w, item_level(report, w))] = {'at_utc': iso(now), 'key': 'migration:' + FORMAT_MIGRATION}
    state.setdefault('migrations', {})[FORMAT_MIGRATION] = {
        'at_utc': iso(now), 'report_generated_at_utc': report.get('generated_at_utc'),
        'sent_total': state.get('sent_total', 0), 'watch_sent': len(state.get('watch_sent') or []),
        'previous_state_keys': sorted(state), 'announced_statuses': copy.deepcopy(announced), 'seeded_items': sorted(items)}
    state['announced_statuses'] = announced
    state.setdefault('notified_items', {}).update(items)
    state.setdefault('health', {})['reference'] = health_metrics(report)
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
        out.append('estado da verificação da certificação mudou')
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
    def __init__(self, key, kind, lines, effects, claims=(), hids=()):
        self.key, self.kind, self.effects, self.claims, self.hids = key, kind, effects, list(claims), list(hids)
        self.text = fit(lines)


def fit(lines):
    """<= MAX_TEXT caracteres sem cortar o topo (estado científico e título): corta linhas do fim e assinala o corte."""
    lines = [x for x in lines if x]
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


def evidence_lines(report, state, hid, reason=None, members=()):
    """Jogos independentes, ROI, probabilidade e evidência nova (nesta ordem). reason: dados do sinal em aberto (watch)."""
    row, reason = hypothesis_row(report, hid) or {}, reason or {}
    get = lambda k: row.get(k) if row.get(k) is not None else reason.get(k)
    games, n = get('games'), get('n')
    out = []
    if members:
        listed = '; '.join(members)
        out.append('e mais %d equivalente%s (partilham ≥ %d %% dos jogos): %s'
                   % (len(members), 's' if len(members) > 1 else '', round(TAU * 100), listed if len(listed) < 900 else listed[:900] + '…'))
    if games is not None:
        acc = ''
        if reason.get('hit_rate') is not None and n is not None:
            acc = ' (acerto %s em %d resultados; a odd exige %s)' % (pct(reason['hit_rate']), n, pct(reason.get('break_even')))
        out.append('Jogos independentes: %d%s' % (games, acc))
    if row.get('game_roi') is not None:
        out.append('ROI por jogo: %s à odd anunciada; %s com 2 %% de perda de odd' % (spct(row['game_roi']), spct(row.get('game_roi_cut'))))
    elif get('roi') is not None:
        out.append('ROI por aposta à odd anunciada: %s' % spct(get('roi')))
    if get('prob_above') is not None:
        out.append('Probabilidade de estar acima do necessário: %s; com 2 %% de perda de odd: %s' % (pct(get('prob_above')), pct(get('prob_above_cut'))))
    ci = get('edge_ci')
    if isinstance(ci, list) and len(ci) == 2:
        out.append('Vantagem estimada: %s pontos (entre %s e %s).' % (pts(get('edge')), pts(ci[0]), pts(ci[1])))
    pro, last = row.get('prospective_games'), (state.get('notice_games') or {}).get(hid)
    if pro is not None:
        since = ('primeiro aviso desta hipótese' if last is None else '%+d desde o último aviso' % (pro - last))
        out.append('Evidência nova: %d jogos prospetivos desde o registo; %s.' % (pro, since))
    return out


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


def item_key(w, level):
    return 'item|%s|%s|%s|%s' % (game_key(w), w.get('market') or '', w.get('selection') or '', level)


def legacy_pairs(watch_sent):
    out = {}
    for k in watch_sent:
        parts = k.split('|') if isinstance(k, str) else []
        if len(parts) >= 3:
            t = parse_time(parts[0])
            if t:
                out.setdefault(('|'.join(parts[1:-1]), parts[-1]), []).append(t)
    return out


def game_notices(report, state, now):
    """Um aviso por jogo, só com as seleções ainda não avisadas; reforços nunca geram novo aviso."""
    notified, sent = state.get('notified_items') or {}, set(state.get('watch_sent') or [])
    pairs, games = legacy_pairs(sent), {}
    for w in (report.get('brain') or {}).get('watch') or []:
        if not isinstance(w, dict) or not isinstance(w.get('reasons'), list) or not w['reasons']:
            continue
        level = item_level(report, w)
        if level == 'alerta' and not still_bettable(w, now):
            continue          # fora de tempo: não serve de alerta nem de indício, e não fica pendente
        ik, t = item_key(w, level), parse_time(w.get('date_utc'))
        if ik in notified or legacy_key(w) in sent:
            continue
        if level == 'indício' and t and any(abs(t - x) <= timedelta(days=LEGACY_PAIR_DAYS) for x in pairs.get((w.get('match'), w.get('selection')), ())):
            continue          # avisado no formato antigo (antes da PR 5) com outra hora de sinal: reforço
        g = games.setdefault(game_key(w), {'items': {}, 'alert': False, 'last': w.get('date_utc') or ''})
        it = g['items'].setdefault(ik, {'level': level, 'entries': []})
        it['entries'].append(w)
        g['alert'] |= level == 'alerta'
        g['last'] = max(g['last'], w.get('date_utc') or '')
    ordered = sorted(games.items(), key=lambda kv: (not kv[1]['alert'], -_epoch(kv[1]['last']), kv[0]))
    return [game_notice(report, state, now, gk, g) for gk, g in ordered[:MAX_WATCH_PER_RUN]]


def _epoch(v):
    t = parse_time(v)
    return t.timestamp() if t else 0.0


def game_notice(report, state, now, gk, g):
    items = sorted(g['items'].items(), key=lambda kv: (kv[1]['level'] != 'alerta', kv[0]))
    first = items[0][1]['entries'][0]
    reasons, covers, alerted = {}, {}, set()
    for ik, it in items:
        latest = max(it['entries'], key=lambda w: w.get('date_utc') or '')
        sel = '%s%s' % (latest.get('selection') or 'mais um golo', (' @ ' + odd(latest['odds'])) if latest.get('odds') else '')
        if len(it['entries']) > 1:
            sel += ' (%d sinais)' % len(it['entries'])
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
    head, claims = labels(report, [h for gr in shown for h in gr])
    match = first.get('match') or 'Jogo'
    lines = head + [('🟢 ALERTA: %s' % match) if g['alert'] else ('A seguir: %s' % match)]
    effects = []
    for gr in shown:
        rep = gr[0]
        r = reasons.get(rep) or next(reasons[h] for h in gr if h in reasons)
        if rep in alerted:
            lines.append('🟢 Tática validada (%s): %s' % (r['status'], rep))
        else:
            lines.append('Encaixa em: %s (%s)' % (rep, 'promissora' if r.get('status') in VALID else r.get('status')))
        lines += evidence_lines(report, state, rep, r, gr[1:])
        sels = []
        for h in gr:
            for s in covers.get(h, ()):
                if s not in sels:
                    sels.append(s)
        lines.append('Aposta: %s' % '; '.join(sels))
        if rep in alerted and r.get('prudent_rate'):
            lines.append('Odd mínima para valer a pena: %s.' % odd('%.2f' % (1 / r['prudent_rate'])))
        for h in gr:
            row = hypothesis_row(report, h) or {}
            if row.get('prospective_games') is not None:
                effects.append(['notice_games', h, row['prospective_games']])
    if len(grouped) > len(shown):
        lines.append('… e mais %d grupos de hipóteses.' % (len(grouped) - len(shown)))
    ko = lisbon(next((w['kickoff_utc'] for _, it in items for w in it['entries'] if w.get('kickoff_utc')), None))
    lines.append('Jogo às %s (hora de Lisboa).' % ko if ko else None)
    lines.append('Resultados passados não garantem os futuros; a decisão é tua.' if g['alert'] else 'É um indício para observar, não uma recomendação.')
    for ik, it in items:
        effects.append(['item', ik])
        for w in it['entries']:
            effects.append(['watch_sent', legacy_key(w)])
    key = 'game|%s|%s' % (gk, sha('\n'.join(k for k, _ in items))[:12])
    return Notice(key, 'alert' if g['alert'] else 'hint', lines, effects, claims, list(reasons))


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


def status_notices(report, state, now, current, healthy):
    announced = state.setdefault('announced_statuses', {})
    changes = state.setdefault('announced_changes', {})
    transitions = {}
    for hid in sorted(set(announced) | set(current)):
        old, new = announced.get(hid, 'em observação'), explicit_status(report, hid, current)
        if new is None and old in VALID and certificate(report, hid) is None:
            new = 'em observação'                         # validada que desaparece sem certificado: perda, nunca escondida
        if new is None or new == old:
            continue
        critical = old in VALID or new in VALID          # entrada ou saída de "validada": nunca adiada pela anti-oscilação
        if old in VALID and new not in VALID:
            pass                                          # perda de certificação: anunciada mesmo com relatório degradado
        elif not healthy or not change_verified(report, hid, new):
            continue
        last = changes.get(hid)
        at = parse_time((last or {}).get('at_utc'))
        if (not critical and last and last.get('from') == new and last.get('to') == old and at
                and now - at < timedelta(hours=OSCILLATION_HOURS)):
            osc = state.setdefault('oscillations', [])
            entry = {'id': hid, 'path': [new, old, new], 'announced_utc': last['at_utc']}
            if entry not in osc:
                osc.append(entry)
            continue
        transitions.setdefault((old, new), []).append(hid)
    out = []
    for (old, new), hids in sorted(transitions.items()):
        for gr in groups_of(report, hids):
            out.append(status_notice(report, state, now, old, new, gr))
    return out


def status_notice(report, state, now, old, new, gr):
    rep = gr[0]
    head, claims = labels(report, gr)
    if new in VALID:
        title = '✅ Tática validada, passou a %s: %s' % (new, rep)
        tail = 'A partir de agora, cada jogo que encaixe nela gera um alerta.'
    elif old in VALID:
        title = ('⛔ Tática deixou de estar validada, %s: %s'
                 % ('voltou a "em observação"' if new == 'em observação' else 'passou a %s' % new, rep))
        tail = 'Deixa de gerar alertas.'
    else:
        title = ('Hipótese %s (antes: %s): %s' % ('voltou a "em observação"' if new == 'em observação' else 'passou a %s' % new, old, rep))
        tail = None
    lines = head + [title] + evidence_lines(report, state, rep, None, gr[1:])
    last = (hypothesis_row(report, rep) or {}).get('last_change')
    ref = None
    if isinstance(last, dict) and last.get('to') in (new, 'forte', 'confirmada') and last.get('games') is not None:
        known = lisbon(last.get('known_utc'))
        lines.append('Mudança fixada com %d jogos independentes%s.' % (last['games'], (' (resultado conhecido às %s)' % known) if known else ''))
        ref = '%s@%s' % (last.get('games'), last.get('known_utc'))
    lines.append(tail)
    effects = []
    for h in gr:
        effects.append(['announce', h, old, new])
        row = hypothesis_row(report, h) or {}
        if row.get('prospective_games') is not None:
            effects.append(['notice_games', h, row['prospective_games']])
    # A mesma transição pode repetir-se (p. ex. certificação perdida e recuperada): o instante do anúncio anterior distingue
    # cada ocorrência e não muda enquanto o aviso estiver pendente (só muda quando a intenção é gravada).
    prev = '+'.join(((state.get('announced_changes') or {}).get(h) or {}).get('at_utc') or '-' for h in sorted(gr))
    key = 'status|%s>%s|%s|%s|%s' % (old, new, sha('\n'.join(sorted(gr)))[:12], ref or 'sem-referência', sha(prev)[:12])
    return Notice(key, 'status', lines, effects, claims, gr)


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


def summary_notice(report, state, now):
    day = now.date().isoformat()
    brain = report.get('brain') or {}
    today = next((d for d in report.get('daily') or [] if d.get('day') == day), {'bets': 0, 'settled': 0, 'wins': 0, 'units': 0.0})
    tl, c, t = report.get('timeline') or [], brain.get('counts') or {}, brain.get('llm_today') or {}
    current = eligible_statuses(report) or {}
    promising = [h for h, s in current.items() if s == 'promissora']
    certified = sorted(certified_ids(report))
    head, claims = labels(report, certified)
    lines = head + [
        'Resumo de %s' % now.strftime('%d/%m'),
        'Apostas de hoje: %d, com resultado %d, ganhas %d, saldo do dia %s un.' % (today['bets'], today['settled'], today['wins'], dec(today['units'])),
        'Saldo total: %s un. em %d apostas com resultado.' % (dec(tl[-1]['u'] if tl else 0.0), report.get('settled_bets') or 0),
        'Hipóteses: %d em teste; %d promissoras (%d grupos de equivalentes), %d abandonadas.'
        % (brain.get('hypotheses') or 0, c.get('promissora', 0), len(groups_of(report, promising)) if promising else 0, c.get('abandonada', 0))]
    counts, base = state.get('delivery_counts') or {}, state.get('summary_counts') or {}
    d = {k: counts.get(k, 0) - base.get(k, 0) for k in COUNTERS}
    if any(d.values()):
        lines.append('Avisos desde o último resumo: %d entregues, %d com falha, %d incertos (não reenviados), %d bloqueados, %d caducados.'
                     % (d['delivered'], d['failed'], d['uncertain'], d['blocked'], d['expired']))
    osc = state.get('oscillations') or []
    if osc:
        lines.append('Oscilações em menos de %d h (não anunciadas): %s.' % (OSCILLATION_HOURS, '; '.join(
            '%s: %s' % (o['id'], ' → '.join(o['path'])) for o in osc[:10])))
    health = state.get('health') or {}
    if health.get('degraded_since'):
        lines.append('Relatório degradado desde %s (%s): mudanças de estado retidas.' % (lisbon(health['degraded_since']), ', '.join(health.get('problems') or [])))
    lines += ['Modelo de linguagem hoje: %s chamadas, cerca de %s neurónios.' % (t.get('calls_today') or 0, int(t.get('neurons_today') or 0)), PAGE]
    return Notice('summary|%s' % day, 'summary', lines, [['summary', day]], claims, certified)


def hello_notice(report):
    head, _ = labels(report, [])
    return Notice('hello', 'hello', head + ['Avisos do goal-roi ligados. Vais receber: indícios de jogos que encaixam em hipóteses promissoras, '
                                            'alertas de apostas, mudanças de estado e um resumo diário.', PAGE], [['hello']])


def plan(report, state, now):
    """Relatório + estado -> avisos por esta ordem. Aplica ao estado só o que não depende de uma entrega (migrações
    silenciosas, referência de saúde, `statuses` a seguir o relatório, regimes sem aviso, oscilações). Os efeitos de cada
    aviso (o que fica marcado como avisado) só são aplicados quando a intenção de envio é gravada."""
    migrate_status_reference(report, state, now)
    migrate_format(report, state, now)
    out = [] if state.get('hello') else [hello_notice(report)]
    out += game_notices(report, state, now)
    new = eligible_statuses(report)
    if new is not None:
        healthy, _ = check_health(report, state, now)
        out += status_notices(report, state, now, new, healthy)
        if state.get('statuses') != new:
            state['statuses'] = copy.deepcopy(new)
    out += regime_notices(report, state, now)
    if now.hour >= SUMMARY_HOUR_UTC and state.get('summary_day') != now.date().isoformat():
        out.append(summary_notice(report, state, now))
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
        # Fail closed: a corrupt state raises instead of being replaced by an empty history.
        return json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else {}

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


def reconcile(store, state, now):
    """Recupera de uma queda em qualquer ponto, sem reenviar e sem perder contadores:
    1) uma intenção sem resultado (no estado ou no registo) passa a INCERTA (nunca é reenviada);
    2) um resultado que só o registo conhece (estado anterior reposto) é reposto no estado, com os seus efeitos e contadores;
    3) o que o estado conhece e o registo não (queda entre as duas gravações) é acrescentado ao registo, marcado reconstruído.
    Ordem das gravações: estado primeiro, depois o registo; repetir a reconciliação é inofensivo."""
    entries, bad = store.read_log()
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
        outcome = terminal[-1] if terminal else ('uncertain' if 'intent' in names else None)
        if outcome is None:
            continue
        intent = next((e for e in evs if e['event'] == 'intent'), {})
        r = recs.setdefault(key, {'kind': evs[0].get('kind'), 'created_utc': evs[0].get('at_utc')})
        r.update(state=outcome, reconstructed=True, events=[x for x in names if x != outcome] + [outcome], sha256=intent.get('sha256'))
        if isinstance(intent.get('effects'), list):
            apply_effects(state, intent['effects'], now)
        _bump(state, outcome)
        if outcome == 'delivered':
            state['sent_total'] = state.get('sent_total', 0) + 1
        changed = True
    missing = []
    for key, r in recs.items():
        for ev in r.get('events') or []:
            if (key, ev) not in seen:
                seen.add((key, ev))
                missing.append(log_entry(now, key, ev, r, reconstructed=True, sha256=r.get('sha256') if ev in ('intent', 'delivered') else None))
    if changed:
        store.save_state(state)
    store.append_log(missing)
    return {'malformed_log_lines': bad, 'reconstructed_log_entries': len(missing), 'changed_state': changed}


def deliver(store, report, state, now, sender, token, chat, checkpoint=None):
    """Planeia e envia. Para cada aviso: intenção (efeitos + `in_flight`) gravada no estado e no registo ANTES do envio;
    resultado gravado depois. Uma falha ou incerteza para os envios desta execução (os seguintes ficam pendentes e só saem
    se ainda forem válidos na execução seguinte). -> (estado, entregues)."""
    cp = checkpoint or (lambda name: None)
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
        if not guard_claims(n.text, report, n.claims):
            apply_effects(state, n.effects, now)
            rec.update(state='blocked', done_utc=iso(now), sha256=sha(n.text))
            rec['events'].append('blocked')
            _bump(state, 'blocked')
            store.save_state(state)
            store.append_log([log_entry(now, n.key, 'blocked', rec, sha256=rec['sha256'], reason='guard_claims')])
            continue
        apply_effects(state, n.effects, now)
        rec.update(state='in_flight', attempt_utc=iso(now), sha256=sha(n.text), chars=len(n.text))
        rec['events'].append('intent')
        store.save_state(state)
        cp('after_intent_state')
        store.append_log([log_entry(now, n.key, 'intent', rec, sha256=rec['sha256'], effects=n.effects)])
        cp('after_intent')
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
    state['last_run_utc'] = iso(now)
    store.save_state(state)
    return state, delivered


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
    state = store.load_state()
    state, done = deliver(store, report, state, now, sender, token, chat, checkpoint)
    print('NOTIFY_OK sent', done)
    return state


def failure(sender=send, now=None, env=None):
    """Chamado só quando a recolha falha. No máximo um aviso por hora (primeiros 10 minutos de cada hora)."""
    env = os.environ if env is None else env
    token, chat, now = env.get('TG_BOT_TOKEN', '').strip(), env.get('TG_CHAT_ID', '').strip(), now or datetime.now(UTC)
    if token and chat and now.minute < 10:
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
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
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
        failure() if '--failure' in sys.argv else run()
    except Exception as exc:      # só o tipo do erro; nunca o token
        print('NOTIFY_FAILED', type(exc).__name__)
    sys.exit(0)


if __name__ == '__main__':
    main()

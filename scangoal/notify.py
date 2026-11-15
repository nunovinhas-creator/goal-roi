"""Avisos pelo Telegram, por um bot do utilizador (API de bots; não usa a conta que lê o ScanGoal).

Envia: ALERTA para cada jogo que encaixa numa tática validada (forte ou confirmada), só enquanto ainda dá para apostar;
aviso quando uma tática passa a validada ou deixa de o ser (e nesse caso deixa de gerar alertas);
sinais em aberto que encaixam numa hipótese promissora (indício); hipóteses que mudam de estado;
mudanças de comportamento de um tipo de sinal; e um resumo diário depois das 21:00 UTC.
Segredos: TG_BOT_TOKEN e TG_CHAT_ID (o token só vai no endereço do pedido e nunca é impresso). Sem eles, não faz nada.
"""
import copy, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .results_bsd import load_json
from .store import data_dir

STATUS_MIGRATION = 'eligible_statuses_v1'
EVIDENCE_SCOPE = 'eligible_bets_only'
SUMMARY_HOUR_UTC = 21
MAX_WATCH_PER_RUN = 5
VALID = ('forte', 'confirmada')   # tática validada: 100 ou mais resultados e certificada pelo teste de confirmação (PR 3)
VALIDATION_METHOD = 'eprocess_online_alpha_v1'   # único método aceite para anunciar uma tática como validada (fail-closed)
ALERT_MAX_AGE_MIN = 30            # alerta só se o jogo ainda não começou ou se o sinal tem no máximo 30 minutos
LISBON = ZoneInfo('Europe/Lisbon')
PAGE = 'https://nunovinhas-creator.github.io/goal-roi/'
KIND = {'BETFAIR/pre': 'Betfair antes do jogo', 'BETFAIR/live': 'Betfair durante o jogo', 'STAKE_LOAD': 'Stake', 'POLYMARKET': 'Polymarket', 'OPEN_GAME': 'Jogo aberto',
        'TOTALS_FORM': 'Forma de golos', 'WEAKENED_LINEUP': 'Alinhamento enfraquecido', 'GOAL_BREWING': 'Golo a caminho',
        'OPEN_GAME_NO_ODDS': 'Jogo aberto sem odd', 'TOTALS_FORM_NO_ODDS': 'Forma de golos sem odd'}
UTC = timezone.utc


class NotifyError(RuntimeError):
    pass


def send(token, chat, text):
    req = Request('https://api.telegram.org/bot%s/sendMessage' % token,
                  data=urlencode({'chat_id': chat, 'text': text[:3900], 'disable_web_page_preview': 'true'}).encode('utf-8'))
    try:
        with urlopen(req, timeout=20) as r:
            if not json.loads(r.read(200_000)).get('ok'):
                raise NotifyError('not ok')
    except HTTPError as exc:
        raise NotifyError('HTTP %d' % exc.code) from None      # nunca imprimir o endereço: contém o token
    except (URLError, TimeoutError, OSError, ValueError):
        raise NotifyError('unavailable') from None


def pct(v):
    return '—' if v is None else ('%.0f %%' % (v * 100))


def pts(v):
    return '—' if v is None else ('%+.0f' % (v * 100))


def dec(v):
    return ('%+.2f' % v).replace('.', ',')


def still_bettable(w, now):
    ko = w.get('kickoff_utc')
    if ko:
        ko = datetime.fromisoformat(ko)
        if (ko if ko.tzinfo else ko.replace(tzinfo=UTC)) > now:
            return True
    return now - datetime.fromisoformat(w['date_utc']) <= timedelta(minutes=ALERT_MAX_AGE_MIN)


def alert(w, r):
    """Texto do alerta de um jogo que encaixa numa tática validada."""
    ko = w.get('kickoff_utc') and datetime.fromisoformat(w['kickoff_utc'])
    ko = ko and (ko if ko.tzinfo else ko.replace(tzinfo=UTC)).astimezone(LISBON)
    ci = r.get('edge_ci')
    lines = ['🟢 ALERTA: tática validada (%s)' % r['status'], w.get('match') or 'Jogo',
             '%s%s' % (w.get('selection') or 'mais um golo', (' @ ' + str(w['odds']).replace('.', ',')) if w.get('odds') else ''),
             'Jogo às %s (hora de Lisboa).' % ko.strftime('%H:%M de %d/%m') if ko else None,
             'Tática: %s' % r['id'],
             'Acertou %s em %d resultados; a odd exige %s.' % (pct(r['hit_rate']), r['n'], pct(r.get('break_even'))),
             'Vantagem estimada: %s pontos (entre %s e %s).' % (pts(r.get('edge')), pts(ci[0]), pts(ci[1])) if ci else None,
             'Odd mínima para valer a pena: %s.' % ('%.2f' % (1 / r['prudent_rate'])).replace('.', ',') if r.get('prudent_rate') else None,
             'Resultados passados não garantem os futuros; a decisão é tua.']
    return '\n'.join(x for x in lines if x)


def certified_ids(report):
    """Táticas certificadas pelo método aceite; qualquer outro relatório não tem nenhuma (fail-closed)."""
    v = (report.get('brain') or {}).get('validation')
    if not isinstance(v, dict) or v.get('method') != VALIDATION_METHOD or not isinstance(v.get('certified'), list):
        return set()
    return {i for i in v['certified'] if isinstance(i, str)}


def eligible_statuses(report):
    """Estados elegíveis. Uma "forte"/"confirmada" sem certificado do método aceite é tratada como promissora: nunca é
    anunciada como validada (PR 3, revisão #5911487915)."""
    brain = report.get('brain') or {}
    statuses = brain.get('status_list')
    if brain.get('evidence_scope') != EVIDENCE_SCOPE or not isinstance(statuses, dict):
        return None
    allowed = {'em observação', 'promissora', 'forte', 'confirmada', 'abandonada'}
    if any(not isinstance(k, str) or v not in allowed for k, v in statuses.items()):
        return None
    certified = certified_ids(report)
    return {k: ('promissora' if v in VALID and k not in certified else v) for k, v in statuses.items()}


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
    record = {'at_utc': now.isoformat(timespec='seconds'),
              'report_generated_at_utc': report.get('generated_at_utc'),
              'previous_statuses': copy.deepcopy(state.get('statuses')),
              'baseline_statuses': copy.deepcopy(new)}
    state.setdefault('migrations', {})[key] = record
    state['statuses'] = copy.deepcopy(new)
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
    """Offline CLI: migrate only; no credentials, sender, report generation or counters."""
    report = load_json(data_dir() / 'report.json', None)
    if not report or eligible_statuses(report) is None:
        raise NotifyError('eligible report required')
    path = data_dir() / 'notify_state.json'
    # Fail closed: a missing/corrupt existing state must not erase delivery history.
    state = json.loads(path.read_text(encoding='utf-8'))
    changed = migrate_status_reference(report, state, now or datetime.now(UTC))
    if changed:
        save_state(path, state)
    print('NOTIFY_STATUS_MIGRATED' if changed else 'NOTIFY_STATUS_UNCHANGED')
    return changed


def messages(report, state, now):
    """Relatório + estado anterior -> lista de textos a enviar. Atualiza o estado."""
    out, brain = [], report.get('brain') or {}
    operational = report.get('operational') or {}
    if not state.get('hello'):
        state['hello'] = True
        out.append('Avisos do goal-roi ligados. Vais receber: alertas de jogos que encaixam em táticas validadas, sinais que encaixam em hipóteses promissoras, mudanças de estado e um resumo diário.\n' + PAGE)
    sent = set(state.get('watch_sent') or [])
    fresh = []
    for w in sorted(brain.get('watch') or [], key=lambda w: not any(x['status'] in VALID for x in w['reasons'])):   # alertas validados primeiro
        key = '%s|%s|%s' % (w.get('date_utc'), w.get('match'), w.get('selection'))
        if key in sent or len(fresh) >= MAX_WATCH_PER_RUN:
            continue
        certified = certified_ids(report)
        valid = next((x for x in w['reasons'] if x['status'] in VALID and x.get('certified') is True and x.get('id') in certified), None)
        if valid:
            if still_bettable(w, now):   # fora de tempo: não serve de alerta nem de indício
                sent.add(key)
                fresh.append(alert(w, valid))
            continue
        sent.add(key)
        r = w['reasons'][0]
        ci = r.get('edge_ci')
        edge = ('\nVantagem estimada: %s pontos (entre %s e %s). Com 2 %% de perda de odd, probabilidade de continuar acima do necessário: %s.'
                % (pts(r.get('edge')), pts(ci[0]), pts(ci[1]), pct(r.get('prob_above_cut')))) if ci else ''
        fresh.append('A seguir: %s\n%s%s\nEncaixa em: %s (%s em %d resultados, %s)%s\nÉ um indício para observar, não uma recomendação.'
                     % (w.get('match') or 'Jogo', w.get('selection') or 'mais um golo', (' @ ' + str(w['odds']).replace('.', ',')) if w.get('odds') else '',
                        r['id'], pct(r['hit_rate']), r['n'], 'promissora' if r['status'] in VALID else r['status'], edge))   # sem certificado: nunca "validada"
    out += fresh
    state['watch_sent'] = sorted(sent)[-400:]
    migrate_status_reference(report, state, now)
    old, new = state.get('statuses'), eligible_statuses(report)
    if old is not None and new is not None:
        for hid, st in sorted(new.items()):
            if old.get(hid) == st:
                continue
            if st in VALID and old.get(hid) not in VALID:
                out.append('✅ Tática validada, passou a %s:\n%s\nA partir de agora, cada jogo que encaixe nela gera um alerta.' % (st, hid))
            elif old.get(hid) in VALID and st not in VALID:
                out.append('⛔ Tática deixou de estar validada, passou a %s:\n%s\nDeixa de gerar alertas.' % (st, hid))
            else:
                out.append('Hipótese passou a %s:\n%s' % (st, hid))
        for hid in sorted(set(old) - set(new)):
            if old[hid] in VALID:
                out.append('⛔ Tática deixou de estar validada, voltou a "em observação":\n%s\nDeixa de gerar alertas.' % hid)
            else:
                out.append('Hipótese voltou a "em observação":\n%s' % hid)
    if new is not None:
        state['statuses'] = copy.deepcopy(new)
    # Never compare the eligible population to the legacy exploratory state.
    # Seed silently on migration; leave historical `regime` intact.
    if operational.get('scope') == 'eligible_bets_only':
        old_reg = state.get('operational_regime')
        for r in operational.get('regime') or []:
            if old_reg is not None and r['state'] in ('piorou', 'melhorou') and old_reg.get(r['type']) != r['state']:
                out.append('Mudança de comportamento (evidência elegível): %s %s. Acerto recente %s, antes %s.'
                           % (KIND.get(r['type'], r['type']), r['state'], pct(r['recent']['hit_rate']), pct(r['before']['hit_rate'])))
        state['operational_regime'] = {r['type']: r['state'] for r in operational.get('regime') or []}
    day = now.date().isoformat()
    if now.hour >= SUMMARY_HOUR_UTC and state.get('summary_day') != day:
        state['summary_day'] = day
        today = next((d for d in report.get('daily') or [] if d['day'] == day), {'bets': 0, 'settled': 0, 'wins': 0, 'units': 0.0})
        tl = report.get('timeline') or []
        c = brain.get('counts') or {}
        t = brain.get('llm_today') or {}
        out.append('\n'.join([
            'Resumo de %s' % now.strftime('%d/%m'),
            'Apostas de hoje: %d, com resultado %d, ganhas %d, saldo do dia %s un.' % (today['bets'], today['settled'], today['wins'], dec(today['units'])),
            'Saldo total: %s un. em %d apostas com resultado.' % (dec(tl[-1]['u'] if tl else 0.0), report.get('settled_bets') or 0),
            'Hipóteses: %d em teste; %d promissoras, %d fortes, %d confirmadas, %d abandonadas.'
            % (brain.get('hypotheses') or 0, c.get('promissora', 0), c.get('forte', 0), c.get('confirmada', 0), c.get('abandonada', 0)),
            'Modelo de linguagem hoje: %s chamadas, cerca de %s neurónios.' % (t.get('calls_today') or 0, int(t.get('neurons_today') or 0)),
            PAGE]))
    return out


def run(sender=send, now=None, env=None):
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
    path = data_dir() / 'notify_state.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if migrate_status_reference(report, state, now):
        save_state(path, state)  # Persist the boundary before any fallible send.
    todo, done = messages(report, state, now), 0
    for text in todo:
        sender(token, chat, text)
        done += 1
    state['last_run_utc'], state['sent_total'] = now.isoformat(timespec='seconds'), state.get('sent_total', 0) + done
    save_state(path, state)
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


def main():
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

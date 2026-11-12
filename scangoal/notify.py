"""Avisos pelo Telegram, por um bot do utilizador (API de bots; não usa a conta que lê o ScanGoal).

Envia: sinais em aberto que encaixam numa hipótese promissora ou melhor; hipóteses que mudam de estado;
mudanças de comportamento de um tipo de sinal; e um resumo diário depois das 21:00 UTC.
Segredos: TG_BOT_TOKEN e TG_CHAT_ID (o token só vai no endereço do pedido e nunca é impresso). Sem eles, não faz nada.
"""
import json, os, sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .results_bsd import load_json
from .store import data_dir

SUMMARY_HOUR_UTC = 21
MAX_WATCH_PER_RUN = 5
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


def messages(report, state, now):
    """Relatório + estado anterior -> lista de textos a enviar. Atualiza o estado."""
    out, brain, ins = [], report.get('brain') or {}, report.get('insights') or {}
    if not state.get('hello'):
        state['hello'] = True
        out.append('Avisos do goal-roi ligados. Vais receber: sinais que encaixam em hipóteses promissoras, mudanças de estado e um resumo diário.\n' + PAGE)
    sent = set(state.get('watch_sent') or [])
    fresh = []
    for w in brain.get('watch') or []:
        key = '%s|%s|%s' % (w.get('date_utc'), w.get('match'), w.get('selection'))
        if key in sent or len(fresh) >= MAX_WATCH_PER_RUN:
            continue
        sent.add(key)
        r = w['reasons'][0]
        ci = r.get('edge_ci')
        edge = ('\nVantagem estimada: %s pontos (entre %s e %s). Com 2 %% de perda de odd, probabilidade de continuar acima do necessário: %s.'
                % (pts(r.get('edge')), pts(ci[0]), pts(ci[1]), pct(r.get('prob_above_cut')))) if ci else ''
        fresh.append('A seguir: %s\n%s%s\nEncaixa em: %s (%s em %d resultados, %s)%s\nÉ um indício para observar, não uma recomendação.'
                     % (w.get('match') or 'Jogo', w.get('selection') or 'mais um golo', (' @ ' + str(w['odds']).replace('.', ',')) if w.get('odds') else '',
                        r['id'], pct(r['hit_rate']), r['n'], r['status'], edge))
    out += fresh
    state['watch_sent'] = sorted(sent)[-400:]
    old, new = state.get('statuses'), brain.get('status_list') or {}
    if old is not None:
        for hid, st in sorted(new.items()):
            if old.get(hid) != st:
                out.append('Hipótese passou a %s:\n%s' % (st, hid))
        for hid in sorted(set(old) - set(new)):
            out.append('Hipótese voltou a "em observação":\n%s' % hid)
    state['statuses'] = new
    old_reg = state.get('regime') or {}
    for r in ins.get('regime') or []:
        if r['state'] in ('piorou', 'melhorou') and old_reg.get(r['type']) != r['state']:
            out.append('Mudança de comportamento: %s %s. Acerto recente %s, antes %s.'
                       % (KIND.get(r['type'], r['type']), r['state'], pct(r['recent']['hit_rate']), pct(r['before']['hit_rate'])))
    state['regime'] = {r['type']: r['state'] for r in ins.get('regime') or []}
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
    state = load_json(path, {})
    todo, done = messages(report, state, now), 0
    for text in todo:
        sender(token, chat, text)
        done += 1
    state['last_run_utc'], state['sent_total'] = now.isoformat(timespec='seconds'), state.get('sent_total', 0) + done
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
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
    try:
        failure() if '--failure' in sys.argv else run()
    except Exception as exc:      # só o tipo do erro; nunca o token
        print('NOTIFY_FAILED', type(exc).__name__)
    sys.exit(0)


if __name__ == '__main__':
    main()

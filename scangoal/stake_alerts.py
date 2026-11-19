"""Aviso pelo Telegram dos sinais Stake que cumprem as linhas 1, 6 e 9 escolhidas pelo utilizador (19/11/2026).

Independente do resto dos avisos: não depende do estado das hipóteses nem de certificados. Só lê os sinais e envia.
- Linha 1: Stake | odd 1,50 a 2,00 & jogo com um só tipo de sinal & valor da aposta menos de 10 mil $
- Linha 6: Stake | competição = liga & aviso até 45 min depois do início & sem sinal anterior de outro tipo no jogo
- Linha 9: Stake | odd 1,50 a 2,00 & aviso até 45 min depois do início & jogo com um só tipo de sinal
Um aviso por jogo e aposta (jogo, mercado, seleção), com as linhas que cumpre. Só sinais atuais (freshness: ao vivo até
30 min). Estado em data/stake_alerts_state.json; a intenção grava-se antes do envio, por isso nunca reenvia.
Segredos: TG_BOT_TOKEN e TG_CHAT_ID. Sem eles, não faz nada. `--preview` lista sem enviar nem gravar.
"""
import json, os, sys
from datetime import datetime, timezone

from . import brain, freshness, insights, notify
from .store import data_dir

STATE_NAME = 'stake_alerts_state.json'
KEEP_DAYS_KEYS = 500
LINES = {
    1: ('odd 1,50 a 2,00 · um só tipo de sinal · valor < 10 mil $',
        (('Faixa de odd', 'odd 1,50 a 2,00'), ('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'), ('Valor da aposta', 'menos de 10 mil $'))),
    6: ('liga · aviso até 45 min depois do início · sem sinal anterior de outro tipo',
        (('Competição', 'liga'), ('Momento do aviso', 'até 45 min depois do início'), ('Sinal anterior de outro tipo no mesmo jogo', 'não'))),
    9: ('odd 1,50 a 2,00 · aviso até 45 min depois do início · um só tipo de sinal',
        (('Faixa de odd', 'odd 1,50 a 2,00'), ('Momento do aviso', 'até 45 min depois do início'), ('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'))),
}


def key_of(b):
    return '|'.join(str(x) for x in (b.get('match_id'), b.get('market'), b.get('selection')))


def matches(signals, now):
    """Sinais Stake atuais que cumprem pelo menos uma das linhas -> [{...}], por ordem de hora, uma entrada por aposta."""
    multi, earlier = brain.multi_matches(signals), brain.earlier_index(signals)
    out, seen = [], set()
    for b in sorted(signals, key=lambda s: s['date_utc']):
        if insights.family(b) != 'Stake' or not freshness.current(b, now):
            continue
        k = key_of(b)
        if k in seen:
            continue
        at = set(brain.atoms(b, multi, earlier))
        hit = [n for n, (_, conds) in LINES.items() if all(c in at for c in conds)]
        if hit:
            seen.add(k)
            out.append({'key': k, 'lines': hit, 'b': b})
    return out


def text(m, now):
    b = m['b']
    t = notify.parse_time(b['date_utc'])
    age = max(0, int((now - t).total_seconds() // 60))
    teams = ' — '.join(b.get('teams') or []) or b['match_id']
    head = 'Stake · linhas ' + ', '.join(str(n) for n in m['lines'])
    sel = '%s%s @%s' % (b.get('market') + ' · ' if b.get('market') else '', b.get('selection') or '', notify.odd(b.get('odds')))
    return '\n'.join([head, teams + ((' · ' + b['league']) if b.get('league') else ''), sel,
                      'Sinal das %s (Lisboa), há %d min' % (t.astimezone(notify.LISBON).strftime('%H:%M'), age),
                      'https://scangoal.com/match/%s?lang=en' % b['match_id']])


def load_state(path):
    try:
        s = json.loads(path.read_text(encoding='utf-8'))
        return s if isinstance(s, dict) and isinstance(s.get('sent'), dict) else {'sent': {}}
    except (OSError, ValueError):
        return {'sent': {}}


def save_state(path, state):
    state['sent'] = dict(sorted(state['sent'].items(), key=lambda kv: kv[1])[-KEEP_DAYS_KEYS:])
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def deliver(signals, now=None, env=None, sender=None, path=None):
    """Envia os avisos novos. -> lista de chaves enviadas. Um erro de envio nunca é repetido (intenção já gravada)."""
    env, now = os.environ if env is None else env, now or datetime.now(timezone.utc)
    token, chat = (env.get('TG_BOT_TOKEN') or '').strip(), (env.get('TG_CHAT_ID') or '').strip()
    if not token or not chat:
        print('STAKE_ALERTS skipped (no keys)')
        return []
    path, sender = path or data_dir() / STATE_NAME, sender or notify.send
    state, sent = load_state(path), []
    for m in matches(signals, now):
        if m['key'] in state['sent']:
            continue
        state['sent'][m['key']] = now.isoformat(timespec='seconds')   # intenção antes do envio: no máximo uma vez
        save_state(path, state)
        try:
            sender(token, chat, text(m, now))
            sent.append(m['key'])
        except Exception as exc:                       # só o tipo; o token nunca sai daqui
            print('STAKE_ALERTS_SEND_FAILED', type(exc).__name__)
    print('STAKE_ALERTS sent', len(sent))
    return sent


def main():
    from . import report
    report.build(None, register=False)
    signals = report.LAST.get('signals') or []
    now = datetime.now(timezone.utc)
    if '--preview' in sys.argv:
        for m in matches(signals, now):
            print(text(m, now), '\n')
        return
    try:
        deliver(signals, now)
    except Exception as exc:
        print('STAKE_ALERTS_FAILED', type(exc).__name__)
        sys.exit(notify.FAILED_EXIT)


if __name__ == '__main__':
    main()

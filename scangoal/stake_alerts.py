"""Avisos Stake só quando a MESMA aposta cumprir simultaneamente as linhas 1, 6 e 9.

Apenas LIVE e no máximo 30 minutos após o sinal.

Um aviso por aposta (jogo, mercado, seleção). A intenção é registada em dois ficheiros
auditáveis e PUBLICADA em main antes de contactar o Telegram. Falta, corrupção,
truncagem ou erro de publicação bloqueiam o envio (fail closed). Uma resposta Telegram
ambígua nunca é repetida, mesmo que isso implique perder o aviso.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import brain, freshness, insights, notify
from .store import data_dir

STATE_NAME = 'stake_alerts_state.json'
LOG_NAME = 'stake_alerts_log.jsonl'
STATE_FORMAT = 'stake_alerts_v1'
LOG_FORMAT = 'stake_alerts_intents_v1'
LINES = {
    1: ('odd 1,50 a 2,00 · um só tipo de sinal · valor < 10 mil $',
        (('Faixa de odd', 'odd 1,50 a 2,00'), ('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'), ('Valor da aposta', 'menos de 10 mil $'))),
    6: ('liga · aviso até 45 min depois do início · sem sinal anterior de outro tipo',
        (('Competição', 'liga'), ('Momento do aviso', 'até 45 min depois do início'), ('Sinal anterior de outro tipo no mesmo jogo', 'não'))),
    9: ('odd 1,50 a 2,00 · aviso até 45 min depois do início · um só tipo de sinal',
        (('Faixa de odd', 'odd 1,50 a 2,00'), ('Momento do aviso', 'até 45 min depois do início'), ('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'))),
}


class StakeStateError(RuntimeError):
    """Sem prova completa de deduplicação ou de publicação: não enviar."""


def key_of(b):
    """Identidade estável da aposta, não da mensagem nem da odd."""
    fixture = b.get('canonical_match_id') or b['match_id']
    market = b['market']
    if b.get('period') and b['period'] != 'regulation':
        market += ':' + b['period']
    return '|'.join((fixture, market, b['selection']))


def matches(signals, now):
    """Apostas Stake LIVE atuais que satisfazem TODAS as linhas 1, 6 e 9 no mesmo sinal."""
    multi, earlier = brain.multi_matches(signals), brain.earlier_index(signals)
    out, seen = [], set()
    for b in sorted(signals, key=lambda s: s['date_utc']):
        if (insights.family(b) != 'Stake' or b.get('phase') != 'live' or
                not all(b.get(k) for k in ('match_id', 'market', 'selection')) or
                not freshness.current(b, now)):
            continue
        k = key_of(b)
        if k in seen:
            continue
        at = set(brain.atoms(b, multi, earlier))
        hit = [n for n, (_, conds) in LINES.items() if all(c in at for c in conds)]
        # AND (não OR): sinais que apenas cumprem 1, 6 ou 9 isoladamente não geram Telegram.
        if len(hit) == len(LINES):
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


def _atomic_json(path, obj):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def load_state(path):
    """Não reconstruir silenciosamente o estado: isso poderia repetir avisos antigos."""
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise StakeStateError('stake state missing or unreadable') from exc
    if (not isinstance(state, dict) or state.get('format') != STATE_FORMAT or
            not isinstance(state.get('sent'), dict) or
            type(state.get('sent_total')) is not int or
            state['sent_total'] != len(state['sent']) or
            any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                for k, v in state['sent'].items())):
        raise StakeStateError('stake state invalid or truncated')
    return state


def load_log(path):
    """Registo independente e append-only das intenções; não aceitar truncagens."""
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        header = json.loads(lines[0])
        if header != {'format': LOG_FORMAT}:
            raise ValueError('invalid header')
        seen = {}
        for line in lines[1:]:
            event = json.loads(line)
            if (not isinstance(event, dict) or set(event) != {'event', 'key', 'at'} or
                    event['event'] not in ('intent', 'rejected', 'expired') or not isinstance(event['key'], str) or
                    not event['key'] or not isinstance(event['at'], str) or not event['at']):
                raise ValueError('invalid or repeated intent')
            if event['event'] == 'intent' and event['key'] not in seen:
                seen[event['key']] = event['at']
            elif event['event'] != 'intent' and event['key'] in seen:
                del seen[event['key']]
            else:
                raise ValueError('invalid transition')
        return seen, lines
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise StakeStateError('stake intent log missing, corrupt or truncated') from exc


def verify_state(state, intents):
    if state['sent'] != intents or state['sent_total'] != len(intents):
        raise StakeStateError('stake state and intent log disagree')


def claimed_items(directory):
    path, log = directory / STATE_NAME, directory / LOG_NAME
    if not path.exists() and not log.exists():
        return set()
    intents, _ = load_log(log)
    verify_state(load_state(path), intents)
    return {'item|id:' + key for key in intents}


def _git_publisher(env, path, log_path):
    """Reutiliza a barreira Git existente: commit + push de ambos os ficheiros ANTES do envio."""
    if env.get('NOTIFY_DURABLE') != 'git' or env.get('GITHUB_REF_NAME') != 'main':
        raise StakeStateError('Stake alerts require NOTIFY_DURABLE=git on main')
    store = SimpleNamespace(state_path=path, log_path=log_path)
    return notify.GitPublisher.for_store(store, 'main')


def deliver(signals, now=None, env=None, sender=None, path=None, publisher=None, clock=None):
    """No máximo uma tentativa por aposta, apenas após prova de publicação remota da intenção."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    clock = clock or notify.run_clock(now)
    token, chat = (env.get('TG_BOT_TOKEN') or '').strip(), (env.get('TG_CHAT_ID') or '').strip()
    if not token or not chat:
        print('STAKE_ALERTS skipped (no keys)')
        return []
    candidates = matches(signals, now)
    if not candidates:  # sem sinais Stake elegíveis, não há qualquer envio a proteger
        print('STAKE_ALERTS sent 0')
        return []
    path = Path(path) if path else data_dir() / STATE_NAME
    with notify.outbox_lock(path.parent):
        return _deliver(candidates, now, env, sender, path, publisher, token, chat, clock)


def _deliver(candidates, now, env, sender, path, publisher, token, chat, clock):
    log_path = path.with_name(LOG_NAME)
    state = load_state(path)
    intents, lines = load_log(log_path)
    verify_state(state, intents)
    general = notify.claimed_items(path.parent)
    pending = [m for m in candidates if m['key'] not in intents
               and notify.item_key(m['b']) not in general
               and '|'.join(str(m['b'][k]) for k in ('match_id', 'market', 'selection')) not in intents]
    if not pending:
        print('STAKE_ALERTS sent 0')
        return []
    publish = publisher if publisher is not None else _git_publisher(env, path, log_path)
    sender = sender or notify.send
    sent = []
    rejected = {event['key'] for event in map(json.loads, lines[1:]) if event['event'] == 'rejected'}

    def persist(event, key, stamp):
        lines.append(json.dumps({'event': event, 'key': key, 'at': stamp}, ensure_ascii=False, sort_keys=True))
        log_tmp = log_path.with_suffix('.tmp')
        log_tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.replace(log_tmp, log_path)
        if event == 'intent':
            state['sent'][key] = stamp
        else:
            del state['sent'][key]
        state['sent_total'] = len(state['sent'])
        _atomic_json(path, state)

    for m in pending:
        if not freshness.current(m['b'], clock()):
            continue
        key, stamp = m['key'], now.isoformat(timespec='seconds')
        # Gravação local completa; se o runner parar antes do push, o aviso não é enviado.
        persist('intent', key, stamp)
        if publish(key) is not True:
            raise StakeStateError('stake intent not confirmed on remote main')
        send_time = clock()
        if not freshness.current(m['b'], send_time):
            persist('expired', key, send_time.isoformat())
            print('STAKE_ALERTS_EXPIRED_BEFORE_SEND')
            continue
        try:
            sender(token, chat, text(m, send_time))
        except notify.DeliveryFailed as exc:
            if exc.retryable and key not in rejected and freshness.current(m['b'], clock()):
                persist('rejected', key, clock().isoformat())
            raise StakeStateError('stake Telegram rejection confirmed') from None
        except Exception as exc:
            print('STAKE_ALERTS_SEND_FAILED', type(exc).__name__)
            raise StakeStateError('stake Telegram delivery failed or ambiguous') from None
        sent.append(key)
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

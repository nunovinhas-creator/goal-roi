"""Sinais originais para o aviso fixo de Over 2,5 pré-jogo."""

from . import chronology, freshness
from .eligibility import eligible, game_identity

REPORT_KEY = 'polymarket_over25'
ACTIVATION = 'polymarket_over25_v1'


def below(value, limit):
    return type(value) in (int, float) and 0 <= value < limit


def candidates(messages, finished_matches, now):
    """Avalia cada mensagem original, com a quota, o valor e a odd do mesmo sinal."""
    out = []
    for m in messages:
        if (m.get('type') != 'POLYMARKET' or m.get('phase') != 'pre'
                or m.get('market') != 'Asian Total' or m.get('selection') != 'Over 2.5'
                or not below(m.get('share'), 80) or not below(m.get('total'), 10_000)):
            continue
        original = m.get('bet') is True and all(
            m.get(k) is False for k in ('had_ft_at_first_sight', 'edited_at_first_sight', 'burst')
        )
        if not eligible(dict(m, in_roi=original)) or game_identity(m) in finished_matches:
            continue
        times = {'date_utc': m.get('date_utc'), 'signal_seen_utc': m.get('collected_at_utc')}
        seen = chronology.signal_seen(times)
        if not (freshness.current(m, now) and seen and seen <= now):
            continue
        out.append({
            'match_id': game_identity(m), 'match': ' — '.join(m.get('teams') or []),
            **times, 'kickoff_utc': m['kickoff_utc'],
            'type': m['type'], 'phase': m['phase'], 'market': m['market'], 'selection': m['selection'],
            'odds': m['odds'], 'share': m['share'], 'total': m['total'],
        })
    return out


def after_activation(rows, cutoff, now):
    """Só sinais emitidos e observados depois da primeira execução do notificador."""
    if cutoff is None or not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        sent = chronology.signal_time(row)
        seen = chronology.signal_seen(row)
        if sent and seen and cutoff < sent <= seen <= now:
            yield dict(row, reasons=[], polymarket_over25=True)

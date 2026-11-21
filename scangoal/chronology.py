"""Cronologia verificável (PR 3/5): quando é que o sistema viu cada sinal e cada resultado. Sem rede, sem escrita.

Instantes distintos de um sinal (nunca se confundem):
- date_utc: hora da mensagem do bot (Telegram) — a hora do sinal;
- kickoff_utc: hora de início do jogo anunciada pelo bot;
- fim do jogo e hora do resultado no mundo: NÃO são observados pelo sistema; nunca se inventam;
- signal_seen_utc: primeira recolha (collected_at_utc) em que o sistema leu a mensagem do sinal;
- result_known_utc: primeira observação, pelo sistema, do resultado efetivamente usado para liquidar o sinal
  (recolha da versão da mensagem com o resultado, matched_at_utc da Highlightly/BSD, fetched_at_utc dos minutos dos golos);
- hora da decisão: o instante a partir do qual o sistema podia decidir sobre o sinal = max(date_utc, signal_seen_utc).

Os carimbos do sistema (collected_at_utc, matched_at_utc, fetched_at_utc) são escritos no INÍCIO de cada passo de recolha;
a leitura efetiva acontece depois (há sinais com collected_at_utc 1 s antes da própria mensagem). Por isso, de forma
conservadora nos dois sentidos:
- um resultado só conta como disponível OBSERVATION_MARGIN_MIN depois do carimbo (limite dos timeouts dos passos:
  leitura do Telegram 220 s, Highlightly 8 × 25 s, BSD 12 × 20 s);
- a informação de uma decisão é a que existia no carimbo da leitura do sinal (nunca a de depois);
- um sinal só é decidível se, mesmo com o relatório a sair DECISION_DELAY_MAX_MIN depois do carimbo (timeout do job),
  ainda dava para apostar.

Política fail-closed: um instante ausente, malformado, sem fuso horário ou contraditório (resultado observado antes do sinal ou
antes do início do jogo) é DESCONHECIDO. Um resultado com hora desconhecida nunca entra numa decisão histórica: só conta no
instante da avaliação ("agora"), depois de todos os resultados com hora provada.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

UTC = timezone.utc
DECISION_MAX_LAG_MIN = 30   # igual a notify.ALERT_MAX_AGE_MIN: um sinal só é decidível enquanto ainda dá para apostar
OBSERVATION_MARGIN_MIN = 5  # carimbo no início do passo; a leitura acaba até 4 min depois (timeouts do workflow)
DECISION_DELAY_MAX_MIN = 15 # o relatório e os avisos saem, no pior caso, até ao timeout do job (15 min)


def parse_utc(value):
    """ISO 8601 com fuso explícito -> datetime UTC; qualquer outra coisa -> None (nunca presume o fuso)."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _parse_utc(value)


@lru_cache(maxsize=65536)
def _parse_utc(value):
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        return None
    try:
        return dt.astimezone(UTC) if dt.tzinfo else None
    except OverflowError:
        return None


def _loose_utc(value):
    """Para date_utc/kickoff_utc do bot (já usados assim noutros módulos): sem fuso conta como UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _loose_cached(value)


@lru_cache(maxsize=65536)
def _loose_cached(value):
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def signal_time(b):
    return _loose_utc(b.get('date_utc'))


def kickoff_time(b):
    return _loose_utc(b.get('kickoff_utc'))


def signal_seen(b):
    """Primeira vez que o sistema leu o sinal (nunca antes da mensagem); None se não provado ou contraditório.

    Contraditório: carimbo mais de OBSERVATION_MARGIN_MIN antes da própria mensagem (a leitura não pode preceder o envio).
    """
    seen, sent = parse_utc(b.get('signal_seen_utc')), signal_time(b)
    if seen is None or sent is None or seen < sent - timedelta(minutes=OBSERVATION_MARGIN_MIN):
        return None
    return max(seen, sent)


def result_check(b):
    """-> (datetime ou None, motivo): 'verified', 'unknown' (sem prova) ou 'contradictory' (antes do sinal ou do início)."""
    if 'result_available_utc' in b:
        known = parse_utc(b['result_available_utc'])
    else:
        stamp = parse_utc(b.get('result_known_utc'))
        known = stamp + timedelta(minutes=OBSERVATION_MARGIN_MIN) if stamp is not None else None
    if known is None:
        return None, 'unknown'
    sent, ko = signal_time(b), kickoff_time(b)
    if sent is None or known < sent or (ko is not None and known < ko):
        return None, 'contradictory'
    return known, 'verified'


def result_known(b):
    """Instante provado em que o resultado ficou disponível ao sistema, ou None (desconhecido: nunca antes de "agora")."""
    return result_check(b)[0]


def decision_time(b):
    """Quando o sistema podia decidir sobre o sinal: só depois de o ter lido. None se a leitura não está provada."""
    return signal_seen(b)   # signal_seen já devolve max(leitura, envio)


def decidable(b):
    """O sinal foi lido enquanto ainda dava para apostar (jogo por começar, ou até DECISION_MAX_LAG_MIN depois do sinal)?

    Sinais lidos tarde (por exemplo, na recolha inicial de 12/11, que leu mensagens de dias anteriores) não correspondem a
    nenhuma decisão que o sistema pudesse ter tomado: são "não reconstituíveis", nunca decisões prospetivas.
    """
    t = decision_time(b)
    if t is None:
        return False
    latest = t + timedelta(minutes=DECISION_DELAY_MAX_MIN)
    ko = kickoff_time(b)
    if b.get('phase') == 'pre' and ko is not None:
        return latest < ko
    if ko is not None and ko > latest:
        return True
    return latest - signal_time(b) <= timedelta(minutes=DECISION_MAX_LAG_MIN)


def born(h):
    """Hora de registo de uma hipótese; None se ausente ou malformada (a hipótese não sustenta decisões)."""
    return parse_utc(h.get('born_utc')) if isinstance(h, dict) else None


def registry_at(registry, as_of):
    return {hid: h for hid, h in registry.items() if (stamp := born(h)) is not None and stamp <= as_of}


def snapshot_at(state, as_of):
    """Select a previously saved telemetry snapshot by its completion time."""
    versions = state.get('snapshots') or []
    versions = versions if isinstance(versions, list) else []
    visible = [(stamp, index, row) for index, row in enumerate([*versions, state]) if isinstance(row, dict)
               and (stamp := parse_utc(row.get('last_run_utc'))) is not None and stamp <= as_of]
    return max(visible)[2] if visible else {}


def archive_snapshot(current, previous, *fields):
    """Preserve the previous summary before updating a mutable state file."""
    before = {key: value for key, value in previous.items() if key in fields or key == 'last_run_utc'}
    after = {key: value for key, value in current.items() if key in fields or key == 'last_run_utc'}
    versions = previous.get('snapshots', [])
    if before != after and parse_utc(before.get('last_run_utc')) is not None and (not versions or versions[-1] != before):
        versions = [*versions, before]
    return dict(current, snapshots=versions) if versions else current


def iso(dt):
    return dt.astimezone(UTC).isoformat(timespec='seconds') if dt else None


def as_of_view(signals, as_of):
    """O que o sistema sabia no instante `as_of` (revisão #5912303516): para avaliações com relógio explícito.

    - sinais lidos depois de `as_of` não existem ainda; sem prova de leitura (carimbo ausente, inválido, sem fuso ou
      contraditório) também não — fail-closed (revisão #5912461690, P2-K), mesmo que emitidos antes de `as_of`;
    - sem observação provada até `as_of`, a aposta fica em aberto;
    - a primeira observação de um resultado do jogo posterior a `as_of` ainda não aconteceu.
    """
    from .opportunity_results import settle_research_bet

    out = []
    for b in signals:
        t = decision_time(b)
        if t is None or t > as_of:   # sem prova de leitura até `as_of` (ausente, inválida, contraditória): não existe (P2-K)
            continue
        if '_ft_observations' in b:
            out.append(settle_research_bet(b, as_of))
            continue
        known = result_known(b)
        if (b.get('w') is not None or b.get('ft') is not None) and (known is None or known > as_of):
            b = dict(b, w=None, ft=None, settled_by=None, result_known_utc=None, result_known_source=None)
            b.pop('result_available_utc', None)
        first = parse_utc(b.get('match_result_first_seen_utc'))
        if first is not None and first > as_of:
            b = dict(b, match_result_first_seen_utc=None)
        quarantine = parse_utc(b.get('result_quarantined_utc'))
        if b.get('result_quarantined') and quarantine is not None and quarantine > as_of:
            b = dict(b, result_quarantined=False, result_quarantined_utc=None)
        out.append(b)
    return out

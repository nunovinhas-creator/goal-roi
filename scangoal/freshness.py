"""Validade temporal de uma oportunidade de aposta: um sinal só é apresentado como atual enquanto ainda dá para apostar.

Incidente de 17/11/2026 (12:58-13:07 UTC): a tática "Betfair | Faixa de odd = odd acima de 3,00 & Momento = durante o jogo"
passou a promissora e o Telegram enviou como "A seguir" doze sinais ao vivo com 47 minutos a três dias de idade. O filtro
temporal só se aplicava aos alertas certificados; os indícios passavam com qualquer idade, e `brain.watch` incluía qualquer
aposta sem resultado conhecido. Este módulo é a regra única, usada em dois sítios independentes:
  1. `brain.build` (relógio do relatório): `watch` só lista oportunidades atuais;
  2. `notify.game_notices` (relógio do envio): barreira final, para TODOS os níveis (indício e alerta; promissora, forte,
     confirmada), mesmo que o relatório venha de outro código ou de outra hora.

Política (conservadora: na dúvida, não é oportunidade atual; os sinais continuam intactos como dados históricos):
- Hora de referência: `date_utc`, a hora ORIGINAL do sinal (mensagem do bot). Nunca a hora de recolha, de geração do
  relatório ou de promoção da tática. Ausente, ilegível ou no futuro (além de FUTURE_SKEW_MIN de tolerância de relógio):
  rejeitado.
- Jogo comprovadamente terminado (resultado final conhecido, por qualquer fonte): rejeitado. A ausência de resultado NÃO
  prova que o jogo está a decorrer: só os limites de tempo abaixo decidem.
- LIVE: elegível só se o sinal tiver no máximo LIVE_MAX_AGE_MIN minutos. Com hora de início conhecida, também rejeitado se o
  início ainda não aconteceu (sinal ao vivo incoerente) ou se já passaram mais de GAME_MAX_MIN minutos desde o início.
- PREMATCH: elegível só com hora de início conhecida e ainda no futuro. Depois do início comprovado: rejeitado (não existe,
  hoje, nenhuma regra explícita e testada que permita um mercado pré-jogo depois do início). Sem hora de início: rejeitado
  (não se pode provar que ainda não começou). Início mais de PREMATCH_MAX_LEAD_DAYS depois do sinal: incoerente, rejeitado.
- Fase ausente ou desconhecida: 'pre' se o sinal for anterior ao início conhecido; senão aplica-se a regra LIVE (a mais curta).
"""
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
LIVE_MAX_AGE_MIN = 30          # sinal ao vivo: no máximo 30 minutos depois da hora original do sinal
FUTURE_SKEW_MIN = 5            # tolerância de relógio para um sinal "do futuro" (relógio do executor vs Telegram)
GAME_MAX_MIN = 150             # 90 + intervalo + compensação, com folga: depois disto o jogo já não está a decorrer
PREMATCH_MAX_LEAD_DAYS = 7     # nos dados reais a maior antecedência é de 2 dias; mais de 7 é uma hora de início incoerente
POLICY = 'signal_time_live30_prematch_before_kickoff_v1'

REASONS = ('ok', 'signal_time_missing', 'signal_time_invalid', 'signal_time_future', 'finished', 'live_too_old',
           'live_before_kickoff', 'live_game_over', 'prematch_started', 'prematch_no_kickoff', 'kickoff_invalid',
           'prematch_kickoff_incoherent', 'clock_invalid')


def parse(value):
    """Data ISO 8601 -> datetime com fuso (sem fuso conta como UTC, como no resto do projeto); senão None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def phase_of(w, ko=None, signal=None):
    p = w.get('phase')
    if p in ('pre', 'live'):
        return p
    return 'pre' if ko is not None and signal is not None and signal < ko else 'live'


def finished(w):
    """Jogo comprovadamente terminado: o cérebro marca `finished` (resultado final visto por qualquer fonte); `ft` e `w`
    também o provam. A ausência destes campos não prova nada (nunca quer dizer "a decorrer")."""
    return bool(w.get('finished')) or bool(w.get('ft')) or w.get('w') is not None


def check(w, now):
    """-> (elegível, motivo). `w`: entrada de `brain.watch` ou aposta em aberto (date_utc, kickoff_utc, phase, ...)."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        return False, 'clock_invalid'
    raw = w.get('date_utc')
    if raw in (None, ''):
        return False, 'signal_time_missing'
    signal = parse(raw)
    if signal is None:
        return False, 'signal_time_invalid'
    if signal - now > timedelta(minutes=FUTURE_SKEW_MIN):
        return False, 'signal_time_future'
    if finished(w):
        return False, 'finished'
    ko = None
    if w.get('kickoff_utc') not in (None, ''):
        ko = parse(w['kickoff_utc'])
        if ko is None:
            return False, 'kickoff_invalid'
    if phase_of(w, ko, signal) == 'pre':
        if ko is None:
            return False, 'prematch_no_kickoff'
        if ko - signal > timedelta(days=PREMATCH_MAX_LEAD_DAYS):
            return False, 'prematch_kickoff_incoherent'
        if ko <= now:
            return False, 'prematch_started'
        return True, 'ok'
    if now - signal > timedelta(minutes=LIVE_MAX_AGE_MIN):
        return False, 'live_too_old'
    if ko is not None:
        if ko - now > timedelta(minutes=FUTURE_SKEW_MIN):
            return False, 'live_before_kickoff'
        if now - ko > timedelta(minutes=GAME_MAX_MIN):
            return False, 'live_game_over'
    return True, 'ok'


def current(w, now):
    """True só se o sinal ainda é uma oportunidade atual em `now` (ver a política no topo do módulo)."""
    return check(w, now)[0]

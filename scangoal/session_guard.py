"""Diagnóstico e proteção da sessão Telegram (MÓDULO ISOLADO: não é importado por reader, watch nem workflows).

Objetivo (auditoria P0 da TG_SESSION, 2026-11-27):
- distinguir AUTH_KEY_UNREGISTERED, SESSION_REVOKED, SESSION_EXPIRED, AUTH_KEY_DUPLICATED, USER_DEACTIVATED e falhas
  transitórias, em vez de o reader colapsar tudo em SESSION_NOT_AUTHORIZED (Telethon.is_user_authorized apanha todo o
  RPCError);
- circuit breaker: perante uma sessão definitivamente invalidada, não repetir tentativas inúteis, alertar UMA vez e
  reabrir só quando o segredo mudar;
- exclusividade local de uso da sessão (lease com TTL);
- nunca registar segredos (redação de StringSession, api_hash, tokens de bot).

Não abre ligações ao Telegram por si: `diagnose` recebe um cliente já ligado. Sem dependência de Telethon: as
exceções são classificadas pelo nome da classe (e das bases), o que cobre as classes reais do Telethon.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Erros que o servidor devolve quando a chave de autorização já não vale. Repetir não ajuda; é preciso nova sessão.
DEFINITIVE = {
    'AuthKeyUnregisteredError': 'AUTH_KEY_UNREGISTERED',
    'AuthKeyInvalidError': 'AUTH_KEY_INVALID',
    'AuthKeyPermEmptyError': 'AUTH_KEY_PERM_EMPTY',
    'AuthKeyDuplicatedError': 'AUTH_KEY_DUPLICATED',
    'SessionRevokedError': 'SESSION_REVOKED',
    'SessionExpiredError': 'SESSION_EXPIRED',
    'UserDeactivatedError': 'USER_DEACTIVATED',
    'UserDeactivatedBanError': 'USER_DEACTIVATED_BAN',
    'UnauthorizedError': 'UNAUTHORIZED',
}
# Falhas em que repetir mais tarde é razoável.
TRANSIENT = {'ServerError', 'RpcCallFailError', 'RpcMcgetFailError', 'TimedOutError', 'FloodWaitError',
             'FloodError', 'SlowModeWaitError', 'InterdcCallErrorError', 'ConnectionError', 'TimeoutError',
             'CancelledError', 'IncompleteReadError', 'OSError'}
# Casos em que a sessão ficou invalidada por uso concorrente (relevante para a causa-raiz).
CONCURRENCY_SUSPECT = {'AUTH_KEY_DUPLICATED'}


@dataclass(frozen=True)
class Diagnosis:
    kind: str          # nome canónico (ex.: SESSION_REVOKED) ou TRANSIENT / UNKNOWN
    definitive: bool   # True: a sessão está invalidada; não repetir
    retry: bool        # True: repetir mais tarde pode resolver


def classify(exc):
    """Classifica uma exceção sem depender de Telethon e sem usar a mensagem (pode conter dados)."""
    names = [cls.__name__ for cls in type(exc).__mro__]
    for name in names:
        if name in DEFINITIVE:
            return Diagnosis(DEFINITIVE[name], True, False)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return Diagnosis('TRANSIENT', False, True)
    for name in names:
        if name in TRANSIENT:
            return Diagnosis('TRANSIENT', False, True)
    return Diagnosis('UNKNOWN', False, False)


async def diagnose(client, request, timeout=30):
    """Faz UM pedido autenticado (ex.: `functions.updates.GetStateRequest()`) e devolve o Diagnosis.
    Ao contrário de `is_user_authorized`, não esconde o tipo de erro. Devolve None se a sessão responde bem."""
    try:
        await asyncio.wait_for(client(request), timeout)
    except Exception as exc:    # noqa: BLE001 - só o tipo interessa; nunca guardar str(exc)
        return classify(exc)
    return None


_SECRET_PATTERNS = (
    re.compile(r'1[A-Za-z0-9_\-+/=]{200,}'),                    # StringSession (versão 1 + base64 longo)
    re.compile(r'\b[0-9]{6,}:[A-Za-z0-9_\-]{30,}\b'),            # token de bot
    re.compile(r'\b[0-9a-f]{32}\b', re.I),                       # api_hash
)


def redact(text):
    """Remove de `text` tudo o que se parece com um segredo do Telegram."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text


def fingerprint(session, api_hash):
    """Identificador curto e não reversível da sessão instalada (HMAC com o api_hash). Serve só para saber se o
    segredo mudou desde o último incidente; não permite reconstruir nem verificar a sessão sem o api_hash."""
    return hmac.new(api_hash.encode(), ''.join(session.split()).encode(), hashlib.sha256).hexdigest()[:8]


def _utc(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _write_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.guard-', delete=False, encoding='utf-8') as file:
        temporary = Path(file.name)
        json.dump(value, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


class Breaker:
    """Circuit breaker de sessão. Estado persistente pequeno e sem segredos (só o fingerprint).

    decide(diagnosis, fp, now) -> 'proceed' | 'skip' | 'alert'
      - sessão boa ou falha transitória: 'proceed' (transitórias nunca abrem o breaker)
      - primeira falha definitiva: abre e devolve 'alert' (um alerta por incidente)
      - breaker aberto e mesmo fingerprint: 'skip' (sem tentativas inúteis, sem novos alertas)
      - fingerprint diferente (segredo substituído): fecha e deixa tentar ('proceed')
    """

    def __init__(self, path, realert=timedelta(hours=24)):
        self.path, self.realert = Path(path), realert

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def decide(self, diagnosis, fp, now=None):
        now, state = _utc(now), self.load()
        if state.get('open') and state.get('fingerprint') != fp:
            state = {}                                  # segredo novo: tentar de novo
            _write_atomic(self.path, {'open': False, 'fingerprint': fp})
        if diagnosis is None:                           # a sessão responde: fechar o breaker, se estava aberto
            if state.get('open'):
                _write_atomic(self.path, {'open': False, 'fingerprint': fp})
            return 'proceed'
        if not diagnosis.definitive:                    # transitória: nunca abre; com breaker aberto, não insistir
            return 'skip' if state.get('open') else 'proceed'
        if not state.get('open'):
            _write_atomic(self.path, {'open': True, 'kind': diagnosis.kind, 'fingerprint': fp,
                                      'opened_utc': now.isoformat(timespec='seconds'),
                                      'alerted_utc': now.isoformat(timespec='seconds')})
            return 'alert'
        alerted = datetime.fromisoformat(state['alerted_utc'])
        if now - alerted >= self.realert:
            state['alerted_utc'] = now.isoformat(timespec='seconds')
            _write_atomic(self.path, state)
            return 'alert'
        return 'skip'


def alert_text(diagnosis, when=None):
    """Texto do alerta, sem dados sensíveis. Não pede nunca para colar a sessão num chat."""
    when = _utc(when).strftime('%Y-%m-%d %H:%MZ')
    return (f'Sessão Telegram invalidada ({diagnosis.kind}) às {when}. A recolha de mensagens está parada. '
            'Gerar nova sessão com scripts/authorize_qr.py e substituir o Secret TG_SESSION.')


class Lease:
    """Lease local com TTL: impede que dois processos do MESMO anfitrião usem a sessão em simultâneo.
    Não substitui a exclusividade entre máquinas (isso é o grupo de concorrência do GitHub ou a regra
    «uma sessão por consumidor»). Uso: `with Lease(path, ttl, owner): ...`"""

    def __init__(self, path, ttl=timedelta(minutes=20), owner=None, clock=None):
        self.path, self.ttl = Path(path), ttl
        self.owner = owner or f'{socket.gethostname()}:{os.getpid()}'
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _locked(self):
        """Exclusão mútua local (flock) à volta de ler-decidir-escrever; evita ver um lease a meio de ser escrito."""
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(self.path) + '.lock', 'a')
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def acquire(self):
        now = self.clock()
        with self._locked():
            try:
                held = json.loads(self.path.read_text(encoding='utf-8'))
                if datetime.fromisoformat(held['expires_utc']) > now and held['owner'] != self.owner:
                    return False
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError):
                pass                                                              # lease ilegível: tratar como expirada
            _write_atomic(self.path, {'owner': self.owner, 'expires_utc': (now + self.ttl).isoformat()})
            return True

    def release(self):
        with self._locked():
            try:
                if json.loads(self.path.read_text(encoding='utf-8')).get('owner') == self.owner:
                    self.path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError('SESSION_IN_USE')
        return self

    def __exit__(self, *exc):
        self.release()

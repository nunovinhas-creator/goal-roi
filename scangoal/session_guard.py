"""Diagnóstico e protecção isolados da sessão Telegram.

O chamador fornece a ligação, um único pedido e, opcionalmente, um serviço de alertas.
O módulo não é usado pelo reader, pelo watch ou pelos workflows.
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

DEFINITIVE = {
    'AuthKeyUnregisteredError': 'AUTH_KEY_UNREGISTERED',
    'AuthKeyInvalidError': 'AUTH_KEY_INVALID',
    'AuthKeyDuplicatedError': 'AUTH_KEY_DUPLICATED',
    'SessionRevokedError': 'SESSION_REVOKED',
    'SessionExpiredError': 'SESSION_EXPIRED',
    'UserDeactivatedError': 'USER_DEACTIVATED',
    'UserDeactivatedBanError': 'USER_DEACTIVATED_BAN',
}
TRANSIENT = {'ServerError', 'RpcCallFailError', 'RpcMcgetFailError', 'TimedOutError', 'InterdcCallErrorError'}
FLOOD_ERRORS = {'FloodError', 'FloodWaitError', 'FloodPremiumWaitError', 'SlowModeWaitError', 'FloodTestPhoneWaitError'}
CONCURRENCY_SUSPECT = {'AUTH_KEY_DUPLICATED'}


@dataclass(frozen=True)
class Diagnosis:
    kind: str
    definitive: bool
    retry: bool
    retry_after: int | None = None


@dataclass(frozen=True)
class Decision:
    action: str
    diagnosis: Diagnosis | None = None
    not_before: datetime | None = None


def classify(exc):
    """Usa classes conhecidas e, para FloodWait, apenas o campo numérico seconds."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    names = [cls.__name__ for cls in type(exc).__mro__]
    for name in names:
        if name in DEFINITIVE:
            return Diagnosis(DEFINITIVE[name], True, False)
    if FLOOD_ERRORS.intersection(names):
        seconds = getattr(exc, 'seconds', None)
        if type(seconds) is int and seconds >= 0:
            return Diagnosis('FLOOD_WAIT', False, True, max(1, seconds))
        return Diagnosis('FLOOD_WAIT', False, False)
    if TRANSIENT.intersection(names) or isinstance(
        exc, (TimeoutError, ConnectionError, asyncio.IncompleteReadError, socket.gaierror)
    ):
        return Diagnosis('TRANSIENT', False, True)
    return Diagnosis('UNKNOWN', False, False)


async def diagnose(client, request, timeout=30):
    """Faz um pedido autenticado e devolve None se responder com sucesso."""
    try:
        await asyncio.wait_for(client(request), timeout)
    except Exception as exc:
        return classify(exc)
    return None


_SECRET_PATTERNS = (
    re.compile(r'1[A-Za-z0-9_\-+/=]{200,}'),
    re.compile(r'\b[0-9]{6,}:[A-Za-z0-9_\-]{30,}\b'),
    re.compile(r'\b[0-9a-f]{32}\b', re.I),
)
_FINGERPRINT = re.compile(r'[0-9a-f]{64}')
_AUTH_FIELDS = {'kind', 'fingerprint', 'opened_utc', 'alerted_utc'}
_WAIT_FIELDS = {'not_before_utc', 'flood_unknown'}
_STATE_FIELDS = {'version', 'open'} | _AUTH_FIELDS | _WAIT_FIELDS


def redact(text):
    """Remove padrões que correspondem a segredos do Telegram."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text


def fingerprint(session, api_hash):
    """Identifica alterações da sessão com HMAC-SHA256, sem guardar a sessão."""
    return hmac.new(api_hash.encode(), ''.join(session.split()).encode(), hashlib.sha256).hexdigest()


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('EXPECTED_AWARE_DATETIME')
    return value.astimezone(timezone.utc)


def _parse_utc(value):
    if not isinstance(value, str):
        raise ValueError
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError
    return stamp


def _unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


class GuardStateError(RuntimeError):
    """Estado ausente, inválido ou inacessível no armazenamento esperado."""


def _validate_state(state):
    if (
        not isinstance(state, dict)
        or type(state.get('version')) is not int
        or state['version'] != 1
        or type(state.get('open')) is not bool
        or state.keys() - _STATE_FIELDS
    ):
        raise ValueError
    if state['open']:
        if state.get('kind') not in DEFINITIVE.values() or not _FINGERPRINT.fullmatch(state.get('fingerprint', '')):
            raise ValueError
        opened = _parse_utc(state.get('opened_utc'))
        if 'alerted_utc' in state and _parse_utc(state['alerted_utc']) < opened:
            raise ValueError
    elif _AUTH_FIELDS & state.keys():
        raise ValueError
    if 'not_before_utc' in state:
        _parse_utc(state['not_before_utc'])
    if 'flood_unknown' in state and state['flood_unknown'] is not True:
        raise ValueError
    return state


def _write_atomic(path, value):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.guard-', delete=False, encoding='utf-8') as file:
            temporary = Path(file.name)
            json.dump(value, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Breaker:
    """Protege uma origem de recolha, incluindo o prazo FloodWait após uma troca de sessão.

    run() mantém o lock durante a decisão, a ligação e a confirmação do alerta.
    Ao usar os métodos individuais, o chamador tem de garantir a mesma exclusão.
    Para vários runners, usar o mesmo volume durável com flock e substituição atómica,
    inicializado antes da utilização e aberto com require_existing=True.
    """

    def __init__(self, path, realert=timedelta(hours=24), *, require_existing=False):
        self.path, self.realert = Path(path), realert
        self.require_existing = require_existing

    def _read(self):
        try:
            raw = self.path.read_text(encoding='utf-8')
            return _validate_state(json.loads(raw, object_pairs_hook=_unique_keys))
        except FileNotFoundError:
            raise
        except (OSError, ValueError, TypeError, RecursionError):
            raise GuardStateError('SESSION_GUARD_STATE_INVALID_OR_UNREADABLE') from None

    def load(self):
        try:
            return self._read()
        except FileNotFoundError:
            if self.require_existing:
                raise GuardStateError('SESSION_GUARD_STATE_MISSING') from None
            return {}

    def _save(self, state):
        try:
            _write_atomic(self.path, state)
        except OSError:
            raise GuardStateError('SESSION_GUARD_STATE_NOT_SAVED') from None

    def initialize(self):
        with Lease(self.path):
            try:
                self._read()
            except FileNotFoundError:
                self._save({'version': 1, 'open': False})

    @staticmethod
    def _check_fingerprint(fp):
        if not isinstance(fp, str) or not _FINGERPRINT.fullmatch(fp):
            raise ValueError('EXPECTED_HMAC_SHA256')

    @staticmethod
    def _gate(state, fp, now):
        if state.get('flood_unknown'):
            return Decision('stop', Diagnosis('FLOOD_WAIT', False, False))
        if 'not_before_utc' in state:
            deadline = _parse_utc(state['not_before_utc'])
            if now < deadline:
                return Decision('defer', not_before=deadline)
        if state.get('open') and state['fingerprint'] == fp:
            return Decision('skip', Diagnosis(state['kind'], True, False))
        return Decision('proceed')

    def before_connect(self, fp, now=None):
        self._check_fingerprint(fp)
        return self._gate(self.load(), fp, _utc(now))

    def _alert_decision(self, state, now):
        alerted = state.get('alerted_utc')
        due = alerted is None or now - _parse_utc(alerted) >= self.realert
        return Decision('alert' if due else 'skip', Diagnosis(state['kind'], True, False))

    def decide(self, diagnosis, fp, now=None):
        self._check_fingerprint(fp)
        now = _utc(now)
        state = self.load() or {'version': 1, 'open': False}
        if diagnosis is not None and diagnosis.kind == 'FLOOD_WAIT':
            return self._record_wait(state, diagnosis, now)
        gate = self._gate(state, fp, now)
        if gate.action == 'skip':
            return self._alert_decision(state, now)
        if gate.action != 'proceed':
            return gate
        if diagnosis is None:
            if state['open']:
                self._save({key: value for key, value in state.items() if key not in _AUTH_FIELDS} | {'open': False})
            return gate
        if diagnosis.definitive and diagnosis.kind in DEFINITIVE.values():
            updated = {key: value for key, value in state.items() if key not in _AUTH_FIELDS}
            updated.update(open=True, kind=diagnosis.kind, fingerprint=fp, opened_utc=now.isoformat())
            self._save(updated)
            return Decision('alert', diagnosis)
        return Decision('retry' if diagnosis.kind == 'TRANSIENT' and diagnosis.retry else 'stop', diagnosis)

    def _record_wait(self, state, diagnosis, now):
        if state.get('flood_unknown'):
            return Decision('stop', Diagnosis('FLOOD_WAIT', False, False))
        seconds = diagnosis.retry_after
        if type(seconds) is int and seconds > 0:
            try:
                deadline = now + timedelta(seconds=seconds)
            except OverflowError:
                pass
            else:
                if 'not_before_utc' in state:
                    deadline = max(deadline, _parse_utc(state['not_before_utc']))
                self._save(state | {'not_before_utc': deadline.isoformat()})
                return Decision('defer', diagnosis, deadline)
        self._save(state | {'flood_unknown': True})
        return Decision('stop', Diagnosis('FLOOD_WAIT', False, False))

    def confirm_alert(self, fp, now=None):
        """Grava a confirmação só depois de o chamador obter uma entrega confirmada."""
        self._check_fingerprint(fp)
        now, state = _utc(now), self.load()
        if state.get('open') and state['fingerprint'] == fp:
            last = _parse_utc(state.get('alerted_utc', state['opened_utc']))
            self._save(state | {'alerted_utc': max(now, last).isoformat()})

    async def run(self, fp, connect, request, *, alert=None, check_timeout=30):
        """Executa no máximo uma ligação e um pedido, com uma factory de contexto assíncrono.

        O callback assíncrono de alerta recebe Diagnosis e devolve True após confirmação.
        Uma falha do callback mantém o alerta pendente para a próxima execução.
        """
        with Lease(self.path):
            decision = self.before_connect(fp)
            if decision.action == 'skip':
                decision = self._alert_decision(self.load(), _utc())
            elif decision.action == 'proceed':
                decision = None
                cancellation = None
                try:
                    async with connect() as client:
                        try:
                            decision = self.decide(await diagnose(client, request, check_timeout), fp)
                        except asyncio.CancelledError as exc:
                            cancellation = exc
                            raise
                except GuardStateError:
                    raise
                except Exception as exc:
                    if cancellation is not None:
                        raise cancellation from None
                    diagnosis = classify(exc)
                    if decision is None or decision.action in {'proceed', 'retry'} or diagnosis.kind == 'FLOOD_WAIT':
                        decision = self.decide(diagnosis, fp)
                if cancellation is not None:
                    raise cancellation
            if decision.action == 'alert' and alert is not None and await alert(decision.diagnosis) is True:
                self.confirm_alert(fp)
            return decision


def alert_text(diagnosis, when=None):
    """Produz um alerta com um código conhecido, sem serializar a excepção."""
    when = _utc(when).strftime('%Y-%m-%d %H:%MZ')
    kind = diagnosis.kind if diagnosis.kind in DEFINITIVE.values() else 'UNKNOWN'
    return (f'Sessão Telegram invalidada ({kind}) às {when}. A recolha de mensagens está parada. '
            'Gerar nova sessão com scripts/authorize_qr.py e substituir o Secret TG_SESSION.')


class Lease:
    """Exclusão POSIX enquanto o descritor está aberto, incluindo após o antigo TTL.

    A morte do processo liberta o lock. O ficheiro .lock mantém o mesmo inode.
    Os argumentos ttl, owner e clock são aceites para chamadas do protótipo anterior.
    """

    def __init__(self, path, ttl=timedelta(minutes=20), owner=None, clock=None):
        self.path = Path(path)
        self._handle = None

    def acquire(self):
        import fcntl

        if self._handle is not None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.with_name(self.path.name + '.lock').open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return True

    def release(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError('SESSION_IN_USE')
        return self

    def __exit__(self, *exc):
        self.release()

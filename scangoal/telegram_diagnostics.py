"""Metadados seguros dos erros da ligação de leitura do Telegram."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from . import authorization_events


# Os logs internos do Telethon podem formatar mensagens RPC e pedidos.
# Os pontos de entrada registam campos controlados a partir das exceções.
client_logger = logging.getLogger('scangoal.telegram_client')
client_logger.addHandler(logging.NullHandler())
client_logger.propagate = False


def error_fields(error):
    """Devolve o nome da classe e, para RPC, o código numérico normalizado."""
    from telethon.errors import RPCError

    name = type(error).__name__
    if not name.isascii() or not name.isidentifier():
        name = 'Exception'
    code = error.code if isinstance(error, RPCError) else None
    if type(code) is int and 100 <= code <= 599:
        return name, f'code={code}'
    return (name,)


def is_definitive_auth_error(error):
    from telethon import errors

    return isinstance(error, (
        errors.AuthKeyDuplicatedError,
        errors.AuthKeyInvalidError,
        errors.AuthKeyUnregisteredError,
        errors.SessionExpiredError,
        errors.SessionRevokedError,
        errors.UserDeactivatedError,
        errors.UserDeactivatedBanError,
    ))


def background_rpc_error(client, *, definitive_only=True):
    """Telethon 1.45 guarda aqui a exceção original do loop de updates."""
    error = getattr(client, '_updates_error', None)
    if error is None:
        return

    from telethon.errors import RPCError

    if isinstance(error, RPCError) and (not definitive_only or is_definitive_auth_error(error)):
        return error


def check_background_rpc_error(client, *, definitive_only=True):
    error = background_rpc_error(client, definitive_only=definitive_only)
    if error is not None:
        raise error


def report_authorization_loss(prefix, error):
    authorization_events.loss(error)
    marker = 'WATCH_PROBLEMS authorization_lost' if prefix == 'WATCH' else 'SCANGOAL_AUTHORIZATION_LOST'
    print(marker, *error_fields(error))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        try:
            with open(output, 'a', encoding='utf-8') as stream:
                stream.write('authorization=lost\n')
        except OSError as exc:
            print('TELEGRAM_DIAGNOSTICS_OUTPUT_FAILED', *error_fields(exc))


@asynccontextmanager
async def diagnosed_session(client, prefix, *, tolerate_disconnect_error=False):
    """Preserva a falha primária e observa revogações até ao fim do encerramento."""
    primary = None
    try:
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        authorization = primary if is_definitive_auth_error(primary) else background_rpc_error(client)
        if authorization is not None:
            report_authorization_loss(prefix, authorization)
        cleanup = None
        try:
            await client.disconnect()
        except BaseException as exc:
            cleanup = exc
            print(prefix + '_DISCONNECT_FAILED', *error_fields(exc))
        if authorization is None:
            authorization = cleanup if is_definitive_auth_error(cleanup) else background_rpc_error(client)
            if authorization is not None:
                report_authorization_loss(prefix, authorization)

        # O cancelamento do chamador tem precedência. Um erro secundário normal
        # não substitui a exceção original da operação.
        if (isinstance(cleanup, asyncio.CancelledError) and asyncio.current_task().cancelling()
                and not isinstance(primary, asyncio.CancelledError)):
            raise cleanup
        if primary is None:
            if cleanup is not None and not isinstance(cleanup, Exception):
                raise cleanup
            if authorization is not None:
                raise authorization
            if cleanup is not None and not tolerate_disconnect_error:
                raise cleanup

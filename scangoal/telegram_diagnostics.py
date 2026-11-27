"""Metadados seguros dos erros da ligação de leitura do Telegram."""

import logging


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


def check_background_rpc_error(client, *, definitive_only=True):
    """Telethon 1.45 guarda aqui a exceção original do loop de updates."""
    error = getattr(client, '_updates_error', None)
    if error is None:
        return

    from telethon.errors import RPCError

    if isinstance(error, RPCError) and (not definitive_only or is_definitive_auth_error(error)):
        raise error

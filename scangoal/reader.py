"""Leitor só de leitura da conversa com o bot ScanGoal (Telethon, conta de utilizador).

Segredos: TG_API_ID, TG_API_HASH, TG_SESSION (StringSession gerada por scripts/authorize.py)
e TG_SCANGOAL (ex.: @scangoal_bot). Nunca envia mensagens. Relê as últimas RECENT mensagens
em cada execução para apanhar as edições em que o bot acrescenta o resultado final.
"""
import asyncio, os, sys
from datetime import datetime, timezone

RECENT = int(os.environ.get('SCANGOAL_RECENT', '400'))


def buttons(msg):
    """Texto e URL dos botões da mensagem (metadados; ajudam a descobrir o que o bot oferece)."""
    try:
        return [[b.text, getattr(b, 'url', None)] for row in (msg.buttons or []) for b in row] or None
    except Exception:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds') if dt else None


async def collect():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from .store import append_versions, data_dir, load_seen
    session = ''.join(os.environ['TG_SESSION'].split())
    client = TelegramClient(StringSession(session), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'],
                            receive_updates=False, flood_sleep_threshold=0, connection_retries=1, request_retries=1)
    _, last_id = load_seen(data_dir() / 'messages.jsonl')
    now = iso(datetime.now(timezone.utc))
    rows, oldest = [], None
    await asyncio.wait_for(client.connect(), 30)
    try:
        if not await client.is_user_authorized():
            print('SCANGOAL_READ_FAILED SESSION_NOT_AUTHORIZED')  # nunca entra em login interativo
            return 1
        entity = await asyncio.wait_for(client.get_entity(os.environ['TG_SCANGOAL'].strip()), 30)
        async with asyncio.timeout(150):
            async for msg in client.iter_messages(entity, limit=RECENT):
                oldest = msg.id
                if getattr(msg, 'out', False) or not (msg.raw_text or '').strip():
                    continue
                rows.append({'message_id': msg.id, 'date_utc': iso(msg.date), 'edit_date_utc': iso(getattr(msg, 'edit_date', None)),
                             'text': msg.raw_text, 'buttons': buttons(msg), 'collected_at_utc': now})
    finally:
        await client.disconnect()
    added = append_versions(rows)
    if last_id and oldest and oldest > last_id + 1:
        print('SCANGOAL_GAP_POSSIBLE last_id', last_id, 'oldest_read', oldest)
    print('SCANGOAL_READ_OK read', len(rows), 'new_versions', added)
    return 0


def main():
    try:
        sys.exit(asyncio.run(collect()))
    except Exception as exc:  # o tipo basta; nunca imprimir segredos
        print('SCANGOAL_READ_FAILED', type(exc).__name__)
        sys.exit(1)


if __name__ == '__main__':
    main()

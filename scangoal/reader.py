"""Leitor só de leitura da conversa com o bot ScanGoal (Telethon, conta de utilizador).

Segredos: TG_API_ID, TG_API_HASH, TG_SESSION (StringSession gerada por scripts/authorize.py)
e TG_SCANGOAL (ex.: @scangoal_bot). Nunca envia mensagens. Relê as últimas RECENT mensagens
em cada execução para apanhar as edições em que o bot acrescenta o resultado final.
"""
import asyncio, json, os, sys
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


def make_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    session = ''.join(os.environ['TG_SESSION'].split())
    return TelegramClient(StringSession(session), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'],
                          receive_updates=False, flood_sleep_threshold=0, connection_retries=1, request_retries=1)


async def fetch(client, entity, limit, now):
    """Lê as últimas `limit` mensagens. -> (linhas a guardar, id mais antigo lido, ids presentes)."""
    rows, oldest, present = [], None, set()
    async for msg in client.iter_messages(entity, limit=limit):
        oldest = msg.id
        present.add(msg.id)
        if getattr(msg, 'out', False) or not (msg.raw_text or '').strip():
            continue
        rows.append({'message_id': msg.id, 'date_utc': iso(msg.date), 'edit_date_utc': iso(getattr(msg, 'edit_date', None)),
                     'text': msg.raw_text, 'buttons': buttons(msg), 'collected_at_utc': now})
    return rows, oldest, present


async def collect():
    from .store import append_versions, data_dir, load_seen
    client = make_client()
    _, last_id = load_seen(data_dir() / 'messages.jsonl')
    now = iso(datetime.now(timezone.utc))
    await asyncio.wait_for(client.connect(), 30)
    try:
        if not await client.is_user_authorized():
            print('SCANGOAL_READ_FAILED SESSION_NOT_AUTHORIZED')  # nunca entra em login interativo
            return 1
        entity = await asyncio.wait_for(client.get_entity(os.environ['TG_SCANGOAL'].strip()), 30)
        async with asyncio.timeout(150):
            rows, oldest, present = await fetch(client, entity, RECENT, now)
    finally:
        await client.disconnect()
    # Mensagens guardadas que já não existem na conversa (dentro da janela lida): o bot apagou-as. O registo mantém-nas.
    stored = {i for i, _ in load_seen(data_dir() / 'messages.jsonl')[0]}
    missing = sorted(i for i in stored if oldest and i >= oldest and i not in present)
    added = append_versions(rows)
    # Estado da recolha: prova de que correu, mesmo quando não há mensagens novas.
    (data_dir() / 'state.json').write_text(json.dumps({'last_run_utc': now, 'read': len(rows), 'new_versions': added, 'missing_ids': missing}) + '\n', encoding='utf-8')
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

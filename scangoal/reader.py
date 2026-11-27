"""Leitor só de leitura da conversa com o bot ScanGoal (Telethon, conta de utilizador).

Segredos: TG_API_ID, TG_API_HASH, TG_SESSION (StringSession gerada por scripts/authorize.py)
e TG_SCANGOAL (ex.: @scangoal_bot). Nunca envia mensagens. Relê as últimas RECENT mensagens
em cada execução para apanhar as edições em que o bot acrescenta o resultado final.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone

from .chronology import archive_snapshot
from .telegram_diagnostics import (check_background_rpc_error, client_logger, diagnosed_session,
                                   error_fields, is_definitive_auth_error)

AUTHORIZATION_EXIT = 4
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
                          receive_updates=False, flood_sleep_threshold=0, connection_retries=1, request_retries=1,
                          raise_last_call_error=True, base_logger=client_logger)


async def fetch(client, entity, limit, now, **bounds):
    """Lê as últimas `limit` mensagens. -> (linhas a guardar, id mais antigo lido, ids presentes)."""
    rows, oldest, present = [], None, set()
    async for msg in client.iter_messages(entity, limit=limit, **bounds):
        oldest = msg.id
        present.add(msg.id)
        if getattr(msg, 'out', False) or not (msg.raw_text or '').strip():
            continue
        rows.append({'message_id': msg.id, 'date_utc': iso(msg.date), 'edit_date_utc': iso(getattr(msg, 'edit_date', None)),
                     'text': msg.raw_text, 'buttons': buttons(msg), 'collected_at_utc': now})
    return rows, oldest, present


def stamp_receipt(rows):
    """Stamp a completed page with the time it became available to consumers."""
    completed = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row.update(collected_at_utc=completed, receipt_version=1)
    return completed


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def write_collection_state(path, previous, state):
    write_json(path, archive_snapshot(state, previous, 'missing_ids'))


def record_window(directory, previous, last_id, oldest, present, now):
    """Persist full-window evidence separately from fast reads and scientific inputs."""
    path = directory / 'collection_windows.json'
    state = json.loads(path.read_text()) if path.exists() else {'intervals': []}
    lower = state.get('full_high_id', last_id)
    prior = previous.get('last_run_utc')
    delayed = prior and (datetime.fromisoformat(now) - datetime.fromisoformat(prior)).total_seconds() >= 1800
    uncovered = bool(lower and oldest and oldest > lower + 1)
    if delayed or uncovered:
        state['intervals'].append({'previous_read': prior, 'observed_at': now, 'after_id': lower,
                                   'before_id': oldest, 'cursor': oldest,
                                   'status': 'pending' if uncovered else 'window_checked'})
        print('SCANGOAL_INTERRUPTION', prior, now, 'outside_window', uncovered)
    state['full_high_id'] = max(lower, max(present, default=0))
    write_json(path, state)
    return state


async def recover_page(client, entity, directory, state, now):
    """Archive one bounded page, isolated from reports, notices and the live message ledger."""
    gap = next((g for g in state['intervals'] if g['status'] == 'pending'), None)
    if gap is None:
        return
    archive = directory / 'collection_backfill'
    archive.mkdir(exist_ok=True)
    path = archive / f"{gap['after_id']}-{gap['before_id']}-{gap['cursor']}.json"
    if path.exists():
        page = json.loads(path.read_text())
    else:
        rows, oldest, ids = await fetch(client, entity, 100, now,
                                        min_id=gap['after_id'], offset_id=gap['cursor'])
        if any(not gap['after_id'] < i < gap['cursor'] for i in ids):
            raise ValueError('Backfill outside recorded interval')
        page = {'rows': rows, 'oldest': oldest, 'count': len(ids)}
        write_json(path, page)
    if page['count'] < 100:
        gap['status'] = 'available_history_checked'
    else:
        gap['cursor'] = page['oldest']
    write_json(directory / 'collection_windows.json', state)


async def collect():
    from .store import append_versions, data_dir, load_seen
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'state.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    client = make_client()
    _, last_id = load_seen(data_dir() / 'messages.jsonl')
    windows = directory / 'collection_windows.json'
    if not windows.exists():
        write_json(windows, {'full_high_id': last_id, 'intervals': []})
    now = iso(datetime.now(timezone.utc))
    async with diagnosed_session(client, 'SCANGOAL'):
        try:
            await asyncio.wait_for(client.connect(), 30)
            # A resolução e a leitura são os pedidos autenticados que já existiam.
            entity = await asyncio.wait_for(client.get_entity(os.environ['TG_SCANGOAL'].strip()), 30)
            async with asyncio.timeout(150):
                rows, oldest, present = await fetch(client, entity, RECENT, now)
        except (ConnectionError, asyncio.CancelledError):
            if not asyncio.current_task().cancelling():
                check_background_rpc_error(client, definitive_only=False)
            raise
        now = stamp_receipt(rows)
        added = append_versions(rows)
        state = record_window(directory, previous, last_id, oldest, present, now)
        # A frescura descreve a leitura principal já gravada, mesmo se a
        # autorização se perder no backfill ou durante a desconexão.
        stored = {i for i, _ in load_seen(directory / 'messages.jsonl')[0]}
        missing = sorted(i for i in stored if oldest and oldest <= i and i not in present)
        write_collection_state(path, previous, {'last_run_utc': now, 'read': len(rows),
                                               'new_versions': added, 'missing_ids': missing})
        if last_id and oldest and oldest > last_id + 1:
            print('SCANGOAL_GAP_POSSIBLE last_id', last_id, 'oldest_read', oldest)
        try:
            check_background_rpc_error(client)
            async with asyncio.timeout(15):
                await recover_page(client, entity, directory, state, now)
        except asyncio.CancelledError:
            if not asyncio.current_task().cancelling():
                check_background_rpc_error(client)
            raise
        except Exception as exc:
            print('SCANGOAL_BACKFILL_PENDING', *error_fields(exc))
            if is_definitive_auth_error(exc):
                raise
            check_background_rpc_error(client)
    print('SCANGOAL_READ_OK read', len(rows), 'new_versions', added)
    return 0


def main():
    try:
        sys.exit(asyncio.run(collect()))
    except (Exception, asyncio.CancelledError) as exc:
        print('SCANGOAL_READ_FAILED', *error_fields(exc))
        sys.exit(AUTHORIZATION_EXIT if is_definitive_auth_error(exc) else 1)


if __name__ == '__main__':
    main()

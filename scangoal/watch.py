"""Leitura rápida entre recolhas: de 2 em 2 minutos, durante o tempo em que a execução antes ficava parada.

Para que os alertas de sinais ao vivo cheguem em cerca de 2 minutos em vez de 10. Só leitura, como o reader.
- Uma só ligação ao Telegram por execução, mantida aberta entre leituras (não entra e sai a cada leitura).
- Cada leitura pede só as últimas FAST_RECENT mensagens; a recolha completa (reader) continua a reler as 400.
- Quando há mensagens novas, refaz o relatório e envia os avisos. Sem novidades, não faz nada.
- Nunca falha a execução: perante um erro (incluindo um pedido do Telegram para esperar) pára de ler e limita-se a
  aguardar o resto do tempo, para a cadência das recolhas não mudar. Duas leituras falhadas seguidas: pára.
"""
import asyncio, json, os, pathlib, shutil, sys, time
from datetime import datetime, timezone

from . import reader
from .store import append_versions, data_dir

WATCH_SECONDS = int(os.environ.get('WATCH_SECONDS', '480'))
INTERVAL = int(os.environ.get('WATCH_INTERVAL', '120'))
FAST_RECENT = int(os.environ.get('WATCH_RECENT', '40'))


async def rounds(read, on_new, sleep=asyncio.sleep, clock=time.monotonic, total=None, every=None):
    """Chama read() a cada `every` segundos até `total`; on_new() quando read() devolve linhas novas. -> leituras feitas."""
    total, every = WATCH_SECONDS if total is None else total, INTERVAL if every is None else every
    start, done, fails, k = clock(), 0, 0, 1
    while k * every <= total and fails < 2:
        wait = start + k * every - clock()
        if wait > 0:
            await sleep(wait)
        k += 1
        try:
            added = await read()
        except Exception as exc:      # o tipo basta; nunca imprimir segredos
            fails += 1
            print('WATCH_READ_FAILED', type(exc).__name__)
            continue
        fails, done = 0, done + 1
        if added:
            try:
                on_new()
            except Exception as exc:
                print('WATCH_REPORT_FAILED', type(exc).__name__)
    return done


def refresh():
    """Relatório e avisos, como os passos da recolha completa."""
    from . import notify, report
    docs = pathlib.Path('docs')
    rep = report.build(docs / 'bets.csv' if docs.is_dir() else None, register=True)
    out = data_dir() / 'report.json'
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    if docs.is_dir():
        shutil.copyfile(out, docs / 'report.json')
    notify.run()


async def watch():
    client = reader.make_client()
    state = {'entity': None}

    async def read():
        if not client.is_connected():
            await asyncio.wait_for(client.connect(), 30)
        if state['entity'] is None:
            if not await client.is_user_authorized():
                raise RuntimeError('session not authorized')
            state['entity'] = await asyncio.wait_for(client.get_entity(os.environ['TG_SCANGOAL'].strip()), 30)
        now = reader.iso(datetime.now(timezone.utc))
        rows, _, _ = await asyncio.wait_for(reader.fetch(client, state['entity'], FAST_RECENT, now), 45)
        added = append_versions(rows)
        path = data_dir() / 'state.json'
        try:
            st = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            st = {}
        st.update(last_run_utc=now, last_fast_read_utc=now)
        path.write_text(json.dumps(st) + '\n', encoding='utf-8')
        print('WATCH_READ_OK read', len(rows), 'new_versions', added)
        return added

    try:
        return await rounds(read, refresh)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main():
    start = time.monotonic()
    if all(os.environ.get(k, '').strip() for k in ('TG_API_ID', 'TG_API_HASH', 'TG_SESSION', 'TG_SCANGOAL')):
        try:
            print('WATCH_DONE reads', asyncio.run(watch()))
        except Exception as exc:
            print('WATCH_FAILED', type(exc).__name__)
    else:
        print('WATCH_NOT_CONFIGURED')
    time.sleep(max(0.0, WATCH_SECONDS - (time.monotonic() - start)))   # a cadência das recolhas fica igual, com ou sem leituras
    sys.exit(0)


if __name__ == '__main__':
    main()

"""Leitura rápida entre recolhas: de 2 em 2 minutos, durante o tempo em que a execução antes ficava parada.

Para que os alertas de sinais ao vivo cheguem em cerca de 2 minutos em vez de 10. Só leitura, como o reader.
- Uma só ligação ao Telegram por execução, mantida aberta entre leituras (não entra e sai a cada leitura).
- Cada leitura pede só as últimas FAST_RECENT mensagens; a recolha completa (reader) continua a reler as 400.
- Quando há mensagens novas, refaz o relatório e envia os avisos. Sem novidades, não faz nada.
- Nunca falha a execução: perante um erro (incluindo um pedido do Telegram para esperar) pára de ler e limita-se a
  aguardar o resto do tempo, para a cadência das recolhas não mudar. Duas leituras falhadas seguidas: pára.
- Só depois de aguardar o tempo todo: código de saída FAILED_EXIT se parou por falhas seguidas, se não arrancou, ou se o
  relatório ou os avisos falharam (o workflow tolera-o com continue-on-error; uma falha isolada recuperada não conta).
"""
import asyncio, json, os, pathlib, shutil, sys, time
from datetime import datetime, timezone

from . import reader
from .store import append_versions, data_dir

WATCH_SECONDS = int(os.environ.get('WATCH_SECONDS', '480'))
INTERVAL = int(os.environ.get('WATCH_INTERVAL', '120'))
FAST_RECENT = int(os.environ.get('WATCH_RECENT', '40'))
FAILED_EXIT = 3


class WatchStopped(RuntimeError):
    """As leituras rápidas pararam (falhas seguidas) ou o relatório/avisos falharam; a espera e a cadência mantêm-se."""


async def rounds(read, on_new, sleep=asyncio.sleep, clock=time.monotonic, total=None, every=None, problems=None):
    """Chama read() a cada `every` segundos até `total`; on_new() quando read() devolve linhas novas. -> leituras feitas.
    problems (lista, opcional): recebe 'reads_stopped' e/ou 'report_failed' para o código de saída."""
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
                if problems is not None:
                    problems.append('report_failed')
    if fails >= 2 and problems is not None:
        problems.append('reads_stopped')
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
    notify.run_checked()          # entregas falhadas/incertas/retiradas -> WATCH_REPORT_FAILED (nunca reenvia)
    # Se a publicação remota falhar, rounds() regista report_failed;
    # o workflow preserva a recolha com continue-on-error e não envia o aviso.
    from . import stake_alerts
    stake_alerts.deliver(report.LAST.get('signals') or [])


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

    problems = []
    try:
        done = await rounds(read, refresh, problems=problems)
        if problems:
            print('WATCH_PROBLEMS', ' '.join(sorted(set(problems))))
            raise WatchStopped(done)
        return done
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main():
    start, code = time.monotonic(), 0
    if all(os.environ.get(k, '').strip() for k in ('TG_API_ID', 'TG_API_HASH', 'TG_SESSION', 'TG_SCANGOAL')):
        try:
            print('WATCH_DONE reads', asyncio.run(watch()))
        except Exception as exc:
            print('WATCH_FAILED', type(exc).__name__)
            code = FAILED_EXIT
    else:
        print('WATCH_NOT_CONFIGURED')
    time.sleep(max(0.0, WATCH_SECONDS - (time.monotonic() - start)))   # a cadência das recolhas fica igual, com ou sem leituras
    sys.exit(code)


if __name__ == '__main__':
    main()

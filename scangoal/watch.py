"""Leitura rápida entre recolhas: de 2 em 2 minutos, durante o tempo em que a execução antes ficava parada.

Para que os alertas de sinais ao vivo cheguem em cerca de 2 minutos em vez de 10. Só leitura, como o reader.
- Uma só ligação ao Telegram por execução, mantida aberta entre leituras (não entra e sai a cada leitura).
- Cada leitura pede só as últimas FAST_RECENT mensagens; a recolha completa (reader) continua a reler as 400.
- Quando há mensagens novas, refaz o relatório e envia os avisos. Sem novidades, não faz nada.
- Pára as leituras perante perda definitiva de autorização ou duas falhas consecutivas.
- Mantém a espera até ao fim da janela e sai com FAILED_EXIT perante essas falhas, ou perante erro de arranque,
  relatório ou avisos. O workflow tolera esse código e continua a cadeia.
"""
import asyncio, json, os, pathlib, shutil, sys, time
from datetime import datetime, timezone

from . import reader
from .store import append_versions, data_dir
from .telegram_diagnostics import check_background_rpc_error, error_fields, is_definitive_auth_error

WATCH_SECONDS = int(os.environ.get('WATCH_SECONDS', '480'))
INTERVAL = int(os.environ.get('WATCH_INTERVAL', '120'))
FAST_RECENT = int(os.environ.get('WATCH_RECENT', '40'))
FAILED_EXIT = 3
# The workflow kills this step at 540 s. A refresh (report + notices) starts only when its measured duration still fits
# before HARD_SECONDS; otherwise the read data is published by the next step and the next collection reports it.
# A refresh killed mid-way leaves a pushed intent unsent (`uncertain`): never a duplicate, but a lost notice.
HARD_SECONDS = int(os.environ.get('WATCH_HARD_SECONDS', '525'))
REFRESH_ESTIMATE = int(os.environ.get('WATCH_REFRESH_ESTIMATE', '240'))


class WatchStopped(RuntimeError):
    """As leituras rápidas pararam (falhas seguidas) ou o relatório/avisos falharam; a espera e a cadência mantêm-se."""


async def rounds(read, on_new, sleep=asyncio.sleep, clock=time.monotonic, total=None, every=None, problems=None,
                 hard=None, estimate=REFRESH_ESTIMATE):
    """Chama read() a cada `every` segundos até `total`; on_new() quando read() devolve linhas novas. -> leituras feitas.
    problems (lista, opcional): recebe 'reads_stopped' e/ou 'report_failed' para o código de saída."""
    total, every = WATCH_SECONDS if total is None else total, INTERVAL if every is None else every
    start, done, fails, k = clock(), 0, 0, 1
    refreshed = pending = False
    failures = 0
    longest = estimate
    authorization_error = None

    def fits():
        if hard is None or clock() - start + longest <= hard:
            return True
        print('WATCH_REFRESH_DEFERRED')
        return False

    def update():
        nonlocal refreshed, pending, failures, longest
        if not fits():
            return
        began = clock()
        try:
            on_new()
        except Exception as exc:
            pending = True
            failures += 1
            print('WATCH_REPORT_FAILED', *error_fields(exc))
        else:
            refreshed, pending, failures = True, False, 0
            print('WATCH_REPORT_COMPLETED')
        finally:
            longest = max(longest, clock() - began)

    while k * every <= total and fails < 2:
        wait = start + k * every - clock()
        if wait > 0:
            await sleep(wait)
        k += 1
        if pending and failures < 2:
            update()
        try:
            added = await read()
        except Exception as exc:
            fails += 1
            print('WATCH_READ_FAILED', *error_fields(exc))
            if is_definitive_auth_error(exc):
                authorization_error = exc
                break
            continue
        fails, done = 0, done + 1
        if added and failures < 2:
            update()
    if fails >= 2 and problems is not None:
        problems.append('reads_stopped')
    if done and (not refreshed or pending) and failures < 2:
        update()
    if pending and problems is not None:
        problems.append('report_failed')
    if authorization_error is not None:
        raise authorization_error
    return done


def refresh(publisher=None):
    """Relatório e avisos, como os passos da recolha completa."""
    from . import notify, report, stake_alerts
    if publisher is not None:
        publisher.resume()
    docs = pathlib.Path('docs')
    rep = report.build(docs / 'bets.csv' if docs.is_dir() else None, register=True)
    out = data_dir() / 'report.json'
    reader.write_json(out, rep)
    if docs.is_dir():
        target = docs / 'report.json'
        temporary = target.with_suffix('.tmp')
        shutil.copyfile(out, temporary)
        temporary.replace(target)
    if publisher is not None:
        publisher.publish()
    try:
        notify.run_checked()
        stake_alerts.deliver(report.LAST.get('signals') or [])
    finally:
        if publisher is not None:
            publisher.publish()


async def watch():
    from .publication import Publisher, PublicationError

    publisher = Publisher() if os.environ.get('NOTIFY_DURABLE') == 'git' else None
    if publisher is not None:
        try:
            publisher.resume()
        except PublicationError as exc:
            # Reads stay independent of publication. Every refresh retries the publication first and sends nothing
            # while it fails, so a pending transaction can never be overtaken by a notice.
            print('WATCH_PUBLICATION_PENDING', *error_fields(exc))
    client = reader.make_client()
    state = {'entity': None}

    async def read():
        check_background_rpc_error(client)
        try:
            if not client.is_connected():
                await asyncio.wait_for(client.connect(), 30)
            if state['entity'] is None:
                state['entity'] = await asyncio.wait_for(client.get_entity(os.environ['TG_SCANGOAL'].strip()), 30)
            now = reader.iso(datetime.now(timezone.utc))
            rows, _, _ = await asyncio.wait_for(reader.fetch(client, state['entity'], FAST_RECENT, now), 45)
        except (ConnectionError, asyncio.CancelledError):
            if not asyncio.current_task().cancelling():
                check_background_rpc_error(client, definitive_only=False)
            raise
        now = reader.stamp_receipt(rows)
        added = append_versions(rows)
        path = data_dir() / 'state.json'
        try:
            st = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            st = {}
        latest = dict(st, last_run_utc=now, last_fast_read_utc=now)
        reader.write_collection_state(path, st, latest)
        print('WATCH_READ_OK read', len(rows), 'new_versions', added)
        return added

    problems = []
    try:
        done = await rounds(read, lambda: refresh(publisher), problems=problems, hard=HARD_SECONDS)
        check_background_rpc_error(client)
        if problems:
            print('WATCH_PROBLEMS', ' '.join(sorted(set(problems))))
            raise WatchStopped(done)
        return done
    except Exception as exc:
        if is_definitive_auth_error(exc):
            print('WATCH_PROBLEMS authorization_lost')
        raise
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
            print('WATCH_FAILED', *error_fields(exc))
            code = FAILED_EXIT
    else:
        print('WATCH_NOT_CONFIGURED')
    time.sleep(max(0.0, WATCH_SECONDS - (time.monotonic() - start)))   # a cadência das recolhas fica igual, com ou sem leituras
    sys.exit(code)


if __name__ == '__main__':
    main()

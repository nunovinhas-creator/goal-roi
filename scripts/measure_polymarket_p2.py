"""P2 — custo do seguidor com um diário de dimensão real (só leitura; tudo num diretório temporário; sem rede).

Uso:
    python -m scripts.measure_polymarket_p2 [--scale 1 4 30] [--out /tmp/p2-measure.json]

Para cada escala, copia data/messages.jsonl (repetido `scale` vezes, com IDs deslocados) para um diretório temporário e
mede, em tempo real e CPU do processo:
- a abordagem anterior (first_versions() + report.load() a cada alteração do diário);
- o seguidor incremental: arranque, passagem sem alteração, 4 leituras rápidas que relêem as mesmas mensagens,
  uma mensagem nova não elegível e um sinal novo elegível (rede recusada pelo stub: mede só o custo local);
- um processo seguidor real (`python -m scangoal.polymarket_observer follow`) durante 20 s com 3 sinais novos:
  CPU do processo (evento "stop"), memória máxima e o atraso entre a escrita no diário e o registo no spool.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scangoal import polymarket_observer as po, polymarket_shadow as ps, store
from scripts.polymarket_shadow import safe_output, write_output
from scripts.rehearse_polymarket_p2 import bot_text

ROOT = Path(__file__).resolve().parents[1]


def timed(fn, repeat=3):
    walls, cpus = [], []
    for _ in range(repeat):
        wall, cpu = time.perf_counter(), time.process_time()
        fn()
        walls.append(time.perf_counter() - wall)
        cpus.append(time.process_time() - cpu)
    return {'wall_ms': round(1000 * statistics.median(walls), 2), 'cpu_ms': round(1000 * statistics.median(cpus), 2)}


def build(directory, source, scale):
    rows = [json.loads(line) for line in source.read_bytes().splitlines() if line.strip()]
    with (directory / 'messages.jsonl').open('w', encoding='utf-8') as handle:
        for k in range(scale):
            for row in rows:
                handle.write(json.dumps(dict(row, message_id=row['message_id'] + k * 10_000_000),
                                        ensure_ascii=False, sort_keys=True) + '\n')
    return rows


def new_row(mid, kickoff, eligible=True):
    now = datetime.now(timezone.utc)
    body = bot_text('Rehearsal Home', 'Rehearsal Away', 'Rehearsal League', kickoff, 1.8) if eligible else \
        'Promotional message %d' % mid
    return {'message_id': mid, 'date_utc': (now - timedelta(seconds=20)).isoformat(timespec='seconds'),
            'edit_date_utc': None, 'text': body + ('\n#%d' % mid if eligible else ''), 'buttons': None,
            'collected_at_utc': now.isoformat(), 'receipt_version': 1}


def refuse(url):
    raise ps.Refusal('network_error', 'measurement')


def measure(scale, source, tmp):
    data, spool = tmp / ('x%d' % scale) / 'data', tmp / ('x%d' % scale) / 'spool'
    data.mkdir(parents=True)
    spool.mkdir()
    rows = build(data, source, scale)
    os.environ['SCANGOAL_DATA_DIR'] = str(data)
    from scangoal import report
    path = data / 'messages.jsonl'
    out = {'scale': scale, 'journal_bytes': path.stat().st_size, 'journal_rows': len(rows) * scale}
    out['previous_per_change'] = {
        'first_versions': timed(lambda: po.first_versions(path)),
        'report_load': timed(lambda: report.load(result_observations={}, as_of=datetime.now(timezone.utc)), 2)}
    init = {}

    def start():
        init['follower'] = po.Follower(data, spool, get=refuse)

    out['incremental_startup'] = timed(start, 1)
    follower = init['follower']
    out['incremental_tick_unchanged'] = timed(follower.tick, 5)
    recent = rows[-40:]
    kickoff = datetime.now(timezone.utc) + timedelta(days=1)
    base = 10_000_000 * scale + 1
    fast = []
    for index in range(4):                                   # leituras rápidas: as mesmas 40 + 1 mensagem nova
        store.append_versions([*[dict(r, message_id=r['message_id'] + (scale - 1) * 10_000_000) for r in recent],
                               new_row(base + index, kickoff, eligible=False)])
        fast.append(timed(follower.tick, 1))
    out['incremental_tick_after_fast_read'] = {
        'wall_ms_median': statistics.median(x['wall_ms'] for x in fast),
        'cpu_ms_median': statistics.median(x['cpu_ms'] for x in fast)}
    store.append_versions([new_row(base + 99, kickoff)])
    out['incremental_tick_new_eligible_signal'] = timed(follower.tick, 1)
    out['records_written'] = len(list(spool.glob('[0-9a-f]*.json')))
    out['record_bytes'] = max((p.stat().st_size for p in spool.glob('[0-9a-f]*.json')), default=0)
    out['reads'] = {'full': follower.cursor.full_reads, 'incremental': follower.cursor.incremental_reads}
    out['measurement_process_peak_rss_mb_includes_previous_approach'] = po.peak_rss_mb()
    out['real_follower_process'] = real_follower(data, tmp / ('x%d' % scale) / 'spool-real', kickoff, base)
    shutil.rmtree(tmp / ('x%d' % scale))
    return out


def real_follower(data, spool, kickoff, base):
    """Processo verdadeiro, com o proxy recusado: só CPU, memória e atraso local (sondagem de 5 s incluída)."""
    env = dict(os.environ, SCANGOAL_DATA_DIR=str(data), GOAL_ROI_POLYMARKET_SHADOW='on',
               HTTPS_PROXY='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9')
    spool.mkdir()
    process = subprocess.Popen([sys.executable, '-B', '-m', 'scangoal.polymarket_observer',
                                'follow', '--spool', str(spool), '--seconds', '20'],
                               cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 60
    while not (spool / 'follower.pid').exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    lags = []
    for index in range(3):
        before = set(spool.glob('[0-9a-f]*.json'))
        store.append_versions([new_row(base + 200 + index, kickoff)])
        written = time.monotonic()
        while set(spool.glob('[0-9a-f]*.json')) == before and time.monotonic() - written < 15:
            time.sleep(0.02)
        lags.append(round(time.monotonic() - written, 3))
        time.sleep(1)
    process.communicate(timeout=90)
    stop = next((json.loads(p.read_text()) for p in spool.glob('run-*-stop.json')), {})
    maxrss = stop.get('peak_rss_mb')
    return {'detection_to_record_seconds': lags, 'poll_seconds': po.POLL_SECONDS,
            'process_cpu_seconds': stop.get('cpu_seconds'), 'process_wall_seconds': round(time.monotonic() - started, 1),
            'process_peak_rss_mb': maxrss, 'ticks': stop.get('ticks'), 'reads': {
                'full': stop.get('full_reads'), 'incremental': stop.get('incremental_reads')},
            'exit_code': process.returncode}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', type=int, nargs='+', default=[1, 4, 30])
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    out = safe_output(args.out) if args.out else None
    source = ROOT / 'data' / 'messages.jsonl'
    previous = os.environ.get('SCANGOAL_DATA_DIR')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = {'schema': 'goal-roi/polymarket-p2-measure/v1', 'measured_utc': po.iso(datetime.now(timezone.utc)),
                      'python': sys.version.split()[0], 'cpu_count': os.cpu_count(),
                      'source_journal_bytes': source.stat().st_size,
                      'scales': [measure(scale, source, Path(tmp)) for scale in args.scale]}
    finally:
        if previous is None:
            os.environ.pop('SCANGOAL_DATA_DIR', None)
        else:
            os.environ['SCANGOAL_DATA_DIR'] = previous
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if out:
        write_output(out, text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()

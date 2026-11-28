"""Offline stage timings, cancellation-safe reservation and real disconnected Git clones."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scangoal import authorization_alerts as alerts
from scangoal import authorization_events as events


def git(*args):
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()


WORKER = """
from pathlib import Path
import sys
from scangoal.authorization_alerts import Alerts, GitState, run
checkout, spool, sent = map(Path, sys.argv[1:])
def sender(*args):
    with sent.open('a') as output:
        output.write('called\\n')
run(spool, Alerts(GitState(checkout), 'synthetic', 'synthetic', sender))
"""


def worker(checkout, spool, sent):
    return subprocess.Popen([sys.executable, '-c', WORKER, str(checkout), str(spool), str(sent)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(child, spool):
    (spool / 'stop').touch()
    out, err = child.communicate(timeout=20)
    assert child.returncode == 0, (out, err)
    return out


def await_file(path):
    end = time.monotonic() + 10
    while not path.is_file():
        if time.monotonic() >= end:
            raise RuntimeError('local worker timed out')
        time.sleep(.01)


def emit(spool, kind, **fields):
    with patch.dict(os.environ, SCANGOAL_AUTH_ALERT_DIR=str(spool)):
        events.emit(kind, **fields)


def same_episode_parallel_gits():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        origin = root / 'bare'
        git('init', '--bare', '-q', str(origin))
        first, second = root / 'first', root / 'second'
        for checkout in (first, second):
            git('clone', '-q', str(origin), str(checkout))
        sent = root / 'sent'
        spool1, spool2 = root / 'spool1', root / 'spool2'
        spool1.mkdir()
        spool2.mkdir()
        with worker(first, spool1, sent) as p1, worker(second, spool2, sent) as p2:
            for spool in (spool1, spool2):
                await_file(spool / 'incident')
            start = events.utc_now()
            emit(spool1, 'loss', at=start, error='SessionRevokedError')
            emit(spool2, 'loss', at=start, error='SessionRevokedError')
            logs = finish(p1, spool1) + finish(p2, spool2)
        assert sent.read_text().splitlines() == ['called']
        state = alerts.GitState(second).read()[1]
        first_id = state['active']
        record = state['incidents'][first_id]
        assert len(state['incidents']) == 1 and record['status'] == 'confirmed', state
        origin_time = datetime.fromisoformat(record['detected_at'])
        def ms(stamp):
            return round((datetime.fromisoformat(stamp) - origin_time).total_seconds() * 1000, 1)
        ack = min(re.findall(r'AUTH_ALERT_RESERVED [0-9a-f]{32} (\S+)', logs))
        print(json.dumps({'incident': first_id, 'detection_ms': 0,
                          'reservation_ack_ms': ms(ack), 'send_ms': ms(record['attempted_at']),
                          'confirmed_ms': ms(record['finished_at'])}))
        assert 0 <= ms(ack) <= ms(record['attempted_at']) <= ms(record['finished_at']) < 300_000
        assert git('--git-dir', str(origin), 'for-each-ref', '--format=%(refname)') == alerts.REF

        for name in ('stopped', 'stop'):
            (spool2 / name).unlink(missing_ok=True)
        with worker(second, spool2, sent) as child:
            emit(spool2, 'recovery', incident=first_id)
            emit(spool2, 'loss', error='SessionRevokedError')
            finish(child, spool2)
        state = alerts.GitState(first).read()[1]
        assert state['active'] != first_id and state['incidents'][first_id].get('recovered_at')
        assert sent.read_text().splitlines() == ['called', 'called']
        print('CAS parallel, restart and recovery passed')


if __name__ == '__main__':
    same_episode_parallel_gits()

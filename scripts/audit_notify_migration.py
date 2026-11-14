"""Offline comparison with the approved notifier; never reads credentials or sends."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import types
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from scangoal import notify
from test_notify_migration import legacy_state, eligible_report, NOW

BASE = '5078921e9cb8b3d88524fe1a5a1913326a07bda7'

def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('data').glob('*')) if p.is_file()}

def blocked(*args, **kwargs):
    raise AssertionError('Network forbidden')

if __name__ == '__main__':
    original = hashes()
    source = subprocess.check_output(['git', 'show', f'{BASE}:scangoal/notify.py'], text=True)
    previous = types.ModuleType('scangoal._previous_notify'); previous.__package__ = 'scangoal'
    exec(compile(source, 'previous_notify.py', 'exec'), previous.__dict__)
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket, 'create_connection', blocked):
        state, rep = legacy_state(), eligible_report()
        before = previous.messages(copy.deepcopy(rep), copy.deepcopy(state), NOW)
        first = notify.messages(rep, state, NOW)
        repeat = notify.messages(rep, state, NOW)
        rep['brain']['status_list'].pop('h0')
        rep['brain']['status_list'].update(h1='forte', h2='abandonada', new='promissora')
        future = notify.messages(rep, state, NOW)
        future_repeat = notify.messages(rep, state, NOW)
    assert original == hashes()
    assert (len(before), len(first), len(repeat), len(future), len(future_repeat)) == (25, 0, 0, 4, 0)
    print(json.dumps({'baseline_commit': BASE, 'scenario': '33 promissoras -> 8; 25 regressam a observação',
                      'before_transition_notices': len(before), 'after_transition_notices': len(first),
                      'repeated_migration_notices': len(repeat), 'genuine_future_notices': future,
                      'repeated_future_notices': len(future_repeat),
                      'history_preserved': {k: state[k] == legacy_state()[k] for k in ('watch_sent','sent_total','summary_day','last_run_utc','regime','custom_setting')},
                      'previous_reference_preserved': state['migrations'][notify.STATUS_MIGRATION]['previous_statuses'] == legacy_state()['statuses'],
                      'data_sha256': original, 'historical_data_unchanged': True}, indent=2, ensure_ascii=False))

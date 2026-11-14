"""Compare PR1 against a pinned baseline, using the same read-only data and clock.

Usage: python scripts/audit_data_eligibility.py > audits/pr1-metrics.json
No report.main(), registration, notifications, remote services or writes to data/.
"""
import copy
import hashlib
import json
import socket
import subprocess
import sys
import types
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scangoal import brain, report

BASE = '0a4423983bee954beb5ed247349e85c7df47eee4'
NOW = '2026-11-14T22:00:00+00:00'
TARGETS = (
    'Stake | Número de apostas = uma',
    'Stake | Dia = fim de semana',
    'Jogo aberto | Confiança indicada pelo bot = menos de 95 % & Minuto = 31 a 60',
    'Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo',
    'Jogo aberto | Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60',
)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat(NOW).astimezone(tz)


def old_module(name):
    source = subprocess.check_output(['git', 'show', f'{BASE}:scangoal/{name}.py'], text=True)
    module = types.ModuleType(f'scangoal._baseline_{name}')
    module.__package__ = 'scangoal'
    exec(compile(source, f'{BASE}:{name}.py', 'exec'), module.__dict__)
    module.datetime = Clock
    return module


def digest():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('data').glob('*')) if p.is_file()}


def summary(rep, rows):
    return {'counts': rep['brain']['counts'], 'hypotheses': rep['brain']['hypotheses'],
            'tested': rep['brain']['tested'], 'walk_forward': {k: v for k, v in rep['brain']['walk_forward'].items() if k != 'curve'},
            'unique_bets': rep['unique_bets'], 'settled_bets': rep['settled_bets'],
            'targets': {hid: next(({k: r[k] for k in ('n', 'games', 'roi', 'status', 'prob_above')}
                                  for r in rows if r['id'] == hid), None) for hid in TARGETS}}


def blocked(*args, **kwargs):
    raise AssertionError('Network disabled for scientific audit')


if __name__ == '__main__':
    hashes = digest()
    oldbrain, oldreport = old_module('brain'), old_module('report')
    oldreport.brain = oldbrain
    registry = brain.load_registry(Path('data/hypotheses.json'))
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket, 'create_connection', blocked), patch.object(report, 'datetime', Clock), patch.object(brain, 'datetime', Clock):
        before = oldreport.build(register=False)
        oldrows, _, _ = oldbrain.evaluate(copy.deepcopy(oldreport.LAST['signals']), registry, report.MIN_SETTLED)
        after = report.build(register=False)
        rows, _, _ = brain.evaluate(copy.deepcopy(report.LAST['signals']), registry, report.MIN_SETTLED)
    assert hashes == digest(), 'Historical data changed'
    assert before['groups'] == after['groups'], 'Strict ROI regression'
    assert before['accuracy'] == after['accuracy'], 'Exploratory accuracy regression'
    print(json.dumps({'baseline_commit': BASE, 'clock_utc': NOW, 'data_sha256': hashes,
                      'before': summary(before, oldrows), 'after': summary(after, rows),
                      'eligibility': after['brain']['eligibility'],
                      'checks': {'historical_data_unchanged': True, 'strict_roi_unchanged': True, 'exploratory_accuracy_unchanged': True},
                      'interpretation': 'Historical sensitivity analysis only; no prospective validation or proof of predictive improvement.'}, indent=2, ensure_ascii=False))

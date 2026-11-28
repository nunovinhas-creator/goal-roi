"""Publish a safe worker snapshot and report it without waiting for the worker."""
import json
import os
import re
from pathlib import Path

from . import authorization_events as events

LABELS = {
    'disabled': 'Mecanismo desligado',
    'healthy': 'Mecanismo saudável sem incidente',
    'detected': 'Incidente detetado, aviso pendente',
    'reserved': 'Reserva pendente de tentativa',
    'attempting': 'Tentativa em curso',
    'confirmed': 'Entrega confirmada',
    'rejected': 'Entrega rejeitada',
    'uncertain': 'Entrega incerta',
    'abandoned': 'Tentativa anterior abandonada sem confirmação',
    'state_unavailable': 'Estado indisponível',
    'channel_unconfigured': 'Canal de aviso indisponível por falta de configuração',
    'worker_interrupted': 'Worker interrompido ou com trabalho por concluir',
}
WARNINGS = {'rejected', 'uncertain', 'abandoned', 'state_unavailable', 'worker_interrupted', 'channel_unconfigured'}
INCIDENT_ID = re.compile(r'[0-9a-f]{32}')


class Reporter:
    """Keep degraded transitions visible even if the incident recovers in this job."""

    def __init__(self, root=None):
        self.root = root
        self.warnings = set()

    def __call__(self, status, incident=None):
        if self.root is None:
            return
        if status in WARNINGS or status == 'confirmed':
            self.warnings.add((status, incident or ''))
        try:
            events.write_atomic(self.root / 'status.json',
                                {'current': [status, incident or ''], 'warnings': sorted(self.warnings)})
        except OSError:
            print('AUTH_ALERT_REPORT_FAILED', flush=True)


def summarize(root):
    if not events.enabled():
        return [('disabled', '')]
    if root is None:
        return [('worker_interrupted', '')]
    interrupted = ('worker_interrupted', '')
    try:
        (root / 'stop').touch()
        snapshot = json.loads((root / 'status.json').read_text(encoding='utf-8'))
        messages = [tuple(snapshot['current']), *map(tuple, snapshot['warnings'])]
        if any(status not in LABELS or not isinstance(incident, str)
               or (incident and not INCIDENT_ID.fullmatch(incident)) for status, incident in messages):
            raise ValueError('invalid status')
    except (OSError, KeyError, ValueError, TypeError, RecursionError):
        return [interrupted]

    try:
        pid = int((root / 'pid').read_text(encoding='ascii'))
        if pid <= 0:
            raise ValueError('invalid pid')
        # A clean stop may have completed already. A live idle worker needs no wait.
        if (root / 'stopped').exists():
            return messages
        if (root / 'exited').exists():
            return [*messages, interrupted]
        os.kill(pid, 0)
        if messages[0][0] == 'attempting' or next(root.glob('*.event'), None):
            messages.append(interrupted)
    except (OSError, ValueError, OverflowError):
        messages.append(interrupted)
    return messages


def main():
    notices = summarize(events.spool())
    summary = []
    for status, incident in dict.fromkeys(notices):
        label = LABELS[status]
        if incident:
            label += f' (incidente {incident})'
        summary.append(f'- {label}')
        prefix = '::warning title=Aviso de autorização Telegram::' if status in WARNINGS or status in {'detected', 'reserved', 'attempting'} else ''
        print(prefix + label, flush=True)
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with Path(path).open('a', encoding='utf-8') as out:
            out.write('### Aviso de autorização Telegram\n\n' + '\n'.join(summary) + '\n')


if __name__ == '__main__':
    main()

"""Local handoff from the existing authenticated reads to the alert worker."""
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def spool():
    path = os.environ.get('SCANGOAL_AUTH_ALERT_DIR')
    return Path(path) if path else None


def write_atomic(path, value):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def emit(kind, *, at=None, **fields):
    root = spool()
    if root is None:
        return False
    try:
        root.mkdir(parents=True, exist_ok=True)
        name = f'{time.time_ns():020d}-{uuid.uuid4().hex}.event'
        write_atomic(root / name, {'kind': kind, 'at': at or utc_now(), **fields})
    except OSError:
        print('AUTH_ALERT_HANDOFF_FAILED', flush=True)
        return False
    return True


def loss(error):
    if spool() is None:
        return
    # rounds may observe the same RPC exception before the session teardown reports it.
    detected_at, queued = getattr(error, '_scangoal_alert_detection', (None, False))
    if queued:
        return
    detected_at = detected_at or utc_now()
    name = type(error).__name__
    queued = emit('loss', at=detected_at, error=name if name.isascii() and name.isidentifier() else 'RPCError')
    error._scangoal_alert_detection = (detected_at, queued)


def recovery_token():
    """Capture the incident known *before* starting an authenticated RPC."""
    root = spool()
    if root is None:
        return None
    try:
        candidate = json.loads((root / 'incident').read_text(encoding='ascii'))
    except (OSError, ValueError):
        return None
    return candidate if isinstance(candidate, str) and re.fullmatch(r'[0-9a-f]{32}', candidate) else None


def recovered(token):
    if token is not None:
        emit('recovery', incident=token)

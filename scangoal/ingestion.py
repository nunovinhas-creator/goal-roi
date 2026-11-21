"""Durable Telegram receipts and evidence of interrupted source reads."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid

from . import reader, store
from .durable_journal import DurabilityError


CHECKPOINT = 'ingestion_checkpoint.json'
KEY = 'ingestion'
BACKFILL_LIMIT = 400


def read_json(path):
    return json.loads(path.read_bytes()) if path.exists() else {}


def now():
    return datetime.now(timezone.utc)


def announce(gap):
    message = ('Telegram ingestion gap %s (%s), after ID %s. Earlier edits or deleted messages may be unavailable.'
               % (gap['id'], gap['reason'], gap['after_id']))
    print('SCANGOAL_INGESTION_GAP', message, flush=True)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as out:
            out.write(message + '\n')


class Receipt:
    def __init__(self, journal, directory, kind):
        self.journal, self.directory = journal, directory
        self.revision, entry = journal.read(KEY)
        if entry is not None and entry.get('phase') not in ('receiving', 'persisted', 'confirmed'):
            raise DurabilityError('invalid ingestion phase')
        if entry is not None and entry['phase'] == 'persisted':
            self.entry = entry
            return
        checkpoint = read_json(directory / CHECKPOINT)
        state = read_json(directory / 'state.json')
        if entry is not None and entry['phase'] == 'confirmed' and entry['id'] != checkpoint.get('id'):
            raise DurabilityError('confirmed Telegram checkpoint is missing from this runner')
        _, last_id = store.load_seen(directory / 'messages.jsonl')
        lower = checkpoint.get('high_id', last_id)
        previous = checkpoint.get('received_utc') or state.get('last_run_utc')
        started = now().isoformat()
        gaps = []
        if entry is not None and entry['phase'] == 'receiving':
            gaps = [*entry['gaps'], {'id': entry['id'], 'reason': 'interrupted_read',
                                    'started_utc': entry['started_utc'], 'detected_utc': started,
                                    'after_id': entry['after_id'], 'status': 'source_history_unverified'}]
        self.entry = {'id': uuid.uuid4().hex, 'phase': 'receiving', 'kind': kind, 'started_utc': started,
                      'after_id': lower, 'previous_utc': previous, 'gaps': gaps}
        self.save(self.entry)
        for gap in gaps:
            announce(gap)

    @property
    def captured(self):
        return self.entry['phase'] == 'persisted'

    def save(self, entry):
        self.revision = self.journal.write(KEY, self.revision, entry)
        self.entry = entry

    async def receive(self, client, entity, limit):
        entry = self.entry
        lower = entry['after_id']
        rows, oldest, present = await reader.fetch(client, entity, limit, entry['started_utc'])
        gap = bool(lower and oldest and lower + 1 < oldest)
        incomplete = None
        if gap:
            backfill, cursor, ids = await reader.fetch(client, entity, BACKFILL_LIMIT, entry['started_utc'],
                                                       min_id=lower, offset_id=oldest)
            if any(not lower < mid < oldest for mid in ids):
                raise DurabilityError('Telegram backfill fell outside its recorded interval')
            rows += backfill
            present.update(ids)
            if len(ids) >= BACKFILL_LIMIT:
                incomplete = cursor
        stamp = reader.stamp_receipt(rows)
        previous = entry['previous_utc']
        delayed = previous is not None and now() - datetime.fromisoformat(previous) >= timedelta(minutes=30)
        stored, _ = store.load_seen(self.directory / 'messages.jsonl')
        missing = sorted({mid for mid, _ in stored if oldest and mid >= oldest and mid not in present})
        known_missing = set(read_json(self.directory / 'state.json').get('missing_ids', []))
        new_missing = set(missing) - known_missing
        gaps = entry['gaps'][:]
        if gap or delayed or new_missing:
            reason = 'outside_recent_window' if gap else 'delayed_read' if delayed else 'source_message_unavailable'
            gaps.append({'id': entry['id'], 'reason': reason, 'started_utc': previous or entry['started_utc'],
                         'detected_utc': stamp, 'after_id': lower, 'before_id': oldest,
                         'available_backfill': len(backfill) if gap else 0, 'uninspected_before_id': incomplete,
                         'unavailable_ids': sorted(new_missing), 'status': 'source_history_unverified'})
        self.save(dict(entry, phase='persisted', rows=rows, oldest=oldest, present=sorted(present),
                       missing=missing, high_id=max(lower, max(present, default=0)), received_utc=stamp, gaps=gaps))
        for incident in gaps:
            announce(incident)

    def apply(self):
        if not self.captured:
            raise DurabilityError('cannot apply a Telegram read without a durable receipt')
        entry = self.entry
        folder = self.directory
        marker = folder / CHECKPOINT
        checkpoint = read_json(marker)
        if checkpoint.get('id') == entry['id']:
            return 0
        if checkpoint.get('id') is not None and checkpoint['received_utc'] >= entry['received_utc']:
            raise DurabilityError('Telegram receipt predates the runner checkpoint')
        previous = read_json(folder / 'state.json')
        added = store.append_versions(entry['rows'])
        stamp = entry['received_utc']
        state = dict(previous, last_run_utc=stamp)
        if entry['kind'] == 'full':
            state.update(read=len(entry['rows']), new_versions=added, missing_ids=entry['missing'])
            reader.record_window(folder, previous, entry['after_id'], entry['oldest'], set(entry['present']), stamp)
        else:
            state['last_fast_read_utc'] = stamp
        reader.write_collection_state(folder / 'state.json', previous, state)
        incidents = {gap['id']: gap for gap in [*checkpoint.get('gaps', []), *entry['gaps']]}
        for gap in entry['gaps']:
            announce(gap)
        reader.write_json(marker, {'id': entry['id'], 'high_id': entry['high_id'], 'received_utc': stamp,
                                   'gaps': list(incidents.values())})
        print('SCANGOAL_INGESTION_PERSISTED', entry['id'], 'new_versions', added, flush=True)
        return added


def confirm(journal, checkpoint):
    revision, entry = journal.read(KEY)
    if entry is None or entry.get('phase') != 'persisted' or entry['id'] != checkpoint.get('id'):
        return
    if any(entry[key] != checkpoint.get(key) for key in ('high_id', 'received_utc')):
        raise DurabilityError('published Telegram checkpoint does not match its durable receipt')
    journal.write(KEY, revision, {key: entry[key] for key in ('id', 'high_id', 'received_utc')} | {'phase': 'confirmed'})
    print('SCANGOAL_INGESTION_CONFIRMED', entry['id'], flush=True)

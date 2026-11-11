"""Registo append-only das mensagens do bot: uma linha por VERSÃO (original e cada edição)."""
import hashlib, json, os
from pathlib import Path


def data_dir():
    return Path(os.environ.get('SCANGOAL_DATA_DIR', 'data'))


def load_seen(path):
    seen, last_id = set(), 0
    if path.exists():
        with path.open(encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    seen.add((r['message_id'], r['sha256']))
                    last_id = max(last_id, r['message_id'])
    return seen, last_id


def append_versions(rows):
    """rows: dicts com message_id, date_utc, edit_date_utc, text, collected_at_utc. Devolve nº de linhas novas."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / 'messages.jsonl'
    seen, _ = load_seen(path)
    new = []
    for r in sorted(rows, key=lambda r: r['message_id']):
        sha = hashlib.sha256(r['text'].encode('utf-8')).hexdigest()
        if (r['message_id'], sha) not in seen:
            seen.add((r['message_id'], sha))
            new.append(dict(r, sha256=sha))
    if new:
        with path.open('a', encoding='utf-8') as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
    return len(new)

"""Export Q7 from the checkout in cwd using the cutoff declared by an immutable data snapshot.

Usage: python /path/to/replay_q7.py /path/to/reference/data /tmp/q7.json
Run the same script from each checkout and pass the same data directory.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.getcwd())
from scangoal import chronology, report

folder, target = map(Path, sys.argv[1:])
os.environ['SCANGOAL_DATA_DIR'] = str(folder)
cutoff = chronology.parse_utc(json.loads((folder / 'report.json').read_bytes())['generated_at_utc'])
original, strict_rows = report.build_bets, []

def capture(*args, **kwargs):
    rows = original(*args, **kwargs)
    if kwargs.get('strict', True):
        strict_rows.extend(rows)
    return rows

keys = ('message_id', 'key', 'teams', 'league', 'date_utc', 'kickoff_utc', 'type', 'market', 'selection',
        'odds', 'ft', 'w', 'settled_by', 'result_known_utc', 'result_known_source', 'result_available_utc')
with patch.object(report, 'build_bets', capture):
    out = report.build(now=cutoff)
target.write_text(json.dumps({'as_of': cutoff.isoformat(), 'q7': out['questions']['Q7 Todas as apostas (referência)'],
                              'rows': [{k: row.get(k) for k in keys} for row in strict_rows]}, ensure_ascii=False, indent=1))

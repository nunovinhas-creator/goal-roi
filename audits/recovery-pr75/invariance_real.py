"""Cut the current report at earlier instants and compare with reports built at those instants.

Run from the repository root: python audits/recovery-pr75/invariance_real.py [hours ...]
Reads data/ only; writes nothing. Exit 1 on any difference in simulation.entries.
"""
import json, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scangoal import report  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from test_simulator_settlement_history import truncated  # noqa: E402

hours = [float(h) for h in sys.argv[1:]] or [24.0, 72.0]
final = report.build()
now = datetime.fromisoformat(final['generated_at_utc'])
later = final['simulation']['entries']
summary = {'final_cut': final['generated_at_utc'], 'entries': len(later),
           'with_history': sum('s' in e for e in later), 'payoff_revisions': final['simulation']['payoff_revisions'],
           'cuts': []}
failed = False
for h in hours:
    cut = now - timedelta(hours=h)
    earlier = report.build(as_of=cut)['simulation']['entries']
    expected = [truncated(e, cut) for e in later if datetime.fromisoformat(e['d']) <= cut]
    diffs = [(a, b) for a, b in zip(expected, earlier) if a != b]
    ok = len(expected) == len(earlier) and not diffs
    failed |= not ok
    summary['cuts'].append({'hours_before': h, 'cut': cut.isoformat(), 'entries': len(earlier),
                            'expected': len(expected), 'differences': len(diffs), 'ok': ok,
                            'first_differences': diffs[:3]})
print(json.dumps(summary, indent=1, ensure_ascii=False))
sys.exit(int(failed))

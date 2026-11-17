"""Auditoria: todos os avisos de jogo já registados em data/notify_log.jsonl, reavaliados com a regra nova (freshness) na
hora REAL em que cada um foi enviado. Mostra que os avisos válidos continuam a passar e quais eram de sinais expirados.

Fase, hora de início e primeira observação de resultado vêm dos sinais reconstruídos a partir de data/ (cópia temporária;
nunca escreve em data/). "Terminado" só conta se o resultado foi visto ANTES da hora do envio.
Uso: python3 audits/stale-notices-evidence/replay_history.py
"""
import collections, json, os, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scangoal import freshness  # noqa: E402

CODE = ('import json,sys;from datetime import datetime;from scangoal import report;'
        'report.build(None,register=False,now=datetime.fromisoformat(sys.argv[1]));'
        'json.dump([{k:b.get(k) for k in ("date_utc","teams","selection","phase","kickoff_utc","match_result_first_seen_utc","ft","type")} '
        'for b in report.LAST["signals"]],open(sys.argv[2],"w"))')


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(ROOT / 'data', tmp / 'data')
        now = datetime.now(timezone.utc).isoformat()
        subprocess.run([sys.executable, '-c', CODE, now, str(tmp / 's.json')], cwd=ROOT, env=dict(os.environ, SCANGOAL_DATA_DIR=str(tmp / 'data')), check=True)
        sigs = json.loads((tmp / 's.json').read_text())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    by = {}
    for b in sigs:
        by.setdefault((b['date_utc'], ' — '.join(b['teams'] or []), b['selection']), b)
    final = {}
    for line in (ROOT / 'data' / 'notify_log.jsonl').read_text(encoding='utf-8').splitlines():
        e = json.loads(line)
        if not e.get('key', '').startswith('game|') or e.get('reconstructed'):
            continue
        if e['event'] == 'intent':
            final[e['key']] = {'at_utc': e['at_utc'], 'kind': e.get('kind'), 'watch_sent': [x[1] for x in e.get('effects') or [] if x[0] == 'watch_sent']}
        elif e['key'] in final and e['event'] in notify_terminal():
            final[e['key']]['event'] = e['event']
    rows, tally = [], collections.Counter()
    for key, n in sorted(final.items(), key=lambda kv: kv[1]['at_utc']):
        t = datetime.fromisoformat(n['at_utc'])
        verdicts = []
        for ws in n['watch_sent']:
            date, match, sel = ws.split('|')[0], '|'.join(ws.split('|')[1:-1]), ws.split('|')[-1]
            b = by.get((date, match, sel))
            if b is None:
                verdicts.append('signal_not_found')
                continue
            seen = freshness.parse(b.get('match_result_first_seen_utc'))
            w = {'date_utc': date, 'kickoff_utc': b.get('kickoff_utc'), 'phase': b.get('phase'), 'finished': seen is not None and seen <= t}
            verdicts.append(freshness.check(w, t)[1])
        ok = any(v == 'ok' for v in verdicts)
        verdict = 'ok' if ok else (verdicts[0] if verdicts else 'no_signal_keys')
        tally[(n.get('event'), verdict)] += 1
        rows.append({'at_utc': n['at_utc'], 'event': n.get('event'), 'kind': n['kind'], 'key': key, 'new_rule': verdict,
                     'signals': n['watch_sent']})
    out = {'notices': len(rows), 'by_event_and_new_rule': {'%s / %s' % k: v for k, v in sorted(tally.items(), key=str)}, 'rows': rows}
    print(json.dumps(out, ensure_ascii=False, indent=1))


def notify_terminal():
    return ('delivered', 'failed', 'uncertain', 'blocked', 'expired', 'withdrawn')


if __name__ == '__main__':
    main()

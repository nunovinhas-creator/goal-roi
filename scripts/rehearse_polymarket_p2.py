"""Ensaio ponta a ponta da P2 com rede real e uma mensagem SINTÉTICA (nunca escrita em data/ nem no repositório).

Uso:
    python -m scripts.rehearse_polymarket_p2 --home "Arsenal" --away "Borussia Dortmund" \
        --kickoff 2026-11-24T20:00:00Z --league "UEFA Champions League" [--out /tmp/rehearsal.json]

Tudo corre num diretório temporário: um diário de mensagens novo, o processo seguidor verdadeiro
(`python -m scangoal.polymarket_observer follow`), o `ingest` e a auditoria. A mensagem é construída no formato do bot,
mas NÃO é um sinal do bot: o resultado só mede latência, identidade e leitura do livro, nunca a qualidade de um sinal.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scangoal import chronology, polymarket_observer as po, store
from scripts.polymarket_shadow import safe_output, write_output

ROOT = Path(__file__).resolve().parents[1]
MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')


def bot_text(home, away, league, kickoff, odds):
    msk = kickoff + timedelta(hours=3)                    # o bot escreve a hora de Moscovo
    head = '%d %s %d %02d:%02d' % (msk.day, MONTHS[msk.month - 1], msk.year, msk.hour, msk.minute)
    slug = '%s-vs-%s-%s' % (home.lower().replace(' ', '-'), away.lower().replace(' ', '-'), kickoff.strftime('%Y%m%d'))
    return ('📈📈 🔷 Polymarket ×2 · %s\n%s — %s · 🏆 %s\nOver 2.5 @%.2f · $5K\n'
            '🧾 3 trades · biggest $2K · market share 70%%\nhttps://scangoal.com/match/%s?lang=en'
            % (head, home, away, league, odds, slug))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--away', required=True)
    parser.add_argument('--kickoff', required=True)
    parser.add_argument('--league', default='Rehearsal League')
    parser.add_argument('--odds', type=float, default=1.80)
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    out = safe_output(args.out) if args.out else None
    kickoff = chronology.parse_utc(args.kickoff)
    if kickoff is None or kickoff <= datetime.now(timezone.utc):
        parser.error('--kickoff must be a future time with an explicit UTC offset')
    with tempfile.TemporaryDirectory() as tmp:
        data, spool = Path(tmp) / 'data', Path(tmp) / 'spool'
        data.mkdir()
        env = dict(os.environ, SCANGOAL_DATA_DIR=str(data), GOAL_ROI_POLYMARKET_SHADOW='on')
        follower = subprocess.Popen([sys.executable, '-B', '-m', 'scangoal.polymarket_observer', 'follow',
                                     '--spool', str(spool), '--seconds', '90'], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 20
            while not (spool / 'follower.pid').exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            os.environ['SCANGOAL_DATA_DIR'] = str(data)
            now = datetime.now(timezone.utc)
            row = {'message_id': 1, 'date_utc': (now - timedelta(seconds=20)).isoformat(timespec='seconds'),
                   'edit_date_utc': None, 'text': bot_text(args.home, args.away, args.league, kickoff, args.odds),
                   'buttons': None, 'collected_at_utc': now.isoformat(), 'receipt_version': 1}
            store.append_versions([row])
            appended = datetime.now(timezone.utc)
            deadline = time.monotonic() + 60
            while not list(spool.glob('[0-9a-f]*.json')) and time.monotonic() < deadline:
                time.sleep(0.2)
            po.main(['ingest', '--stop-first', '--spool', str(spool)], environ={po.FLAG: 'on'})
            log, _ = follower.communicate(timeout=30)
        finally:
            if follower.poll() is None:
                follower.kill()
        _, rows = po.journal_rows(data / po.JOURNAL)
        _, runs = po.journal_rows(data / po.RUNS_JOURNAL)
        from scangoal import report
        from scripts import polymarket_p2_audit as audit
        messages, *_ = report.load(result_observations={}, as_of=datetime.now(timezone.utc))
        result = {
            'schema': 'goal-roi/polymarket-p2-rehearsal/v1', 'synthetic_signal': True,
            'note': 'synthetic bot-format message for a real Polymarket fixture; not a ScanGoal signal',
            'message_appended_utc': po.iso(appended), 'follower_exit_code': follower.returncode,
            'follower_log': [line for line in log.splitlines() if line.startswith('POLYMARKET_SHADOW')],
            'records': rows,
            'observer_runs': runs,
            'audit': audit.audit(messages, po.first_versions(data / 'messages.jsonl'), rows,
                                 as_of=datetime.now(timezone.utc), runs=runs),
        }
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if out:
        write_output(out, text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()

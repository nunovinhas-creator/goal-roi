"""Fixture reproduzível do incidente de 17/11/2026 (avisos "A seguir" de jogos antigos), a partir do histórico git.

- tests/fixtures/incident_2026_11_17/report.json: data/report.json publicado pela recolha das 12:58 (commit 66d7fcb, gerado
  às 12:58:11 UTC, o mesmo relatório que o notificador leu às 12:58:31), reduzido aos campos que o notificador lê;
- tests/fixtures/incident_2026_11_17/state.json: data/notify_state.json imediatamente antes do primeiro envio (4fdb353^);
- tests/fixtures/incident_2026_11_17/SOURCE.json: commits de origem e os 12 avisos de jogo efetivamente registados em
  data/notify_log.jsonl entre 12:58 e 13:08 (chave, evento final, hora).

Só lê o git (nunca altera data/). Uso, na raiz do repositório: python3 audits/stale-notices-evidence/make_fixture.py
"""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'audits' / 'pr5-evidence'))
from make_fixtures import trim  # noqa: E402  (mesmo recorte de campos da PR 5)

OUT = ROOT / 'tests' / 'fixtures' / 'incident_2026_11_17'
REPORT_COMMIT = '66d7fcb'        # data: collect 12:58:42 (relatório da recolha cujo notificador correu às 12:58:31)
FIRST_INTENT = '4fdb353'         # notify: intent 12:58:31 (o primeiro aviso antigo)
LOG_COMMIT = '18488a2'           # data: collect 13:07:22 (registo com os três lotes)
RUNS = ('2026-11-17T12:58:31+00:00', '2026-11-17T13:01:06+00:00', '2026-11-17T13:07:16+00:00')


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rep = json.loads(git('show', '%s:data/report.json' % REPORT_COMMIT))
    (OUT / 'report.json').write_text(json.dumps(trim(rep), ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    (OUT / 'state.json').write_bytes(git('show', '%s^:data/notify_state.json' % FIRST_INTENT))
    final = {}
    for line in git('show', '%s:data/notify_log.jsonl' % LOG_COMMIT).decode('utf-8').splitlines():
        e = json.loads(line)
        if e.get('key', '').startswith('game|') and RUNS[0] <= e.get('at_utc', '') <= '2026-11-17T13:08:00+00:00' and e['event'] != 'pending':
            final[e['key']] = {'event': e['event'], 'at_utc': e['at_utc'],
                               'watch_sent': [x[1] for x in e.get('effects') or [] if x[0] == 'watch_sent'] or final.get(e['key'], {}).get('watch_sent')}
    meta = {'report_commit': git('rev-parse', REPORT_COMMIT).decode().strip(), 'state_commit': git('rev-parse', FIRST_INTENT + '^').decode().strip(),
            'log_commit': git('rev-parse', LOG_COMMIT).decode().strip(), 'report_generated_at_utc': rep['generated_at_utc'],
            'runs_utc': list(RUNS), 'notices': final}
    (OUT / 'SOURCE.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in meta.items() if k != 'notices'}, indent=1), len(final), 'notices')


if __name__ == '__main__':
    main()

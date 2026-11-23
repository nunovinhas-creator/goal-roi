"""P1 — observação sombra, só leitura, das cotações Polymarket Over 2,5 pré-jogo.

Uso (raiz do repositório):
    python -m scripts.polymarket_shadow --upcoming            # todos os candidatos com início futuro
    python -m scripts.polymarket_shadow --match-id ID [...]   # encontros escolhidos
    python -m scripts.polymarket_shadow --upcoming --out /tmp/obs.json

Lê data/messages.jsonl e data/team_aliases.json; nunca escreve em data/ nem em docs/. Só faz GET a
gamma-api.polymarket.com e clob.polymarket.com, sem credenciais. Não envia ordens, Telegram nem altera alertas,
estratégias, certificação ou histórico. Não está ligado a nenhum workflow.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scangoal import chronology, polymarket_shadow, report
from scangoal.eligibility import game_identity
from scangoal.store import data_dir

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ('data', 'docs', '.github', 'audits/strategy-lab', 'audits/opportunity-lab')


def first_signals(messages, now, wanted=None, *, upcoming_only=True):
    """Primeiro sinal no âmbito (por data de envio) de cada encontro (por omissão, só com início futuro)."""
    first = {}
    for m in messages:
        if not polymarket_shadow.in_scope(m):
            continue
        mid = game_identity(m)
        kickoff = chronology.parse_utc(m.get('kickoff_utc'))
        sent = chronology.parse_utc(m.get('date_utc'))
        if (not mid or kickoff is None or sent is None or (upcoming_only and kickoff <= now)
                or (wanted and mid not in wanted)):
            continue
        if mid not in first or sent < chronology.parse_utc(first[mid]['date_utc']):
            first[mid] = m
    return [first[k] for k in sorted(first, key=lambda k: first[k]['kickoff_utc'])]


def safe_output(path):
    target = Path(path).resolve()
    for name in PROTECTED:
        protected = (ROOT / name).resolve()
        if target == protected or protected in target.parents:
            raise SystemExit('refusing to write inside %s/' % name)
    if target == data_dir().resolve() or data_dir().resolve() in target.parents:
        raise SystemExit('refusing to write inside the data directory')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--upcoming', action='store_true')
    group.add_argument('--match-id', action='append')
    group.add_argument('--coverage', action='store_true',
                       help='identity/contract coverage of every in-scope match; reads no prices')
    parser.add_argument('--max-matches', type=int, default=5)
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    out = safe_output(args.out) if args.out else None
    now = datetime.now(timezone.utc)
    messages, *_ = report.load(result_observations={}, as_of=now)
    try:
        aliases = json.loads((data_dir() / 'team_aliases.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        aliases = {}
    aliases = {k: v for k, v in aliases.items() if isinstance(v, list)}
    if args.coverage:
        signals = first_signals(messages, now, upcoming_only=False)
        rows = [polymarket_shadow.coverage(s, aliases=aliases) for s in signals]
        counts = {}
        for row in rows:
            counts[row['status']] = counts.get(row['status'], 0) + 1
        result = {'schema': 'goal-roi/polymarket-shadow-coverage/v1', 'run_started_utc': polymarket_shadow.iso(now),
                  'scope': 'POLYMARKET pre Asian Total Over 2.5 match_goals regulation, all matches',
                  'matches': len(rows), 'counts': counts, 'prices_read': False, 'rows': rows}
    else:
        signals = first_signals(messages, now, set(args.match_id or ()))[:max(0, args.max_matches)]
        result = {'schema': 'goal-roi/polymarket-shadow-run/v1', 'run_started_utc': polymarket_shadow.iso(now),
                  'scope': 'POLYMARKET pre Asian Total Over 2.5 match_goals regulation, kickoff in the future',
                  'candidates': len(signals), 'orders_sent': 0,
                  'observations': [polymarket_shadow.observe(s, aliases=aliases) for s in signals]}
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True)
    if out:
        out.write_text(text + '\n', encoding='utf-8')
    else:
        sys.stdout.write(text + '\n')


if __name__ == '__main__':
    main()

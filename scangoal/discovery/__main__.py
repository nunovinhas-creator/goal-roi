"""Offline CLI for autonomous temporal discovery and prospective evidence."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .. import chronology, event_audit
from . import engine, ledger, miner, researcher, study
from .replay import Replay

REPOSITORY = Path(__file__).resolve().parents[2]


def _summary(report):
    rows = ['# Investigação temporal experimental', '', f"Corte UTC · {report['as_of_utc']}",
            'Preços anunciados pelo bot · execução real por demonstrar', '']
    rows.extend(['| Hipótese | Estado | Jogos caso / controlo | Sem preço caso / controlo | Pares com preço | Diferença teórica pareada |',
                 '| --- | --- | ---: | ---: | ---: | ---: |'])
    for r in report.get('investigations', [])[:12]:
        record = r.get('historical', r.get('evidence', {}))
        decisions, matched = record.get('decisions', {}), record.get('matched', {})
        delta = matched.get('average_pair_difference')
        result = '—' if delta is None else f'{delta:+.2%}'
        missing = [decisions.get(name, 0) - record.get('arms', {}).get(name, {}).get('price_available', 0)
                   for name in ('case', 'control')]
        reason = r.get('reason') or r.get('registration') or ''
        rows.append(f"| {r['id'][:16]} | {r['status']} · {reason} | {decisions.get('case', 0)} / {decisions.get('control', 0)} "
                    f"| {missing[0]} / {missing[1]} | {matched.get('priced_pairs', 0)} | {result} |")
    for r in report.get('investigations', [])[:3]:
        steps = [' & '.join(f"{a['field']} {a['op']} {a['value']}" for a in step) or 'qualquer evento ligado'
                 for step in r['rule']['steps']]
        relations = [f"{a['from'] + 1} → {a['to'] + 1} {a['field']} {a['op']} {a['value']}"
                     for a in r['rule']['relations']]
        rows.extend(['', f"## {r['id'][:16]}", ' → '.join(steps),
                     ' · '.join(relations), r.get('mechanism') or '', r.get('refutation') or ''])
    rows.extend(['', study.VERDICT])
    return '\n'.join(rows)


def _path(directory, inputs, *, write=False):
    resolved = directory.resolve()
    protected = [inputs.resolve(), *(REPOSITORY / name for name in
                  ('.git', '.github', 'data', 'docs', 'ops', 'scangoal', 'scripts', 'tests'))]
    overlaps = any(resolved.is_relative_to(path) or path.is_relative_to(resolved) for path in protected)
    # Any location inside the working tree could be committed with production files.
    if write and (REPOSITORY.is_relative_to(resolved) or resolved.is_relative_to(REPOSITORY) or overlaps):
        raise ValueError('use a separate experimental registry directory')
    return resolved


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('command', choices=('discover', 'evaluate', 'registry', 'prompt'))
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--as-of', required=True)
    cli.add_argument('--registry', type=Path, help='separate experimental batch directory')
    cli.add_argument('--wave', type=int, help='evaluate one immutable publication family')
    cli.add_argument('--receipts', type=Path, help='offline RFC 3161 receipts for published batches')
    cli.add_argument('--expect-head', help='fail if a retained ledger checkpoint is missing')
    cli.add_argument('--commit', action='store_true', help='atomically register candidates from discovery')
    cli.add_argument('--format', choices=('json', 'summary'), default='json')
    choice = cli.add_mutually_exclusive_group()
    choice.add_argument('--llm-model', type=Path, help='locally installed GGUF model for llama-completion')
    choice.add_argument('--llm-reply', type=Path, help='replay a captured JSON model response')
    cli.add_argument('--work', type=int, default=miner.MAX_REPLAY_WORK)
    cli.add_argument('--emissions', type=int, default=miner.MAX_EMISSIONS)
    args = cli.parse_args(argv)
    as_of = chronology.parse_utc(args.as_of)
    if as_of is None or as_of > datetime.now(timezone.utc):
        cli.error('--as-of must have an explicit timezone and cannot be in the future')
    if (args.commit and (args.command != 'discover' or args.registry is None)
            or args.command in ('registry', 'evaluate') and args.registry is None
            or args.expect_head and args.registry is None
            or args.wave is not None and (args.command != 'evaluate' or args.wave < 1)
            or (args.llm_model or args.llm_reply) and args.command != 'discover'):
        cli.error('invalid command, publication, or model option combination')
    if not (1 <= args.work <= miner.MAX_REPLAY_WORK and 1 <= args.emissions <= miner.MAX_EMISSIONS):
        cli.error('search budgets must be positive and within the documented limits')

    try:
        location = _path(args.registry, args.data_dir, write=args.commit) if args.registry else None
        batches = ledger.read(location, expected_head=args.expect_head) if location is not None else []
        if args.command == 'registry':
            available = ledger.visible(batches, as_of)
            report = dict(as_of_utc=as_of.isoformat(), protocol=engine.PROTOCOL, receipt_domain=ledger.DOMAIN,
                          head=ledger.digest(available[-1]) if available else None,
                          batches=[dict(content=b, sha256=ledger.digest(b)) for b in available])
        else:
            replay = Replay(event_audit.build(args.data_dir, as_of, quote_only=True))
            if args.command == 'prompt':
                memory = [r for b in ledger.visible(batches, as_of) for r in b['records']]
                print(researcher.prompt(replay, memory))
                return
            if args.command == 'evaluate':
                report = engine.evaluate(replay, batches, args.receipts, wave=args.wave)
            else:
                reply = None
                if args.llm_reply is not None:
                    with args.llm_reply.open('rb') as handle:
                        reply = handle.read(researcher.MAX_REPLY_BYTES + 1)
                report, records = engine.discover(replay, batches, model=args.llm_model, reply=reply,
                                                  work=args.work, emissions=args.emissions)
                if args.commit:
                    report['publication'] = ledger.append(location, discovery_as_of=as_of,
                        manifest=report['input_sha256'], snapshot_sha256=report['snapshot_sha256'], records=records,
                        investigation=dict(protocol_sha256=report['protocol_sha256'], interpreter_sha256=report['interpreter_sha256'],
                                           work=report['work'], model=report['model']))
        if args.format == 'summary' and args.command != 'registry':
            print(_summary(report))
        else:
            print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        cli.error(str(exc))


if __name__ == '__main__':
    main()

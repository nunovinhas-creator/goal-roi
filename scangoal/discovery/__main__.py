"""Offline CLI for autonomous temporal discovery and prospective evidence."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .. import chronology, event_audit
from . import dsl_v2, engine, engine_v2, ledger, miner, miner_v2, quarantine, researcher, study
from .replay import Replay
from .v3 import engine as engine_v3, registry as registry_v3

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


def _summary_v2(report):
    lines = ['# Investigação temporal v2', '', f"Corte UTC · {report['as_of_utc']}", '',
             '| Hipótese | Estado e motivo | Fechos de sequência | Controlos | Dias em sobreposição | Diferença diária teórica |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in report.get('investigations', [])[:12]:
        evidence = row.get('evidence', row.get('historical', {}))
        counts = evidence.get('overlap_counts', {})
        difference = evidence.get('mean_day_difference')
        output = '—' if difference is None else f'{difference:+.2%}'
        lines.append(f"| {row['id'][:16]} | {row['status']} · {row['reason']} | {counts.get('case', 0)} "
                     f"| {counts.get('control', 0)} | {evidence.get('days', 0)} | {output} |")
    lines += ['', 'Contrastes em preços anunciados, sujeitos a sobreposição e incerteza.', '', study.VERDICT]
    return '\n'.join(lines)


def _blocked_banner(report):
    if report.get('confirmatory_claims') != 'blocked':
        return ''
    notice = report['quarantine']
    return (f"**Protocolo {notice['protocol']} em quarentena: alegações confirmatórias bloqueadas "
            f"({notice['audit']}). Histórico preservado sem alterações.**\n\n")


def _summary_v3(report):
    lines = ['# Inferência temporal v3 · conjunto de risco', '', f"Corte UTC · {report['as_of_utc']}",
             'Confirmatório' if report.get('confirmatory') else 'Exploratório (sem valor confirmatório)', '',
             '| Hipótese | Estado | Pares | Previsão Δ (p) | Odd anunciada Δ (p) | Execução |',
             '| --- | --- | ---: | ---: | ---: | --- |']

    def cell(layer):
        if not layer or layer.get('mean_observed') is None:
            return '—'
        p = layer.get('p')
        return f"{layer['mean_observed']:+.3f} ({'—' if p is None else f'{p:.4f}'})"

    for row in report.get('investigations', [])[:16]:
        evidence = row.get('evidence', {})
        found = evidence.get('layers', {})
        lines.append(f"| {row.get('hypothesis_id', row.get('id', ''))[:20]} | {row.get('status')} · "
                     f"{row.get('reason', '')} | {evidence.get('counts', {}).get('matched_pairs', 0)} "
                     f"| {cell(found.get('prediction'))} | {cell(found.get('announced'))} | não demonstrada |")
    lines += ['', 'Execução real não medida: sem odds executáveis nem registos de colocação.', '',
              engine_v3.VERDICT]
    return '\n'.join(lines)


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
    cli.add_argument('--version', type=int, choices=(1, 2, 3), default=1,
                     help='keep the legacy protocol by default; v2 and v3 are explicit')
    cli.add_argument('--evidence-dir', type=Path, help='optional immutable external archive for evaluate --version 2')
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
    cli.add_argument('--work', type=int)
    cli.add_argument('--emissions', type=int)
    args = cli.parse_args(argv)
    discovery = engine_v2 if args.version in (2, 3) else engine
    generator = miner_v2 if args.version in (2, 3) else miner
    args.work = generator.MAX_REPLAY_WORK if args.work is None else args.work
    args.emissions = generator.MAX_EMISSIONS if args.emissions is None else args.emissions
    as_of = chronology.parse_utc(args.as_of)
    if as_of is None or as_of > datetime.now(timezone.utc):
        cli.error('--as-of must have an explicit timezone and cannot be in the future')
    if (args.commit and (args.command != 'discover' or args.registry is None)
            or args.command in ('registry', 'evaluate') and args.registry is None
            or args.expect_head and args.registry is None
            or args.wave is not None and (args.command != 'evaluate' or args.wave < 1)
            or args.evidence_dir and (args.command != 'evaluate' or args.version != 2)
            or (args.llm_model or args.llm_reply) and args.command != 'discover'):
        cli.error('invalid command, publication, or model option combination')
    if not (1 <= args.work <= generator.MAX_REPLAY_WORK and 1 <= args.emissions <= generator.MAX_EMISSIONS):
        cli.error('search budgets must be positive and within the documented limits')

    try:
        location = _path(args.registry, args.data_dir, write=args.commit) if args.registry else None
        evidence = _path(args.evidence_dir, args.data_dir, write=True) if args.evidence_dir else None
        if evidence is not None and (evidence.is_relative_to(location) or location.is_relative_to(evidence)
                or args.receipts and (evidence.is_relative_to(args.receipts.resolve())
                                       or args.receipts.resolve().is_relative_to(evidence))):
            raise ValueError('proposal ledger, receipts and evidence must have separate directories')
        if args.version == 3:
            report = _run_v3(args, as_of, location)
            if args.format == 'summary' and args.command != 'registry':
                print(_summary_v3(report))
            else:
                print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))
            return
        batches = ledger.read(location, expected_head=args.expect_head) if location is not None else []
        if args.command == 'registry':
            available = ledger.visible(batches, as_of)
            report = dict(as_of_utc=as_of.isoformat(), protocol=discovery.PROTOCOL, receipt_domain=ledger.DOMAIN,
                          head=ledger.digest(available[-1]) if available else None,
                          batches=[dict(content=b, sha256=ledger.digest(b)) for b in available])
        else:
            replay = Replay(event_audit.build(args.data_dir, as_of, quote_only=True))
            if args.command == 'prompt':
                memory = [r for b in ledger.visible(batches, as_of) for r in b['records']]
                print(researcher.prompt(replay, memory, language=dsl_v2) if args.version == 2 else researcher.prompt(replay, memory))
                return
            if args.command == 'evaluate':
                report = discovery.evaluate(replay, batches, args.receipts, wave=args.wave)
                if evidence is not None:
                    digest = engine_v2.archive_evidence(evidence, report)
                    report['evidence_archive_sha256'] = digest
            else:
                reply = None
                if args.llm_reply is not None:
                    with args.llm_reply.open('rb') as handle:
                        reply = handle.read(researcher.MAX_REPLY_BYTES + 1)
                report, records = discovery.discover(replay, batches, model=args.llm_model, reply=reply,
                                                  work=args.work, emissions=args.emissions)
                if args.commit:
                    report['publication'] = ledger.append(location, discovery_as_of=as_of,
                        manifest=report['input_sha256'], snapshot_sha256=report['snapshot_sha256'], records=records,
                        investigation=dict(protocol_sha256=report['protocol_sha256'], interpreter_sha256=report['interpreter_sha256'],
                                           work=report['work'], model=report['model']))
        if args.command != 'registry':
            # v1/v2 confirmatory claims are blocked; their computed history is kept.
            report = quarantine.annotate(report)
        if args.format == 'summary' and args.command != 'registry':
            print(_blocked_banner(report) + (_summary_v2(report) if args.version == 2 else _summary(report)))
        else:
            print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        cli.error(str(exc))


def _run_v3(args, as_of, location):
    if args.expect_head or args.wave is not None or args.evidence_dir:
        raise ValueError('v3 plans do not use --expect-head, --wave or --evidence-dir')
    if args.command == 'registry':
        plans = registry_v3.visible(registry_v3.read(location), as_of)
        return dict(as_of_utc=as_of.isoformat(), protocol=engine_v3.protocol.PROTOCOL,
                    protocol_sha256=engine_v3.protocol.PROTOCOL_SHA256, receipt_domain=registry_v3.DOMAIN,
                    plans=[dict(content=p, sha256=registry_v3.digest(p)) for p in plans])
    replay = Replay(event_audit.build(args.data_dir, as_of, quote_only=True))
    if args.command == 'prompt':
        raise ValueError('v3 reuses the v2 proposal language; use prompt --version 2')
    if args.command == 'evaluate':
        return engine_v3.evaluate(replay, registry_v3.read(location), args.receipts)
    reply = None
    if args.llm_reply is not None:
        with args.llm_reply.open('rb') as handle:
            reply = handle.read(researcher.MAX_REPLY_BYTES + 1)
    mined, records = engine_v2.discover(replay, [], model=args.llm_model, reply=reply,
                                        work=args.work, emissions=args.emissions)
    report = engine_v3.discover(replay, candidates=[r for r in records if 'rule' in r])
    report['mining'] = dict(engine='miner_v2', work=mined['work'], candidates=len(records))
    if args.commit:
        report['registration'] = registry_v3.append(
            location, discovery_as_of=as_of, snapshot_sha256=replay.snapshot_sha256,
            protocol_sha256=engine_v3.protocol.PROTOCOL_SHA256, interpreter_sha256=engine_v3.INTERPRETER_SHA256,
            hypotheses=report['proposed_registration'])
    return report


if __name__ == '__main__':
    main()

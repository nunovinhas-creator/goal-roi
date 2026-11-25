"""Verify the second BSD draft against an updated, immutable production cut offline."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from replay import replay
from scangoal import opportunity_results, prospective

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parents[1]
LEDGER = prospective.LEDGER_NAME
INPUTS = ('messages.jsonl', 'details_hl.json', 'results_hl.json', 'results_bsd.json', 'state.json', 'team_aliases.json')
WORKER = """
import json
import sys
from datetime import datetime
from scangoal import prospective

def deny_network(event, _arguments):
    if event.startswith('socket.'):
        raise AssertionError('Network is disabled in this replay')

sys.addaudithook(deny_network)
added = prospective.record(datetime.fromisoformat(sys.argv[1]))
entries = prospective.load()
prospective.verify(entries)
print(json.dumps({'signals_added': added, 'summary': prospective.summary(entries)}))
"""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def entries(raw):
    return [json.loads(line) for line in raw.splitlines()]


def git_output(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args])


def run(checkout, data, now):
    env = dict(os.environ, PYTHONPATH=str(checkout), PYTHONDONTWRITEBYTECODE='1', SCANGOAL_DATA_DIR=str(data))
    return json.loads(subprocess.check_output([sys.executable, '-c', WORKER, now.isoformat()], cwd=data, env=env))


def stages(production, data, original, observed, archive, authentication, candidate_rows):
    path = data / LEDGER
    transition = {}

    def stage(label, checkout, when, kinds=None):
        before = path.read_bytes()
        result = run(checkout, data, when)
        after = path.read_bytes()
        assert after.startswith(before) and after.startswith(original)
        appended = entries(after[len(before):])
        if kinds is not None:
            assert [entry['kind'] for entry in appended] == kinds, (label, appended)
        summary = result['summary']
        transition[label] = {
            'ledger_sha256': digest(after), 'head_sha256': entries(after)[-1]['sha256'],
            'entries': len(entries(after)), 'prefix_byte_identical': True, 'chain_verified': True,
            'signals_added': result['signals_added'], 'registered_games': summary['games_registered'],
            'registered_signals': summary['signals_registered'],
            'independently_confirmed_games': summary['games_with_independent_result'],
            'conflicting_games': summary['games_with_conflicting_results'],
            'appended': [{key: entry[key] for key in ('kind', 'seq', 'sha256', 'code_manifest_schema',
                                                     'match_id', 'provider', 'ft', 'provider_record_sha256')
                          if key in entry} for entry in appended],
        }
        return appended

    stage('production_baseline', production, observed, [])
    version, = stage('activate_v2', CANDIDATE, observed + timedelta(seconds=1), ['code_version'])
    assert version['code_manifest_schema'] == 2
    assert version['code_sha256']['report.py'] == digest((CANDIDATE / 'scangoal/report.py').read_bytes())
    assert version['reference_sha256']['team_aliases.json'] == digest((data / 'team_aliases.json').read_bytes())
    stage('repeat_v2', CANDIDATE, observed + timedelta(seconds=1), [])

    # Branch the audit at v2. Exercise the retired writer before adding the new BSD archive rows.
    activated = path.read_bytes()
    reversed_version, = stage('rollback_to_v1', production, observed + timedelta(seconds=2), ['code_version'])
    assert 'code_manifest_schema' not in reversed_version
    original_version = next(row for row in reversed(entries(original)) if 'code_sha256' in row)
    assert reversed_version['code_sha256'] == original_version['code_sha256']
    stage('repeat_rollback', production, observed + timedelta(seconds=2), [])
    path.write_bytes(activated)  # Reset only the disposable copy to start the other branch of the experiment.

    bsd_path = data / 'results_bsd.json'
    authenticated_time = max(datetime.fromisoformat(row['observed_at_utc']) for row in authentication['receipts'])
    assert authenticated_time <= observed
    assert not archive['results'].keys() & candidate_rows.keys()
    for mid, row in candidate_rows.items():
        opportunity_results.remember_provider_results(archive, {mid: row}, authenticated_time)
    bsd_path.write_text(json.dumps(archive) + '\n')
    added = stage('verified_bsd_recovery', CANDIDATE, observed + timedelta(seconds=2))

    original_entries = entries(original)
    highlightly = {row['match_id']: row['ft'] for row in original_entries
                   if row['kind'] == 'outcome' and row['provider'] == 'Highlightly'}
    expected = {row['match_id'] for row in added}
    assert len(added) == len(expected) == 6
    assert all(row['kind'] == 'outcome' and row['provider'] == 'BSD'
               and row['ft'] == highlightly[row['match_id']] for row in added)
    stage('repeat_recovery', CANDIDATE, observed + timedelta(seconds=2), [])
    assert all(stage_result['signals_added'] == 0 for stage_result in transition.values())
    assert len({(value['registered_games'], value['registered_signals'], value['independently_confirmed_games'],
                 value['conflicting_games']) for value in transition.values()}) == 1
    return transition, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('production', type=Path, help='clean checkout of the updated production evidence')
    parser.add_argument('diagnostic', type=Path, help='checkout of the original 25 November diagnostic')
    parser.add_argument('lecco', type=Path, help='checkout of the first Lecco diagnostic')
    args = parser.parse_args()
    production = args.production.resolve()
    assert not git_output(production, 'status', '--porcelain')
    original = (production / 'data' / LEDGER).read_bytes()
    prospective.verify(entries(original))
    observed = datetime.fromisoformat(entries(original)[-1]['at_utc'])
    protocol_sha = digest((CANDIDATE / 'scangoal/prospective_protocol.json').read_bytes())
    assert protocol_sha == entries(original)[0]['protocol_sha256']

    public = json.loads((HERE / 'public-evidence.json').read_bytes())
    auth = json.loads((HERE / 'authenticated-evidence.json').read_bytes())
    replay_archive = {'results': {}}
    public_result = replay(args.diagnostic.resolve(), args.lecco.resolve(), public, replay_archive)
    assert public_result == json.loads((HERE / 'replay-results.json').read_bytes())
    auth_bound = {row['id'] for row in auth['cases'] if row['bound']}
    assert auth_bound == {row['bsd_id'] for row in replay_archive['results'].values()} and len(auth_bound) == 12
    auth_prospective = {row['id'] for row in auth['cases'] if row['prospective_bound']}
    assert auth_prospective == {row['event_id'] for row in public_result['cases']
                                if row['prospective'] == 'agrees_with_existing_highlightly'}

    source_files = {name: (production / 'data' / name).read_bytes() for name in INPUTS}
    manual = production / 'data/manual_results.json'
    if manual.exists():
        source_files[manual.name] = manual.read_bytes()

    with TemporaryDirectory(prefix='goal-roi-bsd-final-') as directory:
        data = Path(directory)
        (data / LEDGER).write_bytes(original)
        for name, raw in source_files.items():
            (data / name).write_bytes(raw)
        transition, version = stages(production, data, original, observed,
                                     json.loads(source_files['results_bsd.json']), auth, replay_archive['results'])
        assert all((data / name).read_bytes() == raw for name, raw in source_files.items() if name != 'results_bsd.json')
    assert (production / 'data' / LEDGER).read_bytes() == original
    assert all((production / 'data' / name).read_bytes() == raw for name, raw in source_files.items())

    print(json.dumps({
        'production_revision': git_output(production, 'rev-parse', 'HEAD').decode().strip(),
        'real_ledger_entries': len(entries(original)), 'original_ledger_sha256': digest(original),
        'original_ledger_head_sha256': entries(original)[-1]['sha256'], 'as_of_utc': observed.isoformat(),
        'frozen_protocol_sha256': protocol_sha, 'additional_provider_requests': 0,
        'archived_bsd_diagnostic': public_result['comparison'],
        'manifest_version': {key: version[key] for key in ('code_manifest_schema', 'code_sha256', 'reference_sha256')},
        'stages': transition,
    }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""PR 5 cutover rehearsal on a copy of frozen `main` data. Offline: no network, no credentials, no Telegram, no push.

Steps, all in a temporary directory (the checkout and production data are never written):
1. copy data/ of the frozen commit (from git objects) and build the report with the PR 5 code, as the collector would
   (register=True, at the instant of the frozen production report);
2. offline silent migration `--migrate-format` twice (second call must be byte-identical);
3. two notifier runs with a recording sender (as the next collector runs would do);
4. checks: zero messages; sent_total and watch_sent unchanged; the three PR 1-3 markers byte-identical; statuses equal the
   report's status_list; no delivery log line; post_cutover_check (with the PR 5 checks) passes on a synthetic git history
   before -> after migration -> after two runs; offline preview of the same state shows zero notices.

Usage (repository root): python3.11 scripts/rehearse_pr5_cutover.py <frozen_main_sha> [out.json]
Exit code 0 only if every check passes.
"""
import importlib.util, io, json, os, shutil, subprocess, sys, tempfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scangoal import notify, report  # noqa: E402

MARKERS = ('eligible_statuses_v1', 'status_rules:independent_games_hysteresis_v1', 'status_rules:chronological_eprocess_v3')


def git(*args, cwd=ROOT):
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=rehearsal', '-c', 'user.email=rehearsal@invalid',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True).stdout


def main(argv):
    frozen = argv[0]
    out_path = Path(argv[1]) if len(argv) > 1 else None
    sha = git('rev-parse', frozen).decode().strip()
    code_sha = git('rev-parse', 'HEAD').decode().strip()
    files = git('ls-tree', '--name-only', sha, 'data/').decode().split()
    pcc_spec = importlib.util.spec_from_file_location('pcc', ROOT / 'scripts' / 'post_cutover_check.py')
    pcc = importlib.util.module_from_spec(pcc_spec)
    pcc_spec.loader.exec_module(pcc)
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / 'repo'
        data = repo / 'data'
        data.mkdir(parents=True)
        git('init', '-q', cwd=repo)
        for f in files:
            (repo / f).write_bytes(git('show', '%s:%s' % (sha, f)))
        git('add', '-A', cwd=repo)
        git('commit', '-qm', 'frozen main %s' % sha, cwd=repo)
        frozen_rev = git('rev-parse', 'HEAD', cwd=repo).decode().strip()
        before_state = json.loads((data / 'notify_state.json').read_text(encoding='utf-8'))
        prod_report = json.loads((data / 'report.json').read_text(encoding='utf-8'))
        now = datetime.fromisoformat(prod_report['generated_at_utc'])
        sent, attempts = [], []
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(data)}), \
                patch.object(notify, 'send', side_effect=AssertionError('real sender used')), \
                patch('socket.socket.connect', lambda *a: attempts.append(a)):
            rep = report.build(None, register=True, now=now)
            (data / 'report.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
            git('add', '-A', cwd=repo)
            git('commit', '-qm', 'report rebuilt by PR 5 code', cwd=repo)
            built_rev = git('rev-parse', 'HEAD', cwd=repo).decode().strip()
            with redirect_stdout(io.StringIO()) as m1:
                first = notify.migrate_format_file(now)
            bytes1 = (data / 'notify_state.json').read_bytes()
            with redirect_stdout(io.StringIO()) as m2:
                second = notify.migrate_format_file(now)
            bytes2 = (data / 'notify_state.json').read_bytes()
            git('add', '-A', cwd=repo)
            git('commit', '-qm', 'migrate format', cwd=repo)
            migrated_rev = git('rev-parse', 'HEAD', cwd=repo).decode().strip()
            preview_texts, _ = notify.preview(data / 'notify_state.json', data / 'report.json', now)
            for k in range(2):
                with redirect_stdout(io.StringIO()):
                    notify.run(lambda tok, chat, text: sent.append(text), now, {'TG_BOT_TOKEN': 'rehearsal', 'TG_CHAT_ID': 'rehearsal'})
            git('add', '-A', cwd=repo)
            git('commit', '-qm', 'two runs', cwd=repo)
            after_rev = git('rev-parse', 'HEAD', cwd=repo).decode().strip()
        state = json.loads((data / 'notify_state.json').read_text(encoding='utf-8'))
        log = data / notify.LOG_NAME
        changed = sorted(f for f in files if (repo / f).exists() and (repo / f).read_bytes() != git('show', '%s:%s' % (sha, f)))
        new_files = sorted(str(p.relative_to(repo)) for p in data.iterdir() if 'data/' + p.name not in files)
        checks = {
            'migration_first_call': m1.getvalue().strip(), 'migration_second_call': m2.getvalue().strip(),
            'migration_second_call_byte_identical': bytes1 == bytes2,
            'zero_messages_first_runs': sent == [], 'zero_messages_preview': preview_texts == [],
            'no_network_attempt': attempts == [],
            'sent_total_unchanged': state.get('sent_total') == before_state.get('sent_total'),
            'watch_sent_unchanged': state.get('watch_sent') == before_state.get('watch_sent'),
            'markers_byte_identical': all(state['migrations'][k] == before_state['migrations'][k] for k in MARKERS),
            'format_marker_present': notify.FORMAT_MIGRATION in state['migrations'],
            'statuses_equal_report': state.get('statuses') == rep['brain']['status_list'],
            'announced_equal_previous_reference': state.get('announced_statuses') == before_state.get('statuses'),
            'other_fields_preserved': all(state.get(k) == v for k, v in before_state.items() if k not in ('migrations', 'last_run_utc')),
            'no_delivery_log_lines': (not log.exists()) or log.read_bytes() == b'',
            'no_deliveries': not state.get('deliveries'),
            'science_identical_to_production': (rep['brain']['status_list'] == prod_report['brain']['status_list']
                                                and rep['brain']['validation'] == prod_report['brain']['validation']),
            'certification_not_activated': rep['brain']['validation']['state'] == 'not_activated',
        }
        pcc_results = {'frozen_to_migrated': pcc.check(repo, frozen_rev, migrated_rev), 'migrated_to_after': pcc.check(repo, migrated_rev, after_rev)}
        checks['post_cutover_check_ok'] = all(r['ok'] for r in pcc_results.values())
        ok = all(v is True for k, v in checks.items() if not k.startswith('migration_')) and checks['migration_first_call'] == 'NOTIFY_FORMAT_MIGRATED' \
            and checks['migration_second_call'] == 'NOTIFY_FORMAT_UNCHANGED' and first is True and second is False
    result = {'ok': ok, 'frozen_main_sha': sha, 'code_sha': code_sha, 'instant_utc': now.isoformat(), 'checks': checks,
              'changed_data_files': changed, 'new_data_files': new_files,
              'post_cutover_check': {k: {'ok': v['ok'], 'checks': v['checks'], 'sent_in_run': v['sent_in_run']} for k, v in pcc_results.items()},
              'before': {'sent_total': before_state.get('sent_total'), 'watch_sent': len(before_state.get('watch_sent') or []),
                         'statuses': before_state.get('statuses'), 'markers': sorted(before_state.get('migrations') or {})},
              'after': {'sent_total': state.get('sent_total'), 'watch_sent': len(state.get('watch_sent') or []),
                        'markers': sorted(state.get('migrations') or {}), 'new_fields': sorted(set(state) - set(before_state))}}
    text = json.dumps(result, indent=1, ensure_ascii=False) + '\n'
    if out_path:
        out_path.write_text(text, encoding='utf-8')
    print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

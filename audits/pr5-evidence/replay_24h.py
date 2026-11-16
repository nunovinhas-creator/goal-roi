"""PR 5: offline reconstruction of the Telegram notices of the last 24 h, compared with what production really sent.

Read-only on the checkout and on git history; no network, no credentials, no Telegram.

1. build: for every commit of `main` in the window that touched data/notify_state.json (one per committed collector run),
   rebuild the report with the PR 5 code from the data of THAT commit, at the instant its production report was generated
   (`generated_at_utc`), and keep the fields the notifier reads (cache directory, 4 processes).
2. compare: start from the real notify_state.json just before the window and run the PR 5 notifier (FileStore in a temporary
   directory, recording sender) once per commit, in order, with that commit's instant. Production's real deliveries are read
   from the git history of notify_state.json (sent_total, watch_sent, statuses, summary_day, operational_regime per commit).

Usage (repository root):
    python3.11 audits/pr5-evidence/replay_24h.py build <frozen_main_sha> <cache_dir> [hours]
    python3.11 audits/pr5-evidence/replay_24h.py compare <frozen_main_sha> <cache_dir> <out.json> [hours]

Limits (stated in the result): a commit can hold several notify runs (fast reads commit once at the end); between commits the
replay sees only the committed report. Reports before the PR 3 cutover (20:58 UTC on 15/11) were produced by PR 2 rules in
production; the replay rebuilds every report with the current rules, so it re-baselines silently at its first report.
"""
import json, os, subprocess, sys, tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA = ('messages.jsonl', 'hypotheses.json', 'results_hl.json', 'results_bsd.json', 'details_hl.json', 'llm_state.json',
        'llm_notes.json', 'state.json', 'team_aliases.json', 'manual_results.json', 'certification_ledger.json')


def git(*args, check=True):
    r = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.decode())
    return r.stdout if r.returncode == 0 else None


def window(frozen, hours):
    end = datetime.fromisoformat(git('log', '-1', '--format=%cI', frozen).decode().strip())
    start = end - timedelta(hours=hours)
    revs = git('log', '--reverse', '--format=%H %cI', '--since=%s' % start.isoformat(), '--until=%s' % end.isoformat(), frozen,
               '--', 'data/notify_state.json').decode().split('\n')
    revs = [r.split() for r in revs if r.strip()]
    before = git('log', '-1', '--format=%H', '--until=%s' % start.isoformat(), frozen, '--', 'data/notify_state.json').decode().strip()
    return start, end, before, revs


def trim(rep):
    b = rep['brain']
    keep = ('evidence_scope', 'status_rules', 'eligibility', 'hypotheses', 'counts', 'validation', 'status_list', 'watch',
            'presentation', 'llm_today')
    return {'generated_at_utc': rep['generated_at_utc'], 'settled_bets': rep['settled_bets'], 'audit': rep['audit'],
            'daily': rep['daily'][-3:], 'timeline': rep['timeline'][-1:], 'operational': rep['operational'],
            'brain': {k: b.get(k) for k in keep}}


def build_one(args):
    rev, cache = args
    out = Path(cache) / ('%s.json' % rev)
    if out.exists():
        return rev, 'cached'
    old = json.loads(git('show', '%s:data/report.json' % rev))
    with tempfile.TemporaryDirectory() as d:
        for f in DATA:
            raw = git('show', '%s:data/%s' % (rev, f), check=False)
            if raw is not None:
                (Path(d) / f).write_bytes(raw)
        code = ('import json;from datetime import datetime;from scangoal import report;'
                'r=report.build(None,register=False,now=datetime.fromisoformat(%r));json.dump(r,open(%r,"w"),ensure_ascii=False)'
                % (old['generated_at_utc'], d + '/r.json'))
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=dict(os.environ, SCANGOAL_DATA_DIR=d), check=True,
                       capture_output=True)
        rep = trim(json.loads(Path(d, 'r.json').read_text(encoding='utf-8')))
    rep['_production'] = {'status_list': old['brain'].get('status_list'), 'status_rules': old['brain'].get('status_rules'),
                          'watch': [{k: w.get(k) for k in ('date_utc', 'match', 'selection')} for w in old['brain'].get('watch') or []]}
    out.write_text(json.dumps(rep, ensure_ascii=False), encoding='utf-8')
    return rev, 'built'


def build(frozen, cache, hours=24):
    Path(cache).mkdir(parents=True, exist_ok=True)
    _, _, _, revs = window(frozen, hours)
    with ProcessPoolExecutor(4) as pool:
        for k, (rev, how) in enumerate(pool.map(build_one, [(r, cache) for r, _ in revs]), 1):
            print('%d/%d %s %s' % (k, len(revs), rev[:8], how), flush=True)


def production_runs(before, revs):
    """What production delivered per commit, from the state history."""
    def state(rev):
        return json.loads(git('show', '%s:data/notify_state.json' % rev))
    prev, out = state(before), []
    for rev, t in revs:
        cur = state(rev)
        ps, cs = prev.get('statuses') or {}, cur.get('statuses') or {}
        mig = sorted(set(cur.get('migrations') or {}) - set(prev.get('migrations') or {}))
        changes = {h: [ps.get(h, 'em observação'), cs.get(h, 'em observação')] for h in sorted(set(ps) | set(cs)) if ps.get(h) != cs.get(h)}
        ro, rc = prev.get('operational_regime') or {}, cur.get('operational_regime') or {}
        out.append({'rev': rev, 'at': t, 'sent': (cur.get('sent_total') or 0) - (prev.get('sent_total') or 0),
                    'watch_added': sorted(set(cur.get('watch_sent') or []) - set(prev.get('watch_sent') or [])),
                    'status_changes': {} if mig else changes, 'silent_rebaseline': mig,
                    'summary': cur.get('summary_day') != prev.get('summary_day'),
                    'regime_changes': {k: [ro.get(k), v] for k, v in rc.items() if ro.get(k) != v and v in ('piorou', 'melhorou')}})
        prev = cur
    return out


def compare(frozen, cache, out_path, hours=24):
    from unittest.mock import patch
    from scangoal import notify
    start, end, before, revs = window(frozen, hours)
    prod = production_runs(before, revs)
    runs = []
    with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}):
        (Path(d) / 'notify_state.json').write_bytes(git('show', '%s:data/notify_state.json' % before))
        for (rev, t), p in zip(revs, prod):
            rep = json.loads((Path(cache) / ('%s.json' % rev)).read_text(encoding='utf-8'))
            production_view = rep.pop('_production')
            (Path(d) / 'report.json').write_text(json.dumps(rep, ensure_ascii=False), encoding='utf-8')
            sent = []
            now = datetime.fromisoformat(rep['generated_at_utc'])
            import io
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()):
                notify.run(lambda tok, chat, text: sent.append(text), now, {'TG_BOT_TOKEN': 'replay', 'TG_CHAT_ID': 'replay'})
            runs.append({'rev': rev, 'at': t, 'report_at': rep['generated_at_utc'],
                         'production_rules': production_view['status_rules'], 'replay_rules': rep['brain']['status_rules'],
                         'production_sent': p['sent'], 'production_watch_added': p['watch_added'], 'production_status_changes': p['status_changes'],
                         'production_silent_rebaseline': p['silent_rebaseline'], 'production_summary': p['summary'],
                         'production_regime_changes': p['regime_changes'],
                         'statuses_identical_to_production': production_view['status_list'] == rep['brain']['status_list'],
                         'replay_notices': [{'kind': kind(m), 'text': m} for m in sent]})
        final = json.loads((Path(d) / 'notify_state.json').read_text(encoding='utf-8'))
        log = notify.parse_log((Path(d) / notify.LOG_NAME).read_bytes() if (Path(d) / notify.LOG_NAME).exists() else b'')[0]
    result = analyse(start, end, before, runs, final, log)
    Path(out_path).write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(result['totals'], indent=1, ensure_ascii=False))


def kind(text):
    first = next((l for l in text.split('\n') if not l.startswith('Estado científico:')), '')
    for prefix, k in (('A seguir:', 'game_hint'), ('🟢 ALERTA', 'game_alert'), ('Hipótese', 'status'), ('✅', 'status'), ('⛔', 'status'),
                      ('Resumo de', 'summary'), ('Mudança de comportamento', 'regime'), ('Avisos do goal-roi', 'hello')):
        if first.startswith(prefix):
            return k
    return 'other'


def analyse(start, end, before, runs, final, log):
    from scangoal import notify
    prod_watch = [k for r in runs for k in r['production_watch_added']]
    prod_pairs = {}
    for k in prod_watch:
        date, match, sel = k.split('|')[0], '|'.join(k.split('|')[1:-1]), k.split('|')[-1]
        prod_pairs.setdefault((match, sel), []).append(date)
    replay_games = [n for r in runs for n in r['replay_notices'] if n['kind'] in ('game_hint', 'game_alert')]
    covered, missing = [], []
    replay_text = '\n'.join(n['text'] for n in replay_games)
    for (match, sel), dates in sorted(prod_pairs.items()):
        hit = ('A seguir: %s' % match in replay_text or 'ALERTA: %s' % match in replay_text)
        (covered if hit else missing).append({'match': match, 'selection': sel, 'production_signals': len(dates), 'first_signal': min(dates)})
    pr3 = next((r['at'] for r in runs if any('chronological' in m for m in r['production_silent_rebaseline'])), None)
    after_pr3 = [r for r in runs if pr3 and r['at'] >= pr3]
    prod_status_after = sum(len(r['production_status_changes']) for r in after_pr3)
    replay_status_after = [n for r in after_pr3 for n in r['replay_notices'] if n['kind'] == 'status']
    totals = {
        'window_utc': [start.isoformat(), end.isoformat()], 'commits_replayed': len(runs), 'state_before_window': before,
        'production_messages_sent': sum(r['production_sent'] for r in runs),
        'replay_messages_sent': sum(len(r['replay_notices']) for r in runs),
        'production_watch_signals_notified': len(prod_watch), 'production_games_notified': len(prod_pairs),
        'replay_game_notices': len(replay_games),
        'production_games_covered_by_replay': len(covered), 'production_games_not_in_replay': len(missing),
        'production_status_changes_after_pr3_cutover': prod_status_after,
        'replay_status_notices_after_pr3_cutover': len(replay_status_after),
        'production_status_changes_before_pr3_cutover_pr2_rules': sum(len(r['production_status_changes']) for r in runs if not pr3 or r['at'] < pr3),
        'production_summaries': sum(1 for r in runs if r['production_summary']),
        'replay_summaries': sum(1 for r in runs for n in r['replay_notices'] if n['kind'] == 'summary'),
        'commits_with_statuses_identical_to_production': sum(1 for r in runs if r['statuses_identical_to_production']),
        'pr3_cutover_commit_at': pr3,
        'replay_final_sent_total': final.get('sent_total'), 'replay_delivery_counts': final.get('delivery_counts'),
        'replay_log_lines': len(log),
        'replay_validated_or_strong_words': sum(1 for r in runs for n in r['replay_notices']
                                                if any(w in notify.fold(n['text']) for w in ('validad', 'confirmad', ' forte'))),
    }
    return {'totals': totals, 'missing_games': missing, 'covered_games': covered, 'runs': runs}


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'build':
        build(sys.argv[2], sys.argv[3], *(int(x) for x in sys.argv[4:5]))
    else:
        compare(sys.argv[2], sys.argv[3], sys.argv[4], *(int(x) for x in sys.argv[5:6]))

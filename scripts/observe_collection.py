"""Read production metadata and print a hypothetical decision for the scheduled run."""
import json
import os
import subprocess
from datetime import datetime, timezone

BASE = 'repos/nunovinhas-creator/goal-roi'
RUNS = BASE + '/actions/workflows/collect.yml/runs?branch=main&per_page=20'
STATE = BASE + '/contents/data/state.json?ref=main'
THRESHOLD = 30 * 60


def get(path):
    command = ['gh', 'api', '--method', 'GET', '-H', 'Accept: application/vnd.github.raw+json', path]
    return json.loads(subprocess.check_output(command, timeout=15, stderr=subprocess.DEVNULL))


def age(timestamp, now):
    recorded = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    seconds = (now - recorded).total_seconds()
    if seconds < 0:
        raise ValueError('future timestamp')
    return int(seconds)


def observe(read=get, clock=lambda: datetime.now(timezone.utc)):
    report = dict(mode='observation', observer_run_id=os.getenv('GITHUB_RUN_ID'),
                  observed_utc=clock().isoformat(), latest_run=None, last_read_utc=None,
                  read_age_seconds=None, decision='inconclusive')
    try:
        runs = read(RUNS)['workflow_runs']
        if runs:
            report['latest_run'] = {key: runs[0][key] for key in ('id', 'run_number', 'status', 'conclusion')}
        report['last_read_utc'] = read(STATE)['last_run_utc']
        now = clock()
        report['observed_utc'] = now.isoformat()
        report['read_age_seconds'] = age(report['last_read_utc'], now)
        if not runs:
            report['reason'] = 'no_collection_runs'
            return report
        reason = 'stale_read_and_runs'
        if report['read_age_seconds'] < THRESHOLD:
            reason = 'recent_persisted_read'
        else:
            for run in runs:
                if run['status'] not in ('completed', 'queued', 'pending', 'requested', 'waiting', 'in_progress'):
                    raise ValueError('unknown status')
                if age(run['updated_at'], now) < THRESHOLD:
                    reason = 'recent_run_activity'
                    break
                if run['status'] == 'in_progress':
                    jobs = read(BASE + f"/actions/runs/{int(run['id'])}/jobs?filter=latest")['jobs']
                    if not jobs:
                        raise ValueError('job metadata unavailable')
                    if any(age(j.get('completed_at') or j.get('started_at') or j['created_at'], clock()) < THRESHOLD
                           for j in jobs):
                        reason = 'recent_job_activity'
                        break
        report['reason'] = reason
        report['decision'] = 'would_request_recovery' if reason == 'stale_read_and_runs' else 'would_wait'
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report['reason'] = type(exc).__name__
    return report


if __name__ == '__main__':
    result = observe()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(int(result['decision'] == 'inconclusive'))

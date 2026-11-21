import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import observe_collection as observer


class ObserverTests(unittest.TestCase):
    def exercise(self, read_age, run_age, status='completed', job_age=None, missing=False):
        now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        stamp = lambda seconds: (now - timedelta(seconds=seconds)).isoformat()
        runs = [{'id':123, 'run_number':12, 'conclusion':None, 'status':status, 'updated_at':stamp(run_age)}]
        allowed = {observer.STATE: {} if missing else {'last_run_utc':stamp(read_age)},
                   observer.RUNS:{'workflow_runs':runs}}
        if job_age is not None:
            allowed[observer.BASE + '/actions/runs/123/jobs?filter=latest'] = {'jobs':[{'started_at':stamp(job_age)}]}

        def read(command, **kwargs):
            self.assertEqual(command[:-1], ['gh','api','--method','GET','-H','Accept: application/vnd.github.raw+json'])
            self.assertIn(command[-1], allowed)
            return json.dumps(allowed[command[-1]]).encode()

        with patch.object(observer.subprocess, 'check_output', side_effect=read):
            result = observer.observe(clock=lambda:now)
        self.assertEqual(result['latest_run']['id'], 123)
        return result

    def test_only_reads_for_healthy_stale_and_unknown_states(self):
        for args, expected in [((1799,4000),'would_wait'), ((1800,1800),'would_request_recovery'),
                               ((3600,60,'queued'),'would_wait'), ((3600,3600,'in_progress',90),'would_wait'),
                               ((3600,3600,'in_progress',3600),'would_request_recovery')]:
            with self.subTest(args=args):
                self.assertEqual(self.exercise(*args)['decision'], expected)
        self.assertEqual(self.exercise(0,0,missing=True)['decision'], 'inconclusive')

    def test_schedule_cannot_enter_restart_job(self):
        workflow = Path('.github/workflows/continuity.yml').read_text()
        restart, observe = workflow.split('\n  observe:\n')
        self.assertIn("cron: '7,22,37,52 * * * *'", restart)
        self.assertIn("if: >-\n      github.event_name == 'workflow_run' &&", restart)
        self.assertIn("vars.GOAL_ROI_RECOVERY_MODE == 'active' &&", restart)
        self.assertIn("vars.GOAL_ROI_RECOVERY_ENABLED == 'true' &&", restart)
        self.assertIn("if: github.event_name == 'schedule'", observe)
        self.assertIn('permissions:\n      actions: read\n      contents: read\n', observe)
        self.assertIn('group: scangoal-continuity-observer', observe)
        self.assertEqual([line.strip() for line in observe.splitlines() if '- run:' in line],
                         ['- run: python3 -I scripts/observe_collection.py'])
        for forbidden in ('scangoal-write','secrets.', 'dispatch', 'telegram', ': write'):
            self.assertNotIn(forbidden, observe.lower())

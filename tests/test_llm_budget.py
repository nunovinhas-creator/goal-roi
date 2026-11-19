"""Quota publication tests with isolated Git runners and a local bare remote."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from scangoal import llm, report
from scangoal.llm_budget import BudgetUnavailable, GitBudget
from test_llm_guardrails import DAY, ENV
from test_chronological_validity import sig


class DurableBudget(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', self.remote], check=True)
        self.runners = []
        self.budgets = []
        for suffix in ('one', 'two'):
            directory = self.root / suffix
            subprocess.run(['git', 'init', '-q', directory], check=True)
            subprocess.run(['git', '-C', directory, 'remote', 'add', 'origin', self.remote], check=True)
            self.runners.append(directory)
            self.budgets.append(GitBudget(ENV['CLOUDFLARE_ACCOUNT_ID'], directory))
        self.first, self.second = self.budgets

    def test_missing_journal_blocks_the_day_and_new_day_can_reserve(self):
        self.assertFalse(self.first.reserve(DAY.date(), 20, 25))
        self.assertFalse(self.second.reserve(DAY.date(), 1, 25))
        self.assertTrue(self.second.reserve(DAY.date() + timedelta(days=1), 20, 25))
        self.assertFalse(self.first.reserve(DAY.date() + timedelta(days=1), 6, 25))

    def test_interruption_after_reservation_uses_budget_on_a_fresh_runner(self):
        self.first.reserve(DAY.date() - timedelta(days=1), 100, 100)
        self.enterContext(patch.object(report, 'build', return_value={'brain': {'top': []}}))
        self.enterContext(patch.dict(report.LAST, {'research_signals': [sig('fixture', None, 2)]}))
        self.enterContext(patch.object(llm, 'DAILY_NEURONS', 10))
        self.enterContext(patch.object(llm, 'reserve', return_value=10))

        def no_request(*_):
            self.fail('Workers AI request emitted without a valid new reservation')

        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(self.runners[0] / 'data')}):
            with patch.object(llm, 'save_state', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    llm.run(no_request, DAY, ENV, budget=self.first)
        self.assertFalse((self.runners[1] / 'data' / 'llm_state.json').exists())
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(self.runners[1] / 'data')}):
            llm.run(no_request, DAY + timedelta(hours=1), ENV, budget=self.second)
        _, state = self.second.read(self.second.tip())
        self.assertEqual(list(state['holds'].values()), [100])

    def test_concurrent_writers_cannot_both_spend_the_last_capacity(self):
        self.first.reserve(DAY.date() - timedelta(days=1), 100, 100)
        barrier = threading.Barrier(2)

        def concurrent(budget):
            publish = budget.publish

            def synchronize(parent, state):
                barrier.wait(timeout=10)
                return publish(parent, state)

            with patch.object(budget, 'publish', side_effect=synchronize):
                return budget.reserve(DAY.date(), 100, 100)

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(concurrent, self.budgets))
        self.assertEqual(sorted(result), [False, True])
        _, state = self.second.read(self.second.tip())
        self.assertEqual(sum(state['holds'].values()), 100)

    def test_lost_acknowledgement_keeps_the_hold_for_later_runners(self):
        self.first.reserve(DAY.date() - timedelta(days=1), 100, 100)
        git = self.first.git

        def lose_push_reply(*args, **kwargs):
            result = git(*args, **kwargs)
            if args[0] == 'push':
                return subprocess.CompletedProcess(result.args, 1, b'', b'')
            return result

        with patch.object(self.first, 'git', side_effect=lose_push_reply):
            with self.assertRaises(BudgetUnavailable):
                self.first.reserve(DAY.date(), 100, 100)
        self.assertFalse(self.second.reserve(DAY.date(), 1, 100))

    def test_live_entry_point_has_no_local_only_fallback(self):
        with patch.object(llm, 'request') as transport:
            self.assertIsNone(llm.run(now=DAY, env=ENV))
        transport.assert_not_called()

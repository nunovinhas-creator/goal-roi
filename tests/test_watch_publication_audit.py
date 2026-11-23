import asyncio
import unittest

from scangoal import watch


class WatchPublicationAudit(unittest.TestCase):
    def test_N06_only_a_completed_refresh_clears_the_pending_work(self):
        clock, attempts, problems = [0], [], []
        versions = iter((1, 0, 0))

        async def read():
            return next(versions)

        async def sleep(seconds):
            clock[0] += seconds

        def refresh():
            attempts.append(clock[0])
            if len(attempts) == 1:
                raise RuntimeError('first publication failed')

        asyncio.run(watch.rounds(read, refresh, sleep=sleep, clock=lambda: clock[0], total=3, every=1, problems=problems))
        self.assertEqual((attempts, problems), ([1, 2], []))

    def test_a_refresh_never_starts_when_it_cannot_finish_before_the_step_timeout(self):
        """Production run 1678 (23/11/2026): the fourth fast read found a new version at 480 s, the refresh started and the
        540 s timeout killed it. A killed refresh leaves a pushed intent unsent. The guard measures the refresh and defers
        it to the next collection when it would cross the hard limit."""
        clock, started, finished, problems = [0], [], [], []

        async def read():
            return 1

        async def sleep(seconds):
            clock[0] += seconds

        def refresh():
            started.append(clock[0])
            clock[0] += 200          # report (~100 s) + notices (~100 s), as measured in production
            finished.append(clock[0])

        asyncio.run(watch.rounds(read, refresh, sleep=sleep, clock=lambda: clock[0], total=480, every=120,
                                 problems=problems, hard=525, estimate=200))
        self.assertTrue(started)
        self.assertTrue(all(end <= 525 for end in finished), finished)
        self.assertEqual(problems, [])          # a deferred refresh is not a failure

    def test_the_guard_learns_a_slower_refresh(self):
        clock, finished = [0], []

        async def read():
            return 1

        async def sleep(seconds):
            clock[0] += seconds

        def refresh():
            clock[0] += 300
            finished.append(clock[0])

        asyncio.run(watch.rounds(read, refresh, sleep=sleep, clock=lambda: clock[0], total=480, every=120,
                                 hard=525, estimate=100))
        self.assertEqual(finished, [420])       # the first measured 300 s refresh blocks a second one that cannot fit

    def test_watch_uses_the_hard_limit_below_the_workflow_timeout(self):
        import pathlib
        text = pathlib.Path('.github/workflows/collect.yml').read_text(encoding='utf-8')
        self.assertIn('timeout --signal=TERM --kill-after=10s 540s python -u -m scangoal.watch', text)
        self.assertLess(watch.HARD_SECONDS, 540)

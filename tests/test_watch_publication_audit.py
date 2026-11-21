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

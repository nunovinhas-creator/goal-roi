from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import brain, opportunity_results, scientific_commitments, scientific_receipts


T = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def at(minutes):
    return T + timedelta(minutes=minutes)


def expected_bet(revised, cutoff):
    bet = {'match_id': 'a', 'teams': ['Alpha', 'Beta'], 'league': 'Cup', 'key': '1',
           'market': '1X2', 'period': 'regulation', 'selection': '1', 'odds': 2.0, 'phase': 'pre',
           'date_utc': at(-25).isoformat(), 'signal_seen_utc': at(-24).isoformat(),
           'kickoff_utc': at(0).isoformat(), 'w': None}
    observations = [(120, (1, 0))]
    if revised:
        observations.append((150, (0, 1)))
    bet['_ft_observations'] = tuple(opportunity_results.Observation(
        'telegram_message', '1', 'regulation', ft, at(minutes), at(minutes), at(minutes), T)
        for minutes, ft in observations)
    return opportunity_results.settle_research_bet(bet, cutoff)


class ScientificSettlements(unittest.TestCase):
    def run_replay(self, folder, revised, cutoff):
        return brain.confirmation([expected_bet(revised, cutoff)], at(-60), 0.05, cutoff,
                                  hypothesis='hypothesis', receipt_dir=folder, archive=True)

    def test_archive_keeps_original_admission_and_settlement_after_correction(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)

            def witnessed(_, domain, content, *, as_of):
                stamp = at(-20) if domain == scientific_commitments.ADMISSION else (
                    datetime.fromisoformat(content['available_utc']) + timedelta(minutes=1))
                if stamp <= as_of:
                    return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, content), stamp)

            with patch.object(scientific_receipts, 'verify', witnessed):
                before = self.run_replay(folder, False, at(125))
                files = {path.name: path.read_bytes() for path in folder.glob('*.json')}
                result = self.run_replay(folder, True, at(155))
                replay = self.run_replay(folder, True, at(125))
            self.assertEqual(replay, before)
            self.assertEqual(replay['commitments'], result['commitments'])
            self.assertEqual(result['settlement_events'][0], before['settlement_events'][0])
            original, corrected = result['settlement_events']
            self.assertEqual(corrected['content']['supersedes'], original['digest'])
            self.assertEqual((original['content']['profit'], corrected['content']['profit']), (.96, -1.0))
            self.assertEqual(json.loads((folder / (corrected['digest'] + '.json')).read_bytes()),
                             json.loads(json.dumps(corrected['content'])))
            self.assertEqual({name: (folder / name).read_bytes() for name in files}, files)
            self.assertEqual(result['proof_problems'], [])

    def test_unwitnessed_correction_does_not_inherit_the_original_receipt(self):
        def witnessed(_, domain, content, *, as_of):
            if content.get('supersedes') is not None:
                return None
            stamp = at(-20 if domain == scientific_commitments.ADMISSION else 121)
            return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, content), stamp)

        with TemporaryDirectory() as temp, patch.object(scientific_receipts, 'verify', witnessed):
            result = self.run_replay(Path(temp), True, at(155))
        self.assertIsNotNone(result['settlement_events'][0]['receipt'])
        self.assertIsNone(result['settlement_events'][1]['receipt'])
        self.assertTrue(result['proof_problems'])
        self.assertIsNone(result['certified_at'])

from dataclasses import replace
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

    def test_delayed_settlement_receipt_cannot_remove_an_admission(self):
        first = expected_bet(False, at(145))
        second = dict(first, match_id='b', teams=['Gamma', 'Delta'], key='2',
                      date_utc=at(125).isoformat(), signal_seen_utc=at(126).isoformat(),
                      kickoff_utc=at(180).isoformat(), _ft_observations=())

        def witnessed(_, domain, content, *, as_of):
            if domain == scientific_commitments.ADMISSION:
                stamp = at(-20 if content['commitment']['fixture'] == 'a' else 127)
            elif domain == scientific_commitments.SETTLEMENT:
                stamp = at(150)
            else:
                return None
            if stamp <= as_of:
                return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, content), stamp)

        def replay(as_of):
            bets = [opportunity_results.settle_research_bet(bet, as_of) for bet in (first, second)]
            return brain.confirmation(bets, at(-60), 0.05, as_of, hypothesis='hypothesis')

        with patch.object(scientific_receipts, 'verify', witnessed):
            before = replay(at(145))
            after = replay(at(151))
        self.assertEqual([row['fixture'] for row in before['commitments']], ['a'])
        self.assertEqual(before['commitments'], after['commitments'])
        self.assertEqual((before['games'], before['pending'], before['e_value']), (0, 1, 1.0))
        self.assertEqual((after['games'], after['pending'], after['proof_problems']), (1, 0, []))

    def test_unwitnessed_result_closes_as_loss_at_deadline(self):
        def witnessed(_, domain, content, *, as_of):
            if domain == scientific_commitments.ADMISSION:
                return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, content), at(-20))

        deadline = at(-24) + timedelta(days=brain.OPEN_TIMEOUT_DAYS)
        with patch.object(scientific_receipts, 'verify', witnessed):
            before = brain.confirmation([expected_bet(False, deadline)], at(-60), 0.05,
                                        deadline - timedelta(seconds=1), hypothesis='hypothesis')
            expired = brain.confirmation([expected_bet(False, deadline)], at(-60), 0.05,
                                         deadline, hypothesis='hypothesis')
        self.assertEqual((before['games'], before['pending'], before['e_value']), (0, 1, 1.0))
        self.assertEqual((expired['games'], expired['pending'], expired['voided_as_loss']), (1, 0, 1))
        self.assertLess(expired['e_value'], 1.0)

    def test_receipt_matches_subsecond_provider_availability(self):
        cutoff = at(130)
        first = expected_bet(False, cutoff)
        observed = at(120).replace(microsecond=250000)
        first['_ft_observations'] = (replace(first['_ft_observations'][0], source='bsd',
                                             published_at=None, observed_at=observed, available_at=observed),)

        def witnessed(_, domain, content, *, as_of):
            stamp = at(-20) if domain == scientific_commitments.ADMISSION else at(121)
            return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, content), stamp)

        with patch.object(scientific_receipts, 'verify', witnessed):
            bet = opportunity_results.settle_research_bet(first, cutoff)
            result = brain.confirmation([bet], at(-60), 0.05, cutoff, hypothesis='hypothesis')
        self.assertEqual(result['settlement_events'][0]['content']['available_utc'], observed.isoformat())
        self.assertEqual((result['games'], result['proof_problems']), (1, []))

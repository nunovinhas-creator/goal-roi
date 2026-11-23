"""Provider archives retain competing versions and fail closed on damaged state."""

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import opportunity_results as evidence, results_bsd, results_hl


class ProviderHistory(unittest.TestCase):
    def test_report_and_research_reject_the_same_contradictory_provider_archives(self):
        kickoff = evidence.utc('2026-11-19T18:00:00Z')
        first = evidence.utc('2026-11-19T20:00:00Z')
        revised = evidence.utc('2026-11-19T21:00:00Z')
        record = {'fixture_id': 'match', 'bsd_id': 42, 'event_date': kickoff.isoformat(),
                  'home': 'Alpha', 'away': 'Beta', 'league': 'Cup', 'ft': [2, 1]}
        state = {'results': {}}
        evidence.remember_provider_results(state, {'match': record}, first)
        evidence.remember_provider_results(state, {'match': dict(record, ft=[0, 0])}, revised)
        for update in ({'ft': [2, 1]}, {'bsd_id': 58}, {'fixture_id': 'another-match'},
                       {'event_date': '2026-11-19T18:30:00Z'}):
            with self.subTest(update=update):
                archive = deepcopy(state)
                archive['ft_versions']['match'][-1]['snapshot'].update(update)
                snapshot = evidence.provider_snapshot(archive, 'bsd', revised)['match']
                observed = evidence.provider_observations(archive, 'bsd')['match']
                research = evidence.resolve(observed, kickoff=kickoff, as_of=revised)
                self.assertEqual((snapshot['ft'], research.score), ([2, 1], (2, 1)))

    def test_two_versions_in_one_batch_are_both_saved(self):
        kickoff = evidence.utc('2026-11-19T18:00:00Z')
        stamp = evidence.utc('2026-11-19T20:00:00Z')
        stored = {"ft": [2, 1], "bsd_id": 42, "home": "Alpha", "away": "Beta", "league": "Cup",
                  "event_date": "2026-11-19T18:00+00:00"}
        event = {"id": 42, "status": "finished", "event_date": stored["event_date"],
                 "home_team": "Alpha", "away_team": "Beta", "away_score": 1, "league": "Cup"}
        state = {'results': {}}
        results_bsd.capture_result_versions(state, {'match': stored},
                                            [dict(event, home_score=v) for v in (2, 1)], stamp)
        self.assertEqual([v["ft"] for v in state["ft_versions"]["match"]], [[2, 1], [1, 1]])
        self.assertTrue(evidence.resolve(evidence.provider_observations(state, 'bsd')['match'],
                                         kickoff=kickoff, as_of=stamp).conflict)

    def test_collectors_keep_unreadable_existing_archive_byte_identical(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': folder}))
        for source, collector in (('bsd', results_bsd), ('hl', results_hl)):
            with self.subTest(source=source):
                path = Path(folder, 'results_' + source + '.json')
                original = b'{"ft_versions": '
                path.write_bytes(original)
                with self.assertRaises(json.JSONDecodeError):
                    collector.run(token='mock', fetch=lambda *_: self.fail('remote fetch'), now=evidence.utc('2026-11-19T20:00:00Z'))
                self.assertEqual(path.read_bytes(), original)

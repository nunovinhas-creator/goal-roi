import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import chronology, details_hl, llm, opportunity_ranking, report, store
from scripts.opportunity_forward_audit import PROTOCOL, read_json, sourced_results


class HighlightlyDetailHistory(unittest.TestCase):
    def test_collection_restart_updates_ranking_and_preserves_the_same_earlier_cut(self):
        path = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, SCANGOAL_DATA_DIR=str(path)))
        before = chronology.parse_utc('2026-11-19T21:00:00Z')
        after = chronology.parse_utc('2026-11-19T23:00:00Z')
        activation = '2026-11-19T15:30:00Z'
        fixtures = path / 'results_hl.json'
        binding = {'id': 17, 'fixture_id': 'game-1', 'home': 'Alpha', 'away': 'Beta',
                   'league': 'Example', 'event_date': '2026-11-19T18:00:00Z', 'link': 'https://example.invalid/17'}
        fixtures.write_text(json.dumps({'not_regular_time': {'game-1': binding}}))
        (path / 'notify_state.json').write_text(json.dumps({'migrations': {'polymarket_over25_v1': {'at_utc': activation}}}))
        text = '\n'.join((
            '📈 🔷 Polymarket · 19 nov 2026 21:00', 'Alpha — Beta · Example',
            'Over 2.5 @1.85 · $9.9K', '🧾 2 trades · biggest $4.4K · market share 79%',
            'https://scangoal.com/match/game-1?lang=en',
        ))
        store.append_versions([{'message_id': 1, 'text': text, 'date_utc': '2026-11-19T15:35:00Z',
                                'collected_at_utc': '2026-11-19T15:36:00Z'}])

        def collect(stamp, goals, final):
            payload = {'homeTeam': {'name': 'Alpha'}, 'awayTeam': {'name': 'Beta'},
                       'state': {'description': 'Finished after extra time', 'score': {'current': final}},
                       'events': [{'type': 'Goal', 'time': minute, 'team': {'name': name}} for minute, name in goals]}
            state = details_hl.run(fetch=lambda *_: (payload, 100), token='synthetic',
                                   now=chronology.parse_utc(stamp), priority=())
            self.assertEqual(state['stats']['requests'], 1)

        def view(cut):
            capture, observed = {}, {}
            document = report.build(as_of=cut, capture=capture)
            messages = report.load(result_observations=observed, as_of=cut)[0]
            ranking = opportunity_ranking.build(messages, sourced_results(observed, path, cut),
                                                read_json(PROTOCOL, {}), activation_utc=activation, as_of=cut)
            self.assertEqual(llm.opportunity_digest(cut), ranking)
            return document, capture['research_signals'], ranking

        collect('2026-11-19T20:30:00Z', [('12', 'Alpha'), ('23', 'Beta'), ('80', 'Alpha'), ('110', 'Alpha')], '3 - 1')
        original, prior_rows, prior_rank = view(before)
        self.assertEqual(prior_rank['ranking'][0]['wins'], 1)
        self.assertEqual(prior_rows[0]['ft'], [2, 1])
        # Upstream observation is renewed with the same provider identity and a different link.
        fixtures.write_text(json.dumps({'not_regular_time': {'game-1': dict(binding, link='https://example.invalid/17-v2')}}))
        collect('2026-11-19T22:50:00Z', [('110', 'Alpha')], '1 - 0')
        self.assertEqual(view(before), (original, prior_rows, prior_rank))
        latest, rows, ranking = view(after)
        self.assertEqual(rows[0]['ft'], [0, 0])
        self.assertEqual(ranking['ranking'][0]['losses'], 1)
        self.assertEqual(latest['recent_bets'][0]['result_provenance']['link'], 'https://example.invalid/17-v2')
        self.assertEqual(original['recent_bets'][0]['result_provenance']['available_at'], '2026-11-19T20:30:00+00:00')
        self.assertEqual(latest['recent_bets'][0]['result_provenance']['available_at'], '2026-11-19T22:50:00+00:00')
        self.assertEqual([row['score_90'] for row in details_hl.load_state(path)['detail_versions']['game-1']], [[2, 1], [0, 0]])

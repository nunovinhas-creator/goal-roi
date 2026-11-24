"""Adversarial fixtures for the experimental replay, independent of production state."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scangoal import event_audit, event_intelligence

KICKOFF = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


def clock(minutes):
    return KICKOFF + timedelta(minutes=minutes)


def record(mid, text, sent, *, read=None, edit=None, legacy=False, buttons=None):
    data = dict(message_id=mid, text=text, date_utc=clock(sent).isoformat(),
                edit_date_utc=clock(edit).isoformat() if edit is not None else None,
                collected_at_utc=clock(sent if read is None else read).isoformat(),
                sha256=hashlib.sha256(text.encode()).hexdigest(), buttons=buttons)
    if not legacy:
        data['receipt_version'] = 1
    return data


def poly(mid, selection, sent, *, price='1.90', amount='12K', fixture='alpha-beta', live=None,
         teams='Alpha — Beta', kickoff='15:00', ft=None, reinforcement=None, **receipt):
    head = f"LIVE {live}' 0:0" if live else f'24 nov 2026 {kickoff}'
    multiplier = f' ×{reinforcement}' if reinforcement else ''
    text = (f'📈 🔷 Polymarket{multiplier} · {head}\n{teams} · Test League\n{selection} @{price} · ${amount}\n'
            f'🧾 1 trade · market share 55%\nhttps://scangoal.com/match/{fixture}')
    if ft:
        text += f'\n🏁 FT {ft}'
    return record(mid, text, sent, **receipt)


def stake(mid, selection, sent, *, market='Asian Total', price='2.05', fixture='alpha-beta', **receipt):
    text = (f'💸 🟦 Stake money load — $20K\nTest League · 24 nov 2026 15:00\nAlpha — Beta\n'
            f'📊 {market} → {selection} @ {price}\n🧾 1 bet · biggest $20K\nhttps://scangoal.com/match/{fixture}')
    return record(mid, text, sent, **receipt)


class TemporalReplay(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.messages = self.directory / 'messages.jsonl'

    def save(self, *rows):
        self.messages.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def build(self, minute=150):
        return event_audit.build(self.directory, clock(minute))

    def episode(self, audit, episode_id):
        return next(row for row in audit['episodes'] if row['episode_id'] == episode_id)

    def test_order_uses_emission_and_both_receipts(self):
        first = poly(91, 'Under 2.5', -60, read=-48, legacy=True)
        second = poly(12, 'Over 2.5', -45, read=-41)
        self.save(second, first)
        self.assertFalse(any(row['kind'] == 'sequence' for row in self.build(-42)['episodes']))
        audit = self.build(-41)
        sequence = self.episode(audit, 'sequence:91:12')
        self.assertEqual(sequence['pattern'], 'under_to_over')
        self.assertEqual(sequence['decision_utc'], clock(-41).isoformat())
        self.assertEqual(sequence['features']['emission_gap_seconds'], 900)
        self.assertEqual(sequence['features']['receipt_gap_seconds'], 120)
        self.assertEqual(audit['events'][0]['first_read_utc'], clock(-48).isoformat())
        self.assertEqual(audit['events'][0]['available_utc'], clock(-43).isoformat())
        self.assertEqual(audit, self.build(-41))
        self.save(first, second)
        self.assertEqual(audit['episodes'], self.build(-41)['episodes'])

    def test_late_first_signal_cannot_be_backdated(self):
        self.save(poly(1, 'Under 2.5', -75, read=7), poly(2, 'Over 2.5', -43))
        self.assertNotIn('sequence:1:2', self.build(-30)['outcomes'])
        sequence = self.episode(self.build(), 'sequence:1:2')
        self.assertEqual(sequence['observed_utc'], clock(7).isoformat())
        self.assertIsNone(sequence['decision_utc'])
        self.assertEqual(sequence['features']['receipt_gap_seconds'], -3000)
        self.assertIn('first_signal_outside_decision_window', sequence['exclusion_reasons'])

    def test_edited_body_changes_neither_selection_nor_price(self):
        original = poly(1, 'Under 2.5', -65)
        revised = poly(1, 'Over 2.5', -65, price='4.20', edit=105, read=108, ft='2:1')
        first_seen_edited = poly(3, 'Over 2.5', -32, edit=-28, read=-27)
        self.save(revised, first_seen_edited, poly(2, 'Over 2.5', -41), original)
        early, final = self.build(-25), self.build()
        self.assertEqual(early['episodes'], final['episodes'])
        self.assertEqual(final['events'][0]['odds'], ['1.9', '1.9'])
        self.assertEqual(final['events'][0]['selection'], 'Under 2.5')
        self.assertEqual(final['input_exclusions']['edited_at_first_read'], 1)
        self.assertEqual(len(final['edits']), 2)
        self.assertEqual(early['outcomes']['sequence:1:2']['status'], 'pending')
        label = final['outcomes']['sequence:1:2']
        self.assertEqual((label['status'], label['ft'], label['last_payoff']), ('confirmed', (2, 1), 1.0))
        self.assertEqual(final['fixture_results']['alpha-beta'][0]['journal']['text_sha256'], revised['sha256'])
        self.assertEqual(label['theoretical_profit_units'], 0.9)

    def test_conflicting_same_receipt_versions_and_hashes_are_excluded(self):
        corrupt = poly(3, 'Over 2.5', -40)
        corrupt['sha256'] = 'bad'
        self.save(poly(1, 'Under 2.5', -68), poly(1, 'Over 2.5', -68), corrupt)
        audit = self.build()
        self.assertEqual(audit['input_exclusions'], {'ambiguous_first_version': 1, 'unverified_text_hash': 1})
        self.assertFalse(audit['episodes'])

    def test_identity_joins_exact_aliases_but_quarantines_reused_and_ambiguous_ids(self):
        two_links = [('alternate', 'https://scangoal.com/match/other-fixture')]
        self.save(poly(1, 'Under 2.5', -71), stake(2, 'Over 2.5', -62, fixture='second-exact-id'),
                  poly(3, 'Under 2.5', -54, teams='Gamma — Delta'), poly(4, 'Over 2.5', -51, buttons=two_links),
                  poly(5, 'Over 2.5', -38))
        audit = self.build()
        episode = self.episode(audit, 'sequence:1:2')
        self.assertEqual(episode['fixture_id'], 'alpha-beta')
        self.assertIsNotNone(episode['decision_utc'])
        self.assertEqual(episode['features']['source_relation'], 'divergence')
        self.assertEqual(audit['input_exclusions']['reused_match_id'], 1)
        self.assertEqual(audit['input_exclusions']['ambiguous_match_links'], 1)
        self.assertIn('fixture_identifier_collision', self.episode(audit, 'sequence:1:5')['exclusion_reasons'])
        self.assertEqual(episode, self.episode(self.build(-59), 'sequence:1:2'))

    def test_live_id_cannot_bind_to_two_announced_kickoffs(self):
        self.save(poly(1, 'Under 2.5', 37, live=30), poly(2, 'Over 2.5', 40, live=31),
                  poly(3, 'Under 2.5', 43, kickoff='15:05'), poly(4, 'Under 2.5', 44, kickoff='15:15'))
        audit = self.build()
        self.assertEqual(audit['input_exclusions']['conflicting_fixture_kickoff'], 1)
        self.assertEqual(self.episode(audit, 'sequence:1:2'), self.episode(self.build(42), 'sequence:1:2'))

    def test_line_scope_period_and_joint_outcomes(self):
        self.save(poly(1, 'Under 3.5', -85), stake(2, 'Over 1.50', -81, market='Total Goals'),
                  stake(3, 'Over 3.50', -77, market='1st Half - Asian Total'),
                  poly(4, 'Corners Over 3.5', -70), poly(5, 'Over 3.5', 28, live=28),
                  stake(6, 'Over 1.5', -55))
        audit = self.build()
        overlap = self.episode(audit, 'sequence:1:2')['features']
        self.assertTrue(overlap['same_market'])
        self.assertTrue(overlap['joint_win_possible'])
        self.assertFalse(overlap['same_line'])
        half = self.episode(audit, 'sequence:1:3')['features']
        self.assertFalse(half['same_period'])
        self.assertIsNone(half['joint_win_possible'])
        self.assertFalse(self.episode(audit, 'sequence:1:4')['features']['same_market'])
        transition = self.episode(audit, 'sequence:1:5')['features']
        self.assertTrue(transition['same_line'])
        self.assertFalse(transition['joint_win_possible'])
        self.assertEqual(transition['phases'], ['pre_kickoff', 'at_or_after_kickoff'])
        self.assertTrue(self.episode(audit, 'sequence:2:6')['features']['exact_repetition'])

    def test_scan_goal_under_and_first_half_formats_without_production_parser_changes(self):
        under_text = ("🎯 100% totals form — neither team cleared O 2.5\nTest League · 12'\n"
                      "Alpha 0\nBeta 0\n💰 U 2.5: 1.85\nhttps://scangoal.com/match/alpha-beta")
        half_text = ("🎯 1H total 100% — 1st-half goal\nTest League · 14'\n"
                     "Alpha 0\nBeta 0\n💰 1H Over 0.5: 1.40\nhttps://scangoal.com/match/alpha-beta")
        self.save(poly(1, 'Over 2.5', -60), record(2, under_text, 12), record(3, half_text, 14))
        audit = self.build()
        events = {e['message_id']: e for e in audit['events']}
        self.assertEqual((events[2]['selection'], events[2]['source']), ('Under 2.5', 'ScanGoal'))
        self.assertEqual(events[3]['period'], 'first_half')
        self.assertEqual(self.episode(audit, 'sequence:1:2')['pattern'], 'over_to_under')

    def test_reinforcement_repetition_confirmation_and_exact_duplicates(self):
        first = poly(1, 'Under 2.5', -90)
        duplicate = dict(first, message_id=9, date_utc=clock(-87).isoformat(), collected_at_utc=clock(-87).isoformat())
        self.save(first, duplicate, poly(2, 'Under 2.5', -85, price='1.85', reinforcement=2),
                  stake(3, 'Under 2.50', -80), poly(4, 'Under 2.5', -80, price='1.75'))
        audit = self.build()
        self.assertEqual(audit['events'][1]['duplicate_of'], 1)
        self.assertEqual(audit['events'][2]['signal_kind'], 'reinforcement')
        self.assertEqual(self.episode(audit, 'sequence:1:2')['pattern'], 'under_to_under')
        self.assertEqual(self.episode(audit, 'sequence:2:3')['features']['source_relation'], 'confirmation')
        self.assertNotIn('sequence:3:4', audit['outcomes'])
        self.assertNotIn('sequence:9:2', audit['outcomes'])

    def test_ft_known_by_decision_excludes_economics_and_llm_context(self):
        first = poly(1, 'Under 2.5', -70)
        self.save(first, poly(1, 'Under 2.5', -70, edit=100, read=100, ft='3:1'),
                  poly(2, 'Over 2.5', 101, live=89))
        audit = self.build()
        seq = self.episode(audit, 'sequence:1:2')
        label = audit['outcomes'][seq['episode_id']]
        self.assertIn('ft_already_known', seq['exclusion_reasons'])
        self.assertIsNone(seq['decision_utc'])
        self.assertEqual(label['ft'], (3, 1))
        self.assertIsNone(label['theoretical_profit_units'])
        self.assertNotIn('sequence:1:2', [e['episode_id'] for e in event_audit.llm_context(audit)['episodes']])

    def test_ft_before_regulation_is_not_used_and_unsettled_is_counted(self):
        self.save(poly(1, 'Under 2.5', -69), poly(2, 'Over 2.5', -40),
                  poly(1, 'Under 2.5', -69, edit=60, read=61, ft='2:1'))
        early = self.build()
        self.assertEqual(early['outcomes']['sequence:1:2']['status'], 'pending')
        final = self.build(5000)
        self.assertEqual(final['outcomes']['sequence:1:2']['status'], 'indeterminate')
        self.assertIsNone(final['outcomes']['sequence:1:2']['theoretical_profit_units'])
        self.assertEqual(event_audit.summarize(final)['inversions']['indeterminate'], 1)

    def test_conflicting_result_versions_at_cutoff(self):
        self.save(poly(1, 'Under 2.5', -68), poly(2, 'Over 2.5', -40),
                  poly(1, 'Under 2.5', -68, edit=102, read=104, ft='2:1'),
                  poly(2, 'Over 2.5', -40, edit=110, read=111, ft='1:1'))
        self.assertEqual(self.build(107)['outcomes']['sequence:1:2']['status'], 'confirmed')
        label = self.build(111)['outcomes']['sequence:1:2']
        self.assertEqual(label['reason'], 'conflicting_ft')
        self.assertIsNone(label['theoretical_profit_units'])

    def test_movements_exclude_different_selections_lines_periods_and_sources(self):
        self.save(poly(1, 'Under 2.5', -90, price='2.10–2.15'), poly(2, 'Over 2.5', -85, price='1.77'),
                  poly(3, 'Under 3.5', -80), stake(4, 'Under 2.5', -79),
                  poly(5, '1H Under 2.5', -78), poly(6, 'Under 2.5', -75, price='1.95'),
                  poly(7, 'Under 2.5', -71, price='1.95', amount='13K'),
                  poly(8, 'Under 2.5', -70, price='2.30'), poly(9, 'Under 2.5', -67, price='2.25–2.40'))
        audit = self.build()
        for mid in (2, 3, 4, 5):
            self.assertEqual(self.episode(audit, f'quote:{mid}')['pattern'], 'unproven')
        for mid, state, prior in ((6, 'down', 1), (7, 'stable', 6), (8, 'up', 7), (9, 'unproven', 8)):
            episode = self.episode(audit, f'quote:{mid}')
            self.assertEqual(episode['pattern'], state)
            self.assertEqual(episode['features']['prior_movement']['from_message_id'], prior)
        self.assertEqual(audit['outcomes']['quote:6']['subsequent_movement']['status'], 'stable')
        self.assertEqual(audit['outcomes']['quote:6']['subsequent_movement']['known_utc'], clock(-71).isoformat())

    def test_quote_learned_later_does_not_change_prior_context(self):
        self.save(poly(1, 'Under 2.5', -80, read=-55, price='1.95'),
                  poly(2, 'Under 2.5', -70, price='1.65'), poly(3, 'Under 2.5', -50, price='1.70'))
        early, later = self.build(-65), self.build(-43)
        self.assertEqual(self.episode(early, 'quote:2'), self.episode(later, 'quote:2'))
        self.assertEqual(later['outcomes']['quote:2']['late_prior_movement']['status'], 'down')
        self.assertEqual(later['outcomes']['quote:2']['subsequent_movement']['status'], 'up')
        export = event_audit.llm_context(later, limit=1)
        self.assertEqual(len(export['episodes']), 1)
        self.assertEqual(len(export['episodes'][0]['inputs']), 2)
        self.assertNotIn('subsequent_movement', json.dumps(export))
        self.assertNotIn('late_prior_movement', json.dumps(export))
        self.assertNotIn('payoff', json.dumps(export))
        self.assertTrue(export['experimental'])

    def test_later_live_ft_with_reused_id_cannot_settle_previous_fixture(self):
        self.save(poly(1, 'Under 2.5', -70), poly(2, 'Over 2.5', -50),
                  poly(3, 'Under 2.5', 1440, live=62, ft='7:0'))
        audit = self.build(2000)
        self.assertIsNone(audit['outcomes']['sequence:1:2']['ft'])

    def test_ft_note_on_an_exact_known_alias_excludes_later_canonical_decision(self):
        note = record(3, '🏁 Full time: Alpha 3:1 Beta\nhttps://scangoal.com/match/exact-alias', 100)
        self.save(poly(1, 'Under 2.5', -70), stake(2, 'Over 2.5', -60, fixture='exact-alias'),
                  note, poly(4, 'Over 2.5', 102, live=65))
        audit = self.build()
        self.assertIsNotNone(self.episode(audit, 'sequence:1:2')['decision_utc'])
        self.assertIn('ft_already_known', self.episode(audit, 'sequence:1:4')['exclusion_reasons'])
        self.assertEqual(audit['fixture_results']['alpha-beta'][0]['link'], 'https://scangoal.com/match/exact-alias')

    def test_stake_market_and_price_must_come_from_same_unambiguous_row(self):
        quoted = stake(2, 'Over 2.5', -50)
        ambiguous = quoted['text'].replace('📊 Asian Total', '📊 1X2 → Alpha @3.00\n📊 Asian Total')
        self.save(stake(1, 'Under 2.5', -70), record(2, ambiguous, -50))
        self.assertNotIn('sequence:1:2', self.build()['outcomes'])

    def test_late_live_and_missing_market_clock_do_not_prove_open_window(self):
        live_stake = stake(3, 'Over 3.5', 60)['text'].replace('Alpha — Beta', 'Alpha 0\nBeta 0')
        self.save(poly(1, 'Under 2.5', -70), poly(2, 'Over 2.5', 86, live=86),
                  record(3, live_stake, 60))
        audit = self.build()
        self.assertIn('last_live_market_window_elapsed', self.episode(audit, 'sequence:1:2')['exclusion_reasons'])
        self.assertIn('last_unproven_live_market_window', self.episode(audit, 'quote:3')['exclusion_reasons'])

    def test_stake_price_history_does_not_merge_native_market_labels(self):
        self.save(stake(1, 'Over 2.5', -70, market='Total Goals', price='2.10'),
                  stake(2, 'Over 2.5', -50, market='Asian Total', price='1.90'))
        audit = self.build()
        self.assertEqual(self.episode(audit, 'quote:2')['pattern'], 'unproven')

    def test_simultaneous_conflicting_quotes_cannot_be_ordered_by_read_clock(self):
        self.save(poly(1, 'Under 2.5', -74, read=-74, price='1.75'),
                  poly(2, 'Under 2.5', -74, read=-72, price='2.05'),
                  poly(3, 'Under 2.5', -61, read=-60, price='1.90'))
        move = self.episode(self.build(), 'quote:3')['features']['prior_movement']
        self.assertEqual(move['reason'], 'ambiguous_quote_at_same_time')

    def test_partial_period_cannot_override_conflicting_second_half_scope(self):
        self.save(stake(1, 'Under 2.5', -70),
                  stake(2, 'Over 2.5', -50, market='2nd Half Asian Total After 30 Minutes'))
        audit = self.build()
        self.assertNotIn('sequence:1:2', audit['outcomes'])
        self.assertIn('last_unproven_market_period', self.episode(audit, 'quote:2')['exclusion_reasons'])

    def test_unspecified_whole_goal_total_has_no_asian_push_imputed(self):
        self.save(stake(1, 'Under 2', -70, market='Total Goals'),
                  poly(2, 'Over 2.5', -60, ft='2:0', edit=100, read=100))
        label = self.build()['outcomes']['quote:1']
        self.assertEqual(label['ft'], (2, 0))
        self.assertIsNone(label['last_payoff'])

    def test_live_totals_already_decided_by_the_score_are_excluded(self):
        body = poly(1, 'Over 1.5', 28, live=25)['text'].replace("25' 0:0", "25' 2:0")
        self.save(record(1, body, 28))
        self.assertIn('last_line_already_decided_by_score', self.episode(self.build(), 'quote:1')['exclusion_reasons'])

    def test_external_quotes_are_timestamped_inert_inputs(self):
        args = ('alpha-beta', '1xBet', 'match_goals', 'regulation', 'Over 2.5', '2.5', Decimal('1.9'))
        quote = event_intelligence.ExternalQuote(*args, clock(-45), clock(-42), 'Manual receipt 123')
        self.assertEqual(quote.provenance, 'Manual receipt 123')
        with self.assertRaises(ValueError):
            event_intelligence.ExternalQuote(*args, clock(-42), clock(-45), 'Manual receipt 123')
        with self.assertRaises(ValueError):
            event_intelligence.ExternalQuote(*args, clock(-45), clock(-42), '')

    def test_audit_reads_local_snapshot_without_operational_calls_or_writes(self):
        self.save(poly(1, 'Under 2.5', -90), poly(2, 'Over 2.5', -85))
        for name in ('notify_state.json', 'notify_log.jsonl', 'hypotheses.json', 'report.json',
                     'stake_alerts_state.json', 'polymarket_observer_runs.jsonl'):
            (self.directory / name).write_bytes(b'sentinel')
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with (patch('socket.create_connection', side_effect=AssertionError('network')),
              patch('scangoal.store.append_versions', side_effect=AssertionError('historical write')),
              patch('scangoal.report.build', side_effect=AssertionError('operational report')),
              patch('pathlib.Path.write_bytes', side_effect=AssertionError('disk write'))):
            result = self.build()
            self.assertEqual(len(result['episodes']), 3)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})


if __name__ == '__main__':
    unittest.main()

"""Adversarial offline quote observability tests using synthetic journals only."""

import os
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scangoal import chronology
from scangoal.discovery.v3 import riskset
from scangoal.observability import ContextObservation, QuoteObservation, contract_key, read_timeline
from scangoal.observability.contracts import contract_family
from scangoal.observability.extract import context

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'audits' / 'quote-observability'


def utc(hour, minute=0, second=0):
    return chronology.parse_utc(f'2026-11-24T{hour:02d}:{minute:02d}:{second:02d}Z')


def message(mid, body, emitted, *, collected=None, edited=None, receipt=1):
    row = dict(message_id=mid, text=body, sha256=hashlib.sha256(body.encode()).hexdigest(),
               date_utc=emitted.isoformat(), collected_at_utc=(collected or emitted).isoformat(),
               edit_date_utc=edited.isoformat() if edited else None)
    if receipt is not None:
        row['receipt_version'] = receipt
    return row


def alert(selection='Over 2.5', odds='1.90', fixture='alpha-beta', when='24 nov 2026 15:00',
          teams='Alpha — Beta', ticket='🧾 1 trade · biggest $3.1K'):
    return (f'📈 🔷 Polymarket · {when}\n{teams} · Test League\n{selection} @{odds} · $12K\n'
            f'{ticket}\nhttps://scangoal.com/match/{fixture}')


class Extraction(unittest.TestCase):
    def test_native_clocks_sources_and_asian_contracts_are_exact(self):
        total = dict(source='Stake', phase='pre', market='Asian Total', period='regulation',
                     market_key='match_goals', direction='over', selection='Over 2.25', line='2.25',
                     teams=['Alpha', 'Beta'], fixture_id='one')
        key = contract_key(total)
        self.assertEqual(key, riskset.contract(total))
        self.assertEqual(key, contract_key(dict(total, fixture_id='another')))
        poly = contract_key(dict(total, source='Polymarket'))
        self.assertNotEqual(key, poly)
        self.assertEqual(contract_family(key)[-1], contract_family(poly)[-1])
        self.assertNotEqual(contract_family(key), contract_family(poly))
        for change in (dict(line='2.5'), dict(phase='live'), dict(period='first_half'),
                       dict(period='second_half'), dict(period='first_30_minutes'),
                       dict(market='Total Goals'), dict(market='Total Corners', market_key='corners'),
                       dict(selection='Under 2.25', direction='under')):
            with self.subTest(change=change):
                self.assertNotEqual(key, contract_key(dict(total, **change)))
        handicap = dict(total, market='Asian Handicap', market_key='asian_handicap', line='-0.25',
                        direction=None, selection='AH1(-0.25)')
        self.assertNotEqual(contract_key(handicap),
                            contract_key(dict(handicap, line='+0.25', selection='AH2(+0.25)')))
        self.assertIsNone(contract_key(dict(total, period=None)))
        self.assertIsNone(contract_key(dict(total, line=None)))
        self.assertIsNone(contract_key({}))

    def test_ticket_counts_currencies_and_cumulative_scope(self):
        got = context('🧾 2 bets · biggest $3.1K\nTotal on Over 2.25 this match: $1.2M', 'Over 2.25')
        self.assertEqual((got.biggest, got.n_bets, got.count_unit, got.cumulative),
                         (Decimal('3100'), 2, 'bets', Decimal('1200000')))
        got = context('🧾 0 trades · biggest €3.1K\nΣ on this outcome overall: £2K', None)
        self.assertEqual((got.biggest, got.n_bets, got.count_unit, got.cumulative), (None, 0, 'trades', None))
        self.assertIn('invalid_biggest_ticket_usd', got.issues)
        self.assertIn('invalid_cumulative_usd', got.issues)
        got = context('🧾 1 bet · biggest $1,234\nΣ on this outcome overall: USD 3,10K', None)
        self.assertIsNone(got.biggest)
        self.assertEqual(got.cumulative, Decimal('3100'))
        got = context('🧾 2 bets · biggest $3K\n🧾 3 bets · biggest $5K\n'
                      'Total on Under 2.25 this match: $9K', 'Over 2.25')
        self.assertEqual((got.biggest, got.n_bets, got.cumulative), (None, None, None))
        self.assertIn('cumulative_selection_unproven', got.issues)

    def test_score_paths_keep_stoppage_and_sparse_paths(self):
        got = context("⚽ 45+2′ 1 - 0\n⚽ 47' 1 - 2\n⚽ 47' 1 - 2\n🏁 FT 1:2", None)
        self.assertEqual([(s.kind, s.score, s.minute, s.stoppage) for s in got.scores],
                         [('goal', (1, 0), 45, 2), ('goal', (1, 2), 47, 0), ('final', (1, 2), None, 0)])
        self.assertIn('partial_goal_timeline', got.issues)
        for faulty in ("⚽ 12' 0 - 0", "⚽ 12' 1 - 0\n⚽ 13' 0 - 2", "⚽ 45+3' 1 - 0\n⚽ 45+1' 2 - 0",
                       "⚽ 56' 1 - 0\n🏁 FT 0:1", "⚽ 1' 1 - 0\n⚽ unknown", "⚽ 129' 1 - 0",
                       "⚽ 12' 1 - 0\n🏁 FT 1:0\n🏁 FT 2:0"):
            with self.subTest(faulty=faulty):
                self.assertFalse(any(s.kind == 'goal' for s in context(faulty, None).scores))


class Journal(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.path = self.directory / 'messages.jsonl'

    def save(self, *rows):
        self.path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')

    def test_delayed_edit_adds_facts_without_mutating_original_quote(self):
        original = alert(odds='1.95–2.05')
        update = alert(odds='2.20', ticket='🧾 9 trades · biggest $12K')
        update += "\n⚽ 58' 1 - 0\n🏁 FT 1:0\nΣ on this outcome overall: $60K"
        self.save(message(7, update, utc(10), edited=utc(14), collected=utc(15)),
                  message(7, original, utc(10), collected=utc(10, 4)))
        timeline = read_timeline(self.directory, utc(16))
        quote, = timeline.quotes
        self.assertEqual((quote.price_kind, quote.price_low, quote.price_high, quote.price_point),
                         ('interval', Decimal('1.95'), Decimal('2.05'), None))
        self.assertEqual((quote.biggest_ticket_usd, quote.n_bets, quote.volume_cumulative_usd),
                         (Decimal('3100'), 1, None))
        self.assertEqual(quote.available_utc, utc(10, 4))
        self.assertEqual(quote.receipt, 'completed_page')
        self.assertEqual(quote.volume_alert_usd, Decimal('12000'))
        self.assertFalse(timeline.context_at('alpha-beta', utc(14, 59))[0].scores)
        latest, = timeline.context_at('alpha-beta', utc(15))
        self.assertEqual(latest.available_utc, utc(15))
        self.assertEqual(latest.published_utc, utc(14))
        self.assertEqual(latest.volume_cumulative_usd, Decimal('60000'))
        self.assertEqual([s.kind for s in latest.scores], ['goal', 'final'])
        self.assertEqual(len(latest.record_sha256), 64)
        self.assertEqual(latest.text_sha256, hashlib.sha256(update.encode()).hexdigest())
        self.assertNotEqual(quote.record_sha256, latest.record_sha256)
        history = timeline.history('alpha-beta', utc(15), since=utc(10, 4), completed_only=True)
        self.assertEqual([type(row) for row in history], [QuoteObservation, ContextObservation, ContextObservation])
        prefix = read_timeline(self.directory, utc(14, 59))
        self.assertEqual(timeline.history('alpha-beta', utc(14, 59)), prefix.history('alpha-beta', utc(14, 59)))

    def test_adversarial_receipts_and_missing_identity(self):
        original = alert()
        unknown = alert(fixture='absent').split('\nhttps://')[0]
        self.save(message(1, original, utc(10), collected=utc(10, 1), receipt=None),
                  message(2, alert('Under 2.5'), utc(10), edited=utc(10, 5), collected=utc(10, 4)),
                  message(3, alert('Under 3.5'), utc(10), receipt=True),
                  message(4, alert('Over 3.5'), utc(10), receipt=1.0),
                  message(5, unknown, utc(10)),
                  dict(message(6, alert('Over 4.5'), utc(10)), collected_at_utc='2026-11-24T10:00:00'),
                  dict(message(7, alert('Over 5.5'), utc(10)), sha256='wrong'),
                  {'message_id': 8})
        timeline = read_timeline(self.directory, utc(12))
        self.assertEqual({q.message_id for q in timeline.quotes}, {1, 5})
        unmatched = next(q for q in timeline.quotes if q.message_id == 5)
        self.assertIsNone(unmatched.fixture_id)
        self.assertEqual(unmatched.exclusion, 'missing_match_id')
        self.assertEqual(unmatched.price_kind, 'point')
        reasons = dict(timeline.input_exclusions)
        self.assertEqual(reasons['unknown_receipt_type'], 2)
        self.assertEqual(reasons['unproven_version_clock'], 2)
        self.assertEqual(reasons['publication_after_receipt'], 1)
        self.assertEqual(reasons['unverified_text_hash'], 1)
        self.assertEqual(timeline.history('alpha-beta', utc(10, 5, 59)), ())
        self.assertEqual(len(timeline.history('alpha-beta', utc(10, 6))), 2)
        self.assertEqual(timeline.history('alpha-beta', utc(10, 6), completed_only=True), ())
        with self.assertRaises(ValueError):
            timeline.history('alpha-beta', utc(13))
        with self.assertRaises(ValueError):
            timeline.history('alpha-beta', utc(10).replace(tzinfo=None))

    def test_stale_and_conflicting_versions_and_reconstruction_order(self):
        raw = alert()
        edit = alert(ticket='🧾 2 trades · biggest $6K')
        conflict = alert(ticket='🧾 3 trades · biggest $9K')
        rows = [message(1, raw, utc(10)), message(1, edit, utc(10), edited=utc(13), collected=utc(13, 1)),
                message(1, raw, utc(10), collected=utc(13, 2)),
                message(1, conflict, utc(10), edited=utc(13), collected=utc(13, 3))]
        self.save(*rows, rows[0])
        a = read_timeline(self.directory, utc(14))
        self.save(*reversed(rows))
        b = read_timeline(self.directory, utc(14))
        self.assertEqual(a, b)
        self.assertEqual(a.context_at('alpha-beta', utc(13, 2))[0].n_bets, 2)
        self.assertEqual(a.context_at('alpha-beta', utc(13, 3)), ())
        self.assertEqual(a.quotes[0].n_bets, 1)

    def test_no_quote_from_an_edited_first_read_or_fixture_change(self):
        other = alert(fixture='charlie-delta', teams='Charlie — Delta')
        edited = other + "\n⚽ 70' 0 - 1"
        self.save(message(1, alert(), utc(10)), message(2, other, utc(10)),
                  message(1, edited, utc(10), edited=utc(14), collected=utc(15)),
                  message(3, edited, utc(10), edited=utc(14), collected=utc(15)))
        timeline = read_timeline(self.directory, utc(16))
        self.assertEqual({q.message_id for q in timeline.quotes}, {1, 2})
        self.assertEqual(timeline.context_at('alpha-beta', utc(15)), ())
        self.assertEqual({r.message_id for r in timeline.context_at('charlie-delta', utc(15))}, {2, 3})
        self.assertIsNone(next(r.fixture_id for r in timeline.versions if r.message_id == 1
                               and r.published_utc == utc(14)))

    def test_unrecognized_edit_closes_the_previous_context(self):
        self.save(message(1, alert(), utc(10)),
                  message(1, '', utc(10), edited=utc(13), collected=utc(14)))
        timeline = read_timeline(self.directory, utc(15))
        self.assertEqual(timeline.context_at('alpha-beta', utc(13, 59))[0].n_bets, 1)
        self.assertEqual(timeline.context_at('alpha-beta', utc(14)), ())
        self.assertEqual(len(timeline.quotes), 1)

    def test_raw_ranges_never_become_points_and_before_kickoff_scores_are_withheld(self):
        self.save(message(1, alert(odds='2.0 - 2.2'), utc(10)),
                  message(2, alert(odds='2.2–1.9'), utc(10)),
                  message(3, alert(odds='2.2–'), utc(10)),
                  message(4, alert('Under 2.5') + "\n⚽ 33' 0 - 1", utc(10)),
                  message(5, alert(odds='2.0–2.00'), utc(10)))
        timeline = read_timeline(self.directory, utc(11))
        prices = {q.message_id: (q.price_kind, q.price_point) for q in timeline.quotes}
        self.assertEqual(prices, {1: ('interval', None), 2: ('indeterminate', None),
                                 3: ('indeterminate', None), 4: ('point', Decimal('1.90')), 5: ('interval', None)})
        row = next(v for v in timeline.versions if v.message_id == 4)
        self.assertEqual(row.scores, ())
        self.assertIn('score_timeline_before_kickoff', row.issues)

    def test_periods_and_markets_remain_distinct_after_text_parsing(self):
        selections = ('Over 2.5', '1H Over 2.5', '2H Over 2.5', 'AH1(-0.25)', 'AH2(+0.25)')
        self.save(*(message(i, alert(selection), utc(10, i)) for i, selection in enumerate(selections)))
        quotes = read_timeline(self.directory, utc(11)).quotes
        self.assertEqual([q.contract_key[3] for q in quotes], [
            'regulation', 'first_half', 'second_half', 'regulation', 'regulation',
        ])
        self.assertEqual([q.contract_key[4] for q in quotes], ['2.5', '2.5', '2.5', '-0.25', '+0.25'])
        self.assertEqual(len({q.contract_key for q in quotes}), 5)

    def test_synthetic_command_is_deterministic_and_strict_pairs_require_receipts(self):
        # Exercise autonomous mining and the waterfall without the historical corpus.
        self.save(*(message(i, alert(fixture=f'{i}-fixture', teams=f'Home {i} — Away {i}'), utc(10))
                    for i in range(1, 5)))
        command = [sys.executable, str(AUDIT / 'diagnose.py'), '--data-dir', str(self.directory),
                   '--as-of', '2026-11-24T11:00:00Z', '--days', '90', '--repetitions', '2']
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        a = subprocess.check_output(command, cwd=ROOT, env=env)
        b = subprocess.check_output(command, cwd=ROOT, env=env)
        self.assertEqual(a, b)
        report = json.loads(a)
        self.assertEqual(report['strict_pairs'], 0)
        self.assertEqual(report['completed_receipt_pairs_summed'], 0)
        self.assertEqual(report['price_inventory']['total_quotes'], 4)
        self.assertEqual(report['density_by_contract'][0]['hourly_arrivals'][0]['fixtures'], 4)
        self.assertEqual(report['density_by_contract'][0]['references_with_point_in_both_and_kickoff_within_45m'], 4)
        zero = report['power']['default_zero_pair_power']
        self.assertIsNone(zero['rate_equivalent_days_for_minimum_pairs'])
        self.assertEqual([effect['effect_pp'] for effect in zero['effects']], [4, 8, 12])
        self.assertFalse(report['confirmatory'])


class Power(unittest.TestCase):
    def test_projection_uses_completed_window_pairs_from_the_strict_cascade(self):
        script = '''
import sys
sys.path[:0] = sys.argv[1:]
from diagnose import pair_diagnostics
from scangoal.discovery.replay import Replay
from world import RULE, Scenario, build

replay, _ = build(Scenario('phase-0-test', days=3, per_day=12))
candidates = [dict(rule=RULE)]
completed = pair_diagnostics(replay, candidates, (7,), 2)
assert completed['strict_pairs'] > 0
assert completed['strict_pairs'] == completed['completed_receipt_pairs_summed']
assert completed['strict_pairs'] == completed['age_seconds']['matched_comparator']['n']
legacy = Replay(dict(replay.audit, events=[dict(e, receipt='legacy_upper_bound') for e in replay.by_id.values()]))
legacy_report = pair_diagnostics(legacy, candidates, (7,), 2)
assert legacy_report['strict_pairs'] == completed['strict_pairs']
assert legacy_report['completed_receipt_pairs_summed'] == 0
assert legacy_report['power']['default_zero_pair_power']['pairs_per_day'] is None
'''
        subprocess.run([sys.executable, '-c', script, str(AUDIT), str(AUDIT.parent / 'temporal-discovery-v3')],
                       cwd=ROOT, check=True)

    def test_power_repeats_only_observed_receipt_days_and_runs_kernel(self):
        script = '''
import sys
from datetime import date, timedelta
sys.path[:0] = sys.argv[1:]
from power import PairSample, calculate

first = date(2026, 1, 5)
sample = tuple(PairSample(first + timedelta(days=i), first + timedelta(days=i), 'Stake',
                          'bucket' + str(i % 2), (2.0, 2.05), (25., 26.), (.5, 1.))
               for i in range(8))
a = calculate(sample, first, 14, (7, 56, 112), 2)
assert a == calculate(sample, first, 14, (7, 56, 112), 2)
assert a['observed_pairs'] == 8
assert a['pairs_per_day'] == 8 / 14
first, middle, last = a['effects'][0]['horizons']
assert first['projected_pairs'] == 7
assert middle['projected_pairs'] == 32 and not middle['minimum_sample_and_weeks']
assert last['projected_pairs'] == 64 and last['minimum_sample_and_weeks']
assert last['layers']['prediction']['n'] == last['layers']['announced']['n'] == 2
'''
        subprocess.run([sys.executable, '-c', script, str(AUDIT), str(AUDIT.parent / 'temporal-discovery-v3')],
                       cwd=ROOT, check=True)


if __name__ == '__main__':
    unittest.main()

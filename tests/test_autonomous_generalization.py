"""Adversarial tests for the experimental H path and its isolation from v3.2."""

from datetime import datetime, timedelta, timezone
from random import Random
import unittest

from scangoal import event_intelligence as events
from scangoal.discovery import dsl
from scangoal.discovery.experimental_h import miner, observed, rules
from scangoal.discovery.v32 import engine, language
from scangoal.discovery.v32.tape import Coverage, Tape
from scangoal.offline_pilot import books, discovery as previous, fixtures

T = datetime(2047, 1, 8, 20, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def atom(field, value, op='eq'):
    return dict(field=field, value=value, op=op)


def rule(*kinds, links=()):
    return dict(version=32, within_seconds=720,
                steps=[[atom('event_kind', kind)] for kind in kinds], relations=list(links))


def edge(left, right, field, value, op='eq'):
    return dict(atom(field, value, op), **{'from': left, 'to': right})


def row(game, offset, kind='quote', **changes):
    day, at = T + timedelta(days=game), T + timedelta(days=game) + offset * M
    fixture = books.Game(f'fixture-{game}', events.iso(day - 26 * M), f'home-{game}', f'away-{game}', 'lab')
    live = dict(phase='live', minute=26 + offset, score=[0, 0])
    return fixtures.typed_event(fixture, at, kind, **(live | changes))


def quote(price):
    return dict(odds=[price, price], price_low=float(price), price_high=float(price))


def decisions(rows, at=T, coverage=()):
    source = observed.Observed(Tape(rows, at, coverage))
    return source, list(source.decisions(rules.Meter(200_000)))


def varied_world():
    rows = []
    # Development unit test for generic operators. These fixtures are absent
    # from both independent banks.
    for i in range(12):
        second = '2.05' if i % 3 else '2.80'
        third = '2.39' if i % 4 else '1.85'
        rows.extend((row(i, -9, **quote('2.30')),
                     row(i, -7, 'alert', source='Betfair', volume_usd=12_500 + i * 833),
                     row(i, -4, **quote(second)), row(i, -2, 'score'),
                     row(i, 0, **quote(third))))
    return rows


class ObservationBoundary(unittest.TestCase):
    def test_conflict_is_masked_only_after_receipt_and_never_certifies_absence(self):
        early = row(0, -5, volume_usd=1_000, volume_cumulative_usd=2_000, n_bets=8, count_unit='bets')
        late = dict(early, available_utc=events.iso(T + M), volume_usd=70_000,
                    volume_cumulative_usd=90_000, count_unit='trades')
        terminal = row(0, 0)
        terminal2 = row(0, 2)
        _, values = decisions([early, terminal])
        fixture = values[-1].fixture
        proof = Coverage(fixture, T - 30 * M, T + 2 * M, T + 2 * M, '1' * 64)
        source, with_future = decisions([late, terminal2, early, terminal], T + 2 * M, (proof,))
        self.assertEqual(values[-1], next(item for item in with_future if item.at == T))
        last = with_future[-1]
        previous = [event for event in last.history if event['emitted_utc'] == early['emitted_utc']]
        self.assertEqual(len(previous), 2)
        self.assertTrue(all(event['volume_usd'] is None and event['volume_cumulative_usd'] is None
                            and event['n_bets'] is None for event in previous))
        self.assertFalse(source.covered(last, T - 8 * M, last.at))
        self.assertEqual(2.0, rules.FIELDS['point_odds'].read(previous[0]))

    def test_ambiguous_volume_drives_neither_positive_nor_negative(self):
        old = row(0, -5, volume_usd=1)
        terminal = row(0, 0, volume_usd=200)
        _, values = decisions([old, terminal])
        proof = Coverage(values[-1].fixture, T - 30 * M, T, T, '2' * 64)
        source, values = decisions([dict(old, volume_usd=30), old, terminal], coverage=(proof,))
        definition = rule('quote', 'quote', links=[edge(0, 1, 'volume_ratio', 1, 'gt')])
        self.assertEqual(rules.Rule(definition).match(source, values[-1], rules.Meter(1000)).state, 'indeterminate')

    def test_price_requires_exact_period_phase_contract_and_valid_issue_order(self):
        old, terminal = row(0, -5, **quote('2.50')), row(0, 0, **quote('1.90'))
        _, pair = decisions([old, terminal])
        a, b = pair[-1].history[0], pair[-1].quote
        self.assertLess(rules.relation_value('odds_delta_bp', a, b), 0)
        self.assertIsNone(rules.relation_value('odds_delta_bp', a | {'period': 'first_half'}, b))
        self.assertIsNone(rules.relation_value('odds_delta_bp', a | {'phase': 'pre'}, b))
        self.assertIsNone(rules.relation_value('odds_delta_bp', a | {'price_kind': 'range'}, b))
        self.assertIsNone(rules.relation_value('odds_delta_bp', a | {'fixture_id': 'other'}, b))
        self.assertIsNone(rules.relation_value('odds_delta_bp', a | {'quote_issued_utc': b['quote_issued_utc']}, b))

    def test_absence_needs_coverage_and_simultaneous_issues_cannot_form_an_order(self):
        old = row(0, -4, 'alert')
        revision = row(0, -4, 'revision')
        end = row(0, 0)
        definition = rules.Rule(rule('alert', 'revision', 'quote'))
        unproved, pair = decisions([old, end, revision])
        self.assertEqual(definition.match(unproved, pair[-1], rules.Meter(1000)).state, 'indeterminate')
        proof = Coverage(pair[-1].fixture, T - 30 * M, T, T, 'a' * 64)
        proved, pair = decisions([end, revision, old], coverage=(proof,))
        self.assertEqual(definition.match(proved, pair[-1], rules.Meter(1000)).state, 'absent')
        self.assertEqual(definition.match(proved, pair[-1], rules.Meter(1)).state, 'search_budget')

    def test_future_event_and_inert_result_preserve_prior_rule(self):
        before = row(0, -5, 'alert')
        end = row(0, 0)
        signed = rules.Rule(rule('alert', 'quote'))
        baseline, pair = decisions([before, end])
        future = row(0, -2, 'goal', score=[1, 0], available_utc=events.iso(T + M))
        expanded, later = decisions([dict(end, ft=[7, 9]), future, before], T + M)
        self.assertEqual(signed.match(baseline, pair[-1], rules.Meter(10000)),
                         signed.match(expanded, later[-1], rules.Meter(10000)))
        goals = rules.Rule(rule('goal', 'quote'))
        self.assertEqual(goals.match(expanded, later[-1], rules.Meter(10000)).state, 'indeterminate')


class Discovery(unittest.TestCase):
    def test_goal_projection_failure_remains_in_frozen_code_and_successor_accepts_types(self):
        raw, _, _ = previous.bank(previous.TRAIN)
        tape = Tape(raw, previous.BASE + timedelta(days=7))
        with self.assertRaisesRegex(dsl.Invalid, 'invalid_literal'):
            engine.discover(tape, work=800_000, emissions=160_000)
        result = miner.discover(tape)
        self.assertTrue(any(any(atom == dict(field='event_kind', op='eq', value='goal')
                                for atom in step) for candidate in result['candidates']
                            for step in candidate['rule']['steps']))
        self.assertFalse(result['confirmatory'])

    def test_typed_search_is_deterministic_bounded_and_emits_real_contrasts(self):
        raw = varied_world()
        at = T + timedelta(days=13)
        expected = miner.discover(Tape(raw, at))
        shuffle = [dict(row, message_id=i, ft=[4, 0]) for i, row in enumerate(raw + raw)]
        Random(472).shuffle(shuffle)
        self.assertEqual(expected, miner.discover(Tape(shuffle, at)))
        self.assertFalse(expected['work']['exhausted'])
        self.assertLessEqual(expected['work']['used'], expected['work']['limit'])
        self.assertGreaterEqual(max(len(item['rule']['steps']) for item in expected['candidates']), 5)
        self.assertTrue(any(link['to'] - link['from'] > 1 for item in expected['candidates']
                            for link in item['rule']['relations']))
        self.assertTrue(any(len(item['rule']['relations']) + sum(len(s)-1 for s in item['rule']['steps']) > 1
                            for item in expected['candidates']))
        self.assertTrue(any(item['contrast_witness_count'] for item in expected['candidates']))
        ids = {item['witness_sha256'] for item in expected['candidates']}
        self.assertEqual(len(ids), len(expected['candidates']))
        for item in expected['candidates']:
            self.assertEqual(language.identifier(item['rule']), item['id'])
            if item['alternative']:
                self.assertNotEqual(item['rule'], item['alternative'])
                self.assertEqual(item['rule']['steps'][-1], item['alternative']['steps'][-1])
        for cap in (0, 17, 5000):
            limited = miner.discover(Tape(raw, at), work=cap)
            self.assertTrue(limited['work']['exhausted'])
            self.assertLessEqual(limited['work']['used'], cap)

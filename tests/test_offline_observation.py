"""Independent adversarial and offline integration regressions for PR G."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import event_intelligence as events
from scangoal.discovery.v32 import registry
from scangoal.discovery.v32.tape import Tape
from scangoal.offline_pilot import assurance, books, discovery, fixtures, manifest, pilot

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / 'audits/offline-observation/run.py'
SPEC = importlib.util.spec_from_file_location('offline_audit', AUDIT_PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
T = assurance.T


class SignedCoverage(unittest.TestCase):
    def test_executable_adversarial_matrix_and_frozen_files(self):
        report = assurance.run()
        self.assertEqual(report['check_count'], 10)
        self.assertEqual(report['state_counts'], {'present': 1, 'absent': 1, 'indeterminate': 4})
        self.assertGreaterEqual(AUDIT.frozen()['checked_files'], 25)

    def _one(self, directory, *, expected=None, received=None, **changes):
        game = assurance.lab_games()[0]
        expected = [] if expected is None else expected
        received = expected if received is None else received
        slot = assurance.slot_for(game, 'alert', expected)
        row = manifest.receipt(slot, **dict(dict(requested=T, available=T, source_clock=T,
                                                  status='received' if received else 'empty', payload=received),
                                            **changes))
        return assurance.verified(directory, [(slot, row)]), slot

    def test_complete_typed_empty_is_scoped_and_right_watermarked(self):
        with TemporaryDirectory() as folder:
            replay, slot = self._one(folder)
            self.assertFalse(replay.at(T - assurance.SECOND).coverage)
            evidence = replay.at(T)
            (proof,) = evidence.coverage
            self.assertEqual(evidence.slots[slot.id]['status'], 'empty')
            self.assertEqual(proof.streams, frozenset({'alert'}))
            self.assertFalse(Tape([], T, evidence.coverage).covered(slot.fixture, assurance.LEFT, T, T))

    def test_fake_receipts_body_signature_and_clock_fail_closed(self):
        scenarios = [(dict(source_clock=T + assurance.SECOND), 'unproven_latency_or_source_clock'),
                     (dict(available=T + timedelta(minutes=3)), 'unproven_latency_or_source_clock'),
                     (dict(requested=T - assurance.SECOND), 'request_or_receipt_outside_clock')]
        for changes, reason in scenarios:
            with self.subTest(changes=changes), TemporaryDirectory() as folder:
                replay, slot = self._one(folder, **changes)
                evidence = replay.at(T + timedelta(minutes=4))
                self.assertEqual(evidence.slots[slot.id]['status'], reason)
                self.assertFalse(evidence.coverage)

    def test_independent_scope_must_match_event_numbers_and_identities(self):
        game, other = assurance.lab_games()
        event = fixtures.typed_event(game, T - assurance.SECOND, 'alert', volume_usd=14_000)
        for wrong, reason in [
            (dict(event, volume_usd=True), 'invalid_typed_number'),
            (dict(event, event_kind='revision'), 'wrong_typed_stream'),
            (dict(event, teams=[other.home, other.away]), 'wrong_event_fixture'),
            (dict(event, available_utc=events.iso(T + assurance.SECOND)), 'event_outside_sealed_watermark'),
        ]:
            with self.subTest(reason=reason), TemporaryDirectory() as folder:
                replay, slot = self._one(folder, expected=[wrong])
                evidence = replay.at(T)
                self.assertEqual(evidence.slots[slot.id]['status'], reason)
                self.assertFalse(evidence.coverage)
                self.assertFalse(evidence.rows)

    def test_even_a_signed_empty_cannot_omit_committed_content(self):
        game = assurance.lab_games()[0]
        event = fixtures.typed_event(game, T - timedelta(seconds=5), 'alert', volume_usd=92_000)
        with TemporaryDirectory() as folder:
            replay, slot = self._one(folder, expected=[event], received=[])
            self.assertEqual(replay.at(T).slots[slot.id]['status'], 'independent_inventory_mismatch')

    def test_reading_rows_cannot_mutate_the_verified_prefix(self):
        game = assurance.lab_games()[0]
        row = fixtures.typed_event(game, T - assurance.SECOND, 'alert', volume_usd=17_000)
        with TemporaryDirectory() as folder:
            replay, slot = self._one(folder, expected=[row])
            expected = replay.tape_at(T).snapshot_sha256
            replay.entries[0][1]['payload'][0]['volume_usd'] = 90_000
            evidence = replay.at(T)
            evidence.rows[0]['teams'][0] = 'changed'
            evidence.slots[slot.id]['receipt']['available_utc'] = 'invalid'
            with self.assertRaises(TypeError):
                replay.slots[slot.id] = replace(slot, stream='quote')
            self.assertEqual(replay.tape_at(T).snapshot_sha256, expected)

    def test_plan_and_capture_cannot_use_one_authority_or_rebind_seals(self):
        authority, capture = fixtures.authorities()
        slot = assurance.slot_for(assurance.lab_games()[0], 'score', [])
        plan = manifest.make_plan([slot], sealed_at=assurance.LEFT, catalog_sha256='c' * 64, authority=authority)
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(manifest.Invalid, 'authorities_must_be_distinct'):
                manifest.VerifiedReplay(plan, authority.public_key(), folder, authority.public_key(), witness=None)
            with self.assertRaisesRegex(manifest.Invalid, 'invalid_signature'):
                manifest.load_plan(plan, capture.public_key())
        altered = deepcopy(plan)
        altered['content']['slots'][0]['stream'] = 'goal'
        with self.assertRaisesRegex(manifest.Invalid, 'invalid_signature'):
            manifest.load_plan(altered, authority.public_key())

    def test_postdated_or_cross_domain_plan_cannot_mint_coverage(self):
        authority, _ = fixtures.authorities()
        slot = assurance.slot_for(assurance.lab_games()[0], 'alert', [])
        plan = manifest.make_plan([slot], sealed_at=assurance.LEFT + assurance.SECOND,
                                  catalog_sha256='a' * 64, authority=authority)
        with self.assertRaisesRegex(manifest.Invalid, 'postdated_slot_plan'):
            manifest.load_plan(plan, authority.public_key())
        sample = replace(slot, mode=manifest.POLL, inventory=None)
        plan = manifest.make_plan([sample], sealed_at=assurance.LEFT, catalog_sha256='a' * 64, authority=authority)
        with self.assertRaisesRegex(manifest.Invalid, 'invalid_slot_scope'):
            manifest.load_plan(plan, authority.public_key())

    def test_future_conflicting_receipt_preserves_earlier_point_in_time_state(self):
        game = assurance.lab_games()[0]
        event = fixtures.typed_event(game, T - assurance.SECOND, 'alert')
        slot = assurance.slot_for(game, 'alert', [event])
        good = manifest.receipt(slot, requested=T, available=T, source_clock=T, status='received', payload=[event])
        conflict = manifest.receipt(slot, requested=T, available=T + assurance.SECOND, source_clock=T,
                                    status='empty', payload=[])
        # Constructor forbids duplicated plan slots, so append the second
        # receipt after producing the original independently checked plan.
        authority, capture = fixtures.authorities()
        plan = manifest.make_plan([slot], sealed_at=assurance.LEFT, catalog_sha256='a' * 64, authority=authority)
        plan_sha, _ = manifest.load_plan(plan, authority.public_key())
        with TemporaryDirectory() as folder:
            journal = manifest.Journal(folder, plan_sha, capture.public_key())
            first = journal.append_many([good], capture)
            before = manifest.VerifiedReplay(plan, authority.public_key(), folder, capture.public_key(), witness=first)
            then = before.tape_at(T).snapshot_sha256
            final = journal.append_many([conflict], capture)
            after = manifest.VerifiedReplay(plan, authority.public_key(), folder, capture.public_key(), witness=final)
            self.assertEqual(after.tape_at(T).snapshot_sha256, then)
            self.assertEqual(after.at(T + assurance.SECOND).slots[slot.id]['status'], 'conflicting_receipts')
            self.assertFalse(after.at(T + assurance.SECOND).coverage)


class NativeBooks(unittest.TestCase):
    def setUp(self):
        self.payload = fixtures.catalogue()
        self.trusted = {name: game for name, (game, _) in fixtures.games().items()}

    def test_exact_period_home_away_and_settlement_guard(self):
        for field, change, reason in [('period', 'first_half', 'settlement_unproven'),
                                      ('line', '2.50', 'settlement_unproven'),
                                      ('rules', 'with_extra_time', 'settlement_unproven')]:
            data = deepcopy(self.payload)
            games = data['events']
            games[0]['markets'][-1][field] = change
            with self.subTest(field=field), self.assertRaisesRegex(manifest.Invalid, reason):
                books.catalog(data, self.trusted)
        data = deepcopy(self.payload)
        game = data['events'][0]['game']
        game['home'], game['away'] = game['away'], game['home']
        with self.assertRaisesRegex(manifest.Invalid, 'changed_fixture_identity'):
            books.catalog(data, self.trusted)

    def test_cross_protocol_and_yes_no_never_join(self):
        contracts = books.catalog(self.payload, self.trusted)
        ctf, = [row for row in contracts if row.game.id == 'ref-ctf' and row.family == 'total_goals' and row.side == 'YES']
        v2, = [row for row in contracts if row.game.id == 'ref-v2' and row.family == 'total_goals' and row.side == 'YES']
        no, = [row for row in contracts if row.game.id == 'ref-ctf' and row.family == 'total_goals' and row.side == 'NO']
        self.assertNotEqual(ctf.scope, v2.scope)
        self.assertNotEqual(ctf.scope, no.scope)
        slot = next(x for x in books.plan_slots(contracts, fixtures.REFERENCES) if x.target == v2.id)
        row = fixtures.mock_book(slot, v2)
        with self.assertRaisesRegex(manifest.Invalid, 'wrong_protocol'):
            books.read_book(ctf, row)
        self.assertEqual(books.read_book(v2, row)['price_unit'], books.PRICE_UNIT)

    def test_duplicate_market_token_and_semantic_fixture_refused(self):
        data = deepcopy(self.payload)
        same = deepcopy(data['events'][0]['markets'][0])
        same['market_id'] += '-duplicate'
        data['events'][0]['markets'].append(same)
        with self.assertRaisesRegex(manifest.Invalid, 'ambiguous_contract_or_token_reuse'):
            books.catalog(data, self.trusted)
        data = deepcopy(self.payload)
        row = deepcopy(data['events'][0])
        row['game']['id'] = 'alias-duplicate'
        data['events'].append(row)
        original = self.trusted['ref-ctf']
        with self.assertRaisesRegex(manifest.Invalid, 'ambiguous_or_changed_fixture_identity'):
            books.catalog(data, self.trusted | {'alias-duplicate': replace(original, id='alias-duplicate')})

    def test_partial_catalogue_cannot_shrink_the_planned_denominator(self):
        data = deepcopy(self.payload)
        data['events'].pop()
        with self.assertRaisesRegex(manifest.Invalid, 'missing_expected_catalogue_fixture'):
            books.catalog(data, self.trusted)
        data = deepcopy(self.payload)
        data['events'][0]['markets'].pop()
        with self.assertRaisesRegex(manifest.Invalid, 'missing_expected_catalogue_contract'):
            books.catalog(data, self.trusted)

    def test_schedule_includes_both_exact_boundaries_and_excludes_46_minutes(self):
        contracts = books.catalog(self.payload, self.trusted)
        slots = list(books.plan_slots(contracts, fixtures.REFERENCES))
        self.assertEqual(len(slots), 720)
        for contract in contracts:
            times = sorted(manifest.stamp(slot.end) for slot in slots if slot.target == contract.id)
            if contract.game.id == 'outside-46':
                self.assertFalse(times)
                continue
            kickoff = manifest.stamp(contract.game.kickoff)
            self.assertEqual(len(times), 24)
            self.assertEqual(times[0], kickoff - timedelta(hours=6))
            self.assertEqual(times[-1], kickoff - timedelta(minutes=15))
            self.assertTrue(all(b - a == timedelta(minutes=15) for a, b in zip(times, times[1:])))

    def test_decimal_depth_keeps_native_units_and_rejects_float_or_duplicate(self):
        contracts = books.catalog(self.payload, self.trusted)
        slot = next(iter(books.plan_slots(contracts, fixtures.REFERENCES)))
        contract = next(row for row in contracts if row.id == slot.target)
        row = fixtures.mock_book(slot, contract)
        data = books.read_book(contract, row)
        self.assertEqual(data['ask_depth_shares'], '282')
        self.assertEqual(data['bid_depth_shares'], '185')
        self.assertNotIn('midpoint', data)
        for invalid, reason in [(dict(row['payload'], bids=[dict(price=0.42, size='110')]), 'noncanonical'),
                                (dict(row['payload'], bids=row['payload']['bids'] * 2), 'duplicate_price_level')]:
            with self.assertRaisesRegex(manifest.Invalid, reason):
                books.read_book(contract, dict(row, payload=invalid))


class Integrated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A deliberate socket or DNS call would fail this full local command.
        with patch.object(socket, 'socket', side_effect=AssertionError('network unavailable')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS unavailable')):
            captures = json.loads(AUDIT_PATH.with_name('recorded-books.json').read_bytes())
            cls.book = pilot.temporary_run(captures=captures['responses'])
            cls.brain = discovery.run(try_model=False)

    def test_pilot_audits_losses_conflicts_and_two_native_protocols(self):
        self.assertEqual(self.book['window']['included_games'], ['edge-ctf', 'gap-ctf', 'pair-v2', 'ref-ctf', 'ref-v2'])
        self.assertEqual((self.book['slots']['planned'], self.book['slots']['observed'], self.book['slots']['lost']),
                         (720, 706, 14))
        self.assertEqual(self.book['recorded_book_responses'], 2)
        for fault in ('lost_receipt', 'timeout', 'rate_limited', 'offline', 'disk_error', 'market_suspended',
                      'book_identity_changed_or_conflicted', 'no_two_sided_liquidity', 'crossed_or_locked_book',
                      'incomplete_book_or_wrong_protocol', 'unproven_latency_or_source_clock', 'conflicting_receipts'):
            self.assertIn(fault, self.book['slots']['reasons'])
        self.assertEqual(self.book['by_protocol']['CTF']['reopened'], 1)
        self.assertEqual(self.book['book_continuous_coverage_spans'], 0)
        self.assertIsNone(self.book['power'])
        self.assertEqual(self.book['confirmatory_pairs'], 0)
        self.assertTrue(all(self.book[key] == 0 for key in ('network_calls', 'orders_sent', 'notifications_sent')))

    def test_full_book_without_alerts_and_late_goal_remain_indeterminate(self):
        coverage = self.book['independent_stream_probe']
        rows = {r['game']: r for probe in coverage.values() for r in probe['rows']}
        self.assertEqual(rows['edge-ctf']['antecedent'], 'absent')
        self.assertEqual(rows['gap-ctf']['antecedent'], 'indeterminate')
        self.assertNotIn('alert', rows['gap-ctf']['streams'])
        self.assertEqual(rows['pair-v2']['antecedent'], 'indeterminate')
        self.assertNotIn('goal', rows['pair-v2']['streams'])
        self.assertTrue(all(row['observed'] for row in self.book['observations'] if row['game_id'] == 'gap-ctf'))
        self.assertEqual(coverage['CTF']['admissible_pairs'], 1)
        self.assertEqual(coverage['V2']['admissible_pairs'], 0)

    def test_catalogue_book_conflict_does_not_rewrite_old_comparisons(self):
        conflicted = next(row for row in self.book['observations'] if row['status'] == 'conflicting_receipts')
        earlier = [row for row in self.book['comparisons'] if row['reference_slot'] == conflicted['slot']]
        self.assertEqual(len(earlier), 5)
        self.assertTrue(any(row['status'] == 'valid' for row in earlier))
        self.assertEqual(set(self.book['comparators']), {'excluded', 'indeterminate', 'valid'})

    def test_unseen_benchmark_exposes_legacy_crash_and_tests_unmodified_learner(self):
        self.assertEqual(self.brain['frozen_entrypoint']['status'], 'invalid_literal_on_mixed_trajectory')
        results = self.brain['deterministic']
        self.assertEqual(results['candidates'], 47)
        self.assertFalse(self.brain['transfer_gate'])
        self.assertEqual(results['holdout_passes'], 0)
        for candidate in [*results['all_candidates'], self.brain['oracle']]:
            self.assertEqual(registry.hypothesis_id(candidate['rule']), candidate['id'])
        for score in self.brain['oracle']['holdout']:
            self.assertEqual((score['exact_terminal'], score['exact_witness']), (6, 6))
            self.assertFalse(score['false_motifs'])
        for score in results['selected_on_development']['holdout']:
            self.assertEqual(score['precision_at_t'], 1 / 3)
            self.assertEqual(score['false_motifs'], {'price_rises': 6, 'wrong_period': 6})
        self.assertIsNone(self.brain['llm']['metrics'])

    def test_versioned_summary_is_identical_to_executable_replay(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network unavailable')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS unavailable')):
            report = AUDIT.run(try_model=False)
        generated = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        self.assertEqual(generated.encode(), AUDIT_PATH.with_name('report.json').read_bytes())


if __name__ == '__main__':
    unittest.main()

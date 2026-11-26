import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from scangoal import dropping_odds_alerts as alerts, notify
from scangoal.dropping_odds_journal import GitJournal, Journal, JournalError, REF, WITNESS_REF, utc

UTC = timezone.utc
SENT = datetime(2026, 11, 26, 13, 53, 40, tzinfo=UTC)
NOW = SENT + timedelta(minutes=8)
BEFORE = SENT - timedelta(hours=1)
ON = {'GOAL_ROI_DROPPING_ODDS': 'on', 'GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN': '15',
      'GOAL_ROI_DROPPING_ODDS_ON_SINCE': (BEFORE + timedelta(minutes=1)).isoformat(),
      'TG_BOT_TOKEN': 'fake-token', 'TG_CHAT_ID': 'fake-chat'}
SHADOW = {'GOAL_ROI_DROPPING_ODDS': 'shadow'}
A = """Odds drop: Al Waab FC
⚽️ Football · League 2 · starts in 21 min
Al-Mu Aidar – Al Waab FC
Handicap Al Waab FC (0) — odds 2.46 → 2.02 (−18%)
Strategy: drop from 15%
https://scangoal.com/MySignals.ashx?go=23543"""
B = """Odds drop
⚽️ Football · PRO LEAGUE · starts in 3 h 45 min
Al Faisaly (ksa) – Al Draih
Total over 3 — odds 2.28 → 1.92 (−16%)
Strategy: drop from 15%
https://scangoal.com/MySignals.ashx?go=23528"""
C = """Odds drop: Vojvodina
⚽️ Football · Super Liga · starts in 47 h 56 min
Radnik Surdulica – Vojvodina
Handicap Vojvodina (−0.75) — odds 2.24 → 1.87 (−16%)
Strategy: drop from 15%
https://scangoal.com/MySignals.ashx?go=23468"""


def row(mid=1, text=A, sent=SENT, received=None, edited=None):
    received = received or (sent + timedelta(minutes=8))
    return {'message_id': mid, 'text': text, 'date_utc': sent.isoformat(),
            'collected_at_utc': received.isoformat(),
            'edit_date_utc': edited.isoformat() if edited else None}


def disposition(text=A, sent=SENT, now=NOW, max_age=None):
    item = alerts.observation(1, [row(text=text, sent=sent)])
    return item, alerts.assess(item, now, max_age, alerts.SeenQuotes(), alerts.snapshot(item))


class ParsingTests(unittest.TestCase):
    def test_three_examples(self):
        a, reason = disposition(A)
        self.assertEqual(reason, 'eligible')
        self.assertEqual(a.decision.kickoff, SENT + timedelta(minutes=21))
        self.assertIn('🕒 14:14', alerts.alert(a))
        self.assertIn('2.46 → 2.02 | −18%', alerts.alert(a))
        self.assertIn('Rentabilidade não demonstrada.', alerts.alert(a))
        self.assertEqual(disposition(B)[1], 'market')
        self.assertEqual(disposition(C)[1], 'other_day')

    def test_line_allowlist(self):
        for line in ('0', '+0.25', '-0.25', '+0.75', '−0.75', '-0.00', '+0,250'):
            with self.subTest(line=line):
                self.assertEqual(disposition(A.replace('(0)', f'({line})'))[1], 'eligible')
        for line in ('+0.50', '-0.50', '+1', '−1.25', '0.2501'):
            with self.subTest(line=line):
                self.assertEqual(disposition(A.replace('(0)', f'({line})'))[1], 'line')

    def test_reported_drop_inclusive(self):
        for final, drop, reason in [('1.72', 14, 'reported_drop'), ('1.70', 15, 'eligible'),
                                    ('1.68', 16, 'eligible'), ('1.62', 19, 'eligible'),
                                    ('1.60', 20, 'reported_drop')]:
            with self.subTest(drop=drop):
                text = A.replace('2.46 → 2.02 (−18%)', f'2.00 → {final} (−{drop}%)')
                self.assertEqual(disposition(text)[1], reason)

    def test_final_odds_inclusive(self):
        for initial, final, reason in [('1.77', '1.49', 'final_odds'), ('1.79', '1.50', 'eligible'),
                                       ('2.38', '2.00', 'eligible'), ('3.57', '3.00', 'eligible'),
                                       ('3.58', '3.01', 'final_odds')]:
            with self.subTest(final=final):
                text = A.replace('2.46 → 2.02 (−18%)', f'{initial} → {final} (−16%)')
                self.assertEqual(disposition(text)[1], reason)

    def test_rounding_precision_and_odds_direction(self):
        for prices, reason in [('2.02 → 2.02 (−18%)', 'odds_not_decreasing'),
                               ('1.96 → 2.02 (−18%)', 'odds_not_decreasing'),
                               ('2.00 → 1.71 (−15%)', 'eligible'),
                               ('2.00 → 1.69 (−15%)', 'eligible'),
                               ('2.24 → 1.87 (−16%)', 'percentage_mismatch'),
                               ('2.50 → 2.10 (−16.00%)', 'eligible'),
                               ('2.50 → 2.10 (−16.01%)', 'percentage_mismatch')]:
            with self.subTest(prices=prices):
                item, actual = disposition(A.replace('2.46 → 2.02 (−18%)', prices))
                self.assertEqual(actual, reason)
                self.assertIsInstance(item.decision.quote.calculated, Decimal)

    def test_formatted_decimal_values(self):
        text = A.replace('(0)', '(+0,25)').replace('2.46 → 2.02 (−18%)',
                            '**2,46**\u00a0→\u00a0<b>2,02</b> (**−18 %**)')
        item, reason = disposition('📉 ' + text)
        self.assertEqual(reason, 'eligible')
        self.assertEqual(item.decision.quote.reported, Decimal(18))
        self.assertEqual(item.decision.quote.final, Decimal('2.02'))

    def test_market_and_sport_exclusions(self):
        for market in ('Total over 3', 'Asian Total under 2.25', 'Team Total over 1',
                       'Over/Under', 'Result', '1X2', 'Draw', 'Double chance',
                       'Corners handicap Al Waab FC (0)', 'Cards handicap Al Waab FC (0)',
                       'Al Waab FC shots'):
            with self.subTest(market=market):
                self.assertEqual(disposition(A.replace('Handicap Al Waab FC (0)', market))[1], 'market')
        self.assertEqual(disposition(A.replace('⚽️ Football', '🏀 Basketball'))[1], 'not_football')
        self.assertEqual(disposition(A.replace('starts in 21 min', 'LIVE 43′'))[1], 'not_prematch')

    def test_ambiguity_and_missing_fields(self):
        cases = {'Al-Mu Aidar – Al Waab FC – Third club': 'ambiguous_fixture',
                 'Handicap Al Waab (0)': 'ambiguous_selection',
                 'Odds drop: Al-Mu Aidar': 'ambiguous_selection',
                 'https://scangoal.com.invalid/MySignals.ashx?go=23543': 'message_format',
                 'starts in 3 h 90 min': 'invalid_countdown',
                 'Handicap Al Waab FC (?0)': 'invalid_handicap',
                 '2.46 → (−18%)': 'invalid_handicap'}
        original = ('Al-Mu Aidar – Al Waab FC', 'Handicap Al Waab FC (0)', 'Odds drop: Al Waab FC',
                    'https://scangoal.com/MySignals.ashx?go=23543', 'starts in 21 min',
                    'Handicap Al Waab FC (0)', '2.46 → 2.02 (−18%)')
        for (replacement, reason), field in zip(cases.items(), original):
            with self.subTest(replacement=replacement):
                self.assertEqual(disposition(A.replace(field, replacement))[1], reason)
        self.assertEqual(disposition(A.replace('League 2', ' '))[1], 'message_format')

    def test_calendar_and_daylight_changes(self):
        scenarios = [
            ('2026-11-26T13:53:40Z', '1 h', '2026-11-26T14:53:40+00:00', 'eligible'),
            ('2026-11-26T13:53:40Z', '3 h 45 min', '2026-11-26T17:38:40+00:00', 'eligible'),
            ('2026-11-26T13:53:40Z', '15 h', '2026-11-27T04:53:40+00:00', 'other_day'),
            ('2026-11-26T13:53:40Z', '47 h 56 min', '2026-11-28T13:49:40+00:00', 'other_day'),
            ('2026-08-02T22:45:00Z', '21 min', '2026-08-03T00:06:00+01:00', 'other_day'),
            ('2026-03-29T00:50:00Z', '21 min', '2026-03-29T02:11:00+01:00', 'eligible'),
            ('2026-10-25T00:50:00Z', '21 min', '2026-10-25T01:11:00+00:00', 'eligible'),
        ]
        for stamp, wait, local, status in scenarios:
            with self.subTest(stamp=stamp, wait=wait):
                sent = utc(stamp)
                item, reason = disposition(A.replace('21 min', wait), sent, sent + timedelta(minutes=8))
                self.assertEqual(reason, status)
                self.assertEqual(item.decision.kickoff.astimezone(alerts.LISBON).isoformat(), local)
                if stamp == '2026-10-25T00:50:00Z':
                    self.assertIn('01:11 UTC+00:00', alerts.alert(item))

    def test_stale_exactly_at_age_or_kickoff_and_receipt_is_audited(self):
        maximum = timedelta(minutes=15)
        self.assertEqual(disposition(A, now=SENT + maximum, max_age=maximum)[1], 'eligible')
        self.assertEqual(disposition(A, now=SENT + maximum + timedelta(seconds=1), max_age=maximum)[1], 'stale')
        self.assertEqual(disposition(A, now=SENT + timedelta(minutes=21))[1], 'started')
        self.assertEqual(disposition(A, now=SENT)[1], 'invalid_metadata')

    def test_edit_cannot_reanchor_or_overwrite_original(self):
        edit = SENT + timedelta(minutes=9)
        receipt = SENT + timedelta(minutes=10)
        original = row()
        changed = row(text=A.replace('21 min', '15 min'), edited=edit, received=receipt)
        format_only = row(text=A.replace('2.46', '**2.46**'), edited=edit, received=receipt)
        for rows in ([changed], [original, changed]):
            self.assertEqual(alerts.observation(1, rows).decision.reason, 'ambiguous_edit')
        item = alerts.observation(1, [original, format_only])
        self.assertEqual(item.decision.kickoff, SENT + timedelta(minutes=21))
        self.assertEqual(item.sent, SENT)
        bad = dict(format_only, edit_date_utc=(receipt + timedelta(seconds=1)).isoformat())
        self.assertEqual(alerts.observation(1, [original, bad]).decision.reason, 'invalid_metadata')


class MemoryStore:
    """Simulates commit/ack boundaries, independently of the process being terminated."""

    def __init__(self):
        self.files = Journal(BEFORE).files()

    def read(self):
        return Journal.load(*self.files)

    def publish(self, journal):
        self.files = journal.files()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup((temp := tempfile.TemporaryDirectory()).cleanup)
        self.archive = Path(temp.name) / 'messages.jsonl'
        self.fake = Mock()
        self.store = MemoryStore()
        self.write(row())
        guard = notify.no_network()
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)

    def write(self, *rows):
        self.archive.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')

    def run_mode(self, env=ON, sender=None, clock=None):
        return alerts.run(self.archive, env, self.store, sender or self.fake, clock or (lambda: NOW))

    def test_default_and_unknown_off_never_open_store_or_archive(self):
        self.archive.unlink()
        for env in ({}, {'GOAL_ROI_DROPPING_ODDS': 'off'}, {'GOAL_ROI_DROPPING_ODDS': 'ON'}):
            with patch.object(self.store, 'read', side_effect=AssertionError):
                self.assertFalse(self.run_mode(env))
        self.fake.assert_not_called()

    def test_shadow_durable_decisions_both_percentages_and_no_send(self):
        counts = self.run_mode(SHADOW)
        self.assertEqual(counts['eligible'], 1)
        self.assertEqual([counts[f'shadow_{label}_eligible'] for label in alerts.EXPERIMENTS], [1, 0, 1, 1])
        received, eligible = self.store.read().events
        self.assertEqual(received['experiments'], {'unlimited': 'eligible', '5m': 'stale',
                                                   '10m': 'eligible', '15m': 'eligible'})
        self.assertEqual(received['latency'], {'sent_to_receipt_ms': 480000, 'receipt_to_evaluation_ms': 0,
                                              'sent_to_evaluation_ms': 480000, 'first_receipt_utc': NOW.isoformat()})
        signal = eligible['signal']
        self.assertEqual(signal['reported'], '18')
        self.assertEqual(Decimal(signal['calculated']).quantize(Decimal('.01')), Decimal('17.89'))
        self.assertFalse(self.run_mode(SHADOW))
        self.fake.assert_not_called()

    def test_shadow_thresholds_keep_independent_deduplication_across_restarts(self):
        self.run_mode(SHADOW)
        newer = row(2, A.replace('21 min', '14 min'), sent=SENT + timedelta(minutes=7),
                    received=NOW - timedelta(seconds=30))
        self.write(row(), newer)
        self.run_mode(SHADOW)
        events = self.store.read().events
        self.assertEqual(events[-2]['experiments'], {'unlimited': 'duplicate', '5m': 'eligible',
                                                     '10m': 'duplicate', '15m': 'duplicate'})
        self.assertEqual(events[-2]['latency']['receipt_to_evaluation_ms'], 30000)
        self.fake.assert_not_called()

    def test_hosted_on_is_rejected_without_opening_git_or_telegram(self):
        env = dict(ON, GITHUB_ACTIONS='true', GITHUB_REPOSITORY='nunovinhas-creator/goal-roi',
                   GITHUB_REF='refs/heads/main')
        with patch.dict(os.environ, env, clear=True), patch('sys.argv', ['alerts', 'run']), \
                patch.object(alerts, 'GitJournal', side_effect=AssertionError):
            self.assertEqual(alerts.main(), 3)
        self.fake.assert_not_called()

    def test_on_send_once_with_published_intent(self):
        def expect(token, chat, text):
            self.assertEqual(self.store.read().status, {1: 'pending'})
            self.assertEqual((token, chat), ('fake-token', 'fake-chat'))
            self.assertIn('Al Waab FC AH 0', text)
        self.fake.side_effect = expect
        self.assertEqual(self.run_mode(), {'sent': 1})
        self.assertEqual(self.store.read().status, {1: 'sent'})
        self.assertFalse(self.run_mode())
        self.fake.assert_called_once()

    def test_missing_credentials_and_explicit_age(self):
        with self.assertRaises(ValueError):
            self.run_mode(dict(ON, GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN=''))
        self.assertEqual(self.run_mode(dict(ON, TG_CHAT_ID='')), {'failed': 1})
        self.assertEqual(self.store.read().events[-1]['reason'], 'missing_credentials')
        self.fake.assert_not_called()

    def test_no_backfill_and_future_activation(self):
        later = (SENT + timedelta(minutes=1)).isoformat()
        self.assertFalse(self.run_mode(dict(ON, GOAL_ROI_DROPPING_ODDS_ON_SINCE=later)))
        future = (NOW + timedelta(minutes=1)).isoformat()
        self.assertFalse(self.run_mode(dict(ON, GOAL_ROI_DROPPING_ODDS_ON_SINCE=future)))
        self.assertFalse(self.store.read().events)
        self.fake.assert_not_called()

    def test_live_clock_rechecked_after_publication(self):
        kickoff = SENT + timedelta(minutes=21)
        times = iter([NOW, NOW, NOW, kickoff, kickoff])
        self.assertEqual(self.run_mode(dict(ON, GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN='30'),
                                       clock=lambda: next(times)), {'expired': 1})
        self.assertEqual(self.store.read().events[-1]['reason'], 'started')
        self.fake.assert_not_called()

    def test_freshness_rechecked_after_publication(self):
        late = SENT + timedelta(minutes=16)
        times = iter([NOW, NOW, NOW, late, late])
        self.assertEqual(self.run_mode(clock=lambda: next(times)), {'expired': 1})
        self.assertEqual(self.store.read().events[-1]['reason'], 'stale')
        self.fake.assert_not_called()

    def test_duplicate_telegram_edit_new_telegram_and_reused_provider_id(self):
        edit = row(text=A.replace('2.46', '2.50'), edited=SENT + timedelta(minutes=1))
        repeat = row(2, A.replace('23543', '999'))
        revised_quote = row(3, A.replace('2.46 → 2.02 (−18%)', '2.00 → 1.68 (−16%)'))
        other_match = row(4, A.replace('Al Waab FC', 'Other Club'))
        self.run_mode()
        self.write(row(), edit, repeat, revised_quote, other_match)
        self.assertEqual(self.run_mode(), {'duplicate': 2, 'late_versions': 1, 'sent': 1})
        self.assertEqual(self.fake.call_count, 2)
        log = self.store.read()
        audit = [event for event in log.events if event['status'] == 'late_versions']
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['signal']['message_rule'], 'ambiguous_edit')
        self.assertEqual(log.status[1], 'sent')
        self.assertFalse(self.run_mode())

    def test_team_order_case_and_unicode_spacing_preserve_exact_selected_team(self):
        flipped = A.replace('Al-Mu Aidar – Al Waab FC', 'al waab fc – AL-MU AIDAR')
        flipped = flipped.replace('Al Waab FC', 'AL\u00a0WAAB FC').replace('23543', '10')
        other_side = A.replace('Al Waab FC (0)', 'Al-Mu Aidar (0)').replace('drop: Al Waab FC', 'drop: Al-Mu Aidar')
        self.write(row(), row(2, flipped), row(3, other_side))
        self.assertEqual(self.run_mode(), {'sent': 2, 'duplicate': 1})
        self.assertIn('Al-Mu Aidar AH 0', self.fake.call_args.args[2])

    def test_name_aliases_and_reused_ids_across_distinct_fixtures_are_not_inferred(self):
        changed = A.replace('Al Waab FC', 'Al Waáb FC')
        self.write(row(), row(2, changed))
        self.assertEqual(self.run_mode(), {'sent': 2})

    def test_kickoff_changes_and_new_provider_aliases_do_not_chain_the_time_window(self):
        alias = A.replace('21 min', '23 min').replace('23543', '99')
        self.write(row(), row(2, alias))
        self.assertEqual(self.run_mode(), {'sent': 1, 'duplicate': 1})
        delayed = alias.replace('23 min', '25 min').replace('2.46 → 2.02 (−18%)', '2.00 → 1.68 (−16%)')
        different = alias.replace('23 min', '25 min').replace('go=99', 'go=77')
        self.write(row(), row(2, alias), row(3, delayed), row(4, different))
        self.assertEqual(self.run_mode(), {'conflicting_kickoff': 1, 'sent': 1})
        self.assertEqual(self.fake.call_count, 2)

    def test_pending_ack_lost_before_send_is_never_retried(self):
        publish = self.store.publish
        def lose_ack(journal):
            publish(journal)
            raise JournalError('ambiguous push reply')
        with patch.object(self.store, 'publish', side_effect=lose_ack), self.assertRaises(JournalError):
            self.run_mode()
        self.fake.assert_not_called()
        self.assertEqual(self.run_mode(), {'uncertain': 1})
        self.assertEqual(self.store.read().status, {1: 'uncertain'})
        self.fake.assert_not_called()

    def test_interruption_before_durable_intent_cannot_send(self):
        with patch.object(self.store, 'publish', side_effect=SystemExit), self.assertRaises(SystemExit):
            self.run_mode()
        self.fake.assert_not_called()
        self.assertFalse(self.store.read().status)
        self.assertEqual(self.run_mode(), {'sent': 1})

    def test_interruption_after_send_leaves_uncertain_and_never_retries(self):
        publish = self.store.publish
        def interrupted(journal):
            if journal.status[1] == 'sent':
                raise SystemExit
            publish(journal)
        with patch.object(self.store, 'publish', side_effect=interrupted), self.assertRaises(SystemExit):
            self.run_mode()
        self.fake.assert_called_once()
        self.assertEqual(self.run_mode(), {'uncertain': 1})
        self.fake.assert_called_once()

    def test_transport_429_timeout_and_5xx_have_no_retries(self):
        for response, state, reason in [
            (HTTPError('https://example.invalid', 429, '', None, None), 'failed', 'telegram_rate_limited'),
            (HTTPError('https://example.invalid', 503, '', None, None), 'uncertain', 'telegram_unconfirmed'),
            (TimeoutError(), 'uncertain', 'telegram_unconfirmed'),
        ]:
            with self.subTest(response=response):
                self.store = MemoryStore()
                with patch.object(notify, 'urlopen', side_effect=response) as request:
                    self.assertEqual(self.run_mode(sender=notify.send), {state: 1})
                    self.assertEqual(self.store.read().events[-1]['reason'], reason)
                    self.assertFalse(self.run_mode(sender=notify.send))
                    request.assert_called_once()

    def test_corrupt_or_missing_state_fails_closed(self):
        for corrupt in (b'', b'{bad}\n'):
            with self.subTest(corrupt=corrupt):
                self.store = MemoryStore()
                journal = self.store.read()
                journal.append(1, 'received', NOW)
                journal.append(1, 'rejected', NOW, reason='duplicate')
                state, _ = journal.files()
                self.store.files = state, corrupt
                with self.assertRaises(JournalError):
                    self.run_mode()
        self.fake.assert_not_called()

    def test_offline_replay_is_read_only_and_counts_real_format(self):
        self.write(row(), row(2, B), row(3, C),
                   row(4, A.replace('23543', '999'), sent=SENT + timedelta(seconds=2)),
                   row(5, A.replace('2.46', '2.60')))
        before = self.archive.read_bytes()
        result = alerts.replay(self.archive, timedelta(minutes=15))
        self.assertEqual((result['messages'], result['same_day'], result['same_day_fixtures']), (5, 4, 2))
        self.assertEqual((result['eligible'], result['duplicates'], result['ambiguous']), (1, 1, 2))
        self.assertEqual(result['excluded'], {'market': 1, 'other_day': 1, 'duplicate': 1, 'percentage_mismatch': 1})
        self.assertEqual(self.archive.read_bytes(), before)
        self.fake.assert_not_called()

    def test_timed_replay_does_not_use_receipt_as_evaluation(self):
        self.write(row())
        timestamp = SENT + timedelta(minutes=16)
        result = alerts.replay(self.archive, None, {1: timestamp})
        self.assertEqual(result['eligible'], 1)
        self.assertEqual(result['experiments']['15m']['stale'], 1)
        self.assertEqual(result['timings'][0]['latency']['receipt_to_evaluation_ms'], 480000)


class GitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup((temp := tempfile.TemporaryDirectory()).cleanup)
        self.path = Path(temp.name)
        self.remote = self.path / 'remote.git'
        self.archive = self.path / 'messages.jsonl'
        self.archive.write_text(json.dumps(row()) + '\n')
        self.git(self.path, 'init', '--bare', str(self.remote))
        self.stores = []
        for i in range(2):
            self.git(self.path, 'clone', str(self.remote), str(i))
            self.stores.append(GitJournal(self.path / str(i)))
        first = self.path / '0'
        self.git(first, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '--allow-empty', '-m', 'collector snapshot')
        self.git(first, 'push', 'origin', 'HEAD:refs/heads/main')
        self.stores[0].bootstrap(BEFORE)

    @staticmethod
    def git(root, *args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout

    def test_missing_or_duplicate_bootstrap_is_explicit(self):
        with self.assertRaises(JournalError):
            self.stores[1].bootstrap(BEFORE)
        snapshot = self.stores[1].read()
        snapshot.append(1, 'received', NOW)
        snapshot.append(1, 'rejected', NOW, reason='test')
        witness = self.stores[1].tip
        self.git(self.remote, 'update-ref', '-d', REF)
        with self.assertRaises(JournalError):
            self.stores[1].publish(snapshot)
        with self.assertRaises(JournalError):
            self.stores[1].read()
        self.assertEqual(self.git(self.remote, 'rev-parse', WITNESS_REF).decode().strip(), witness)
        with self.assertRaises(JournalError):
            GitJournal(self.path / '1').bootstrap(BEFORE)

    def test_single_ref_rollback_and_witness_deletion_suspend_fresh_runner(self):
        first, second = self.stores
        origin = first.tip
        sender = Mock()
        self.assertEqual(alerts.run(self.archive, ON, first, sender, lambda: NOW), {'sent': 1})
        tip = first.tip
        for ref in (REF, WITNESS_REF):
            with self.subTest(ref=ref):
                self.git(self.remote, 'update-ref', ref, origin)
                with self.assertRaises(JournalError):
                    alerts.run(self.archive, ON, second, sender, lambda: NOW)
                self.git(self.remote, 'update-ref', ref, tip)
        self.git(self.remote, 'update-ref', '-d', WITNESS_REF)
        with self.assertRaises(JournalError):
            alerts.run(self.archive, ON, second, sender, lambda: NOW)
        sender.assert_called_once()

    def test_replayed_valid_tree_on_new_commit_is_detected_by_parent_log(self):
        first, second = self.stores
        origin = first.tip
        alerts.run(self.archive, ON, first, Mock(), lambda: NOW)
        tree = self.git(self.remote, 'rev-parse', f'{origin}^{{tree}}').decode().strip()
        tip = self.git(self.remote, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, '-p', first.tip, '-m', 'restored old valid snapshot').decode().strip()
        for ref in (REF, WITNESS_REF):
            self.git(self.remote, 'update-ref', ref, tip)
        with self.assertRaisesRegex(JournalError, 'append-only'):
            second.read()

    def test_coordinated_rollback_is_detectable_only_with_newer_checkpoint(self):
        first, second = self.stores
        origin = first.tip
        sender = Mock()
        self.assertEqual(alerts.run(self.archive, ON, first, sender, lambda: NOW), {'sent': 1})
        for ref in (REF, WITNESS_REF):
            self.git(self.remote, 'update-ref', ref, origin)
        with self.assertRaisesRegex(JournalError, 'append-only'):
            first.read()
        self.assertFalse(second.read().status)
        self.assertEqual(alerts.run(self.archive, ON, second, sender, lambda: NOW), {'sent': 1})
        self.assertEqual(sender.call_count, 2)

    def test_atomic_push_rejection_changes_neither_ref_and_cannot_send(self):
        store = self.stores[0]
        before = self.git(self.remote, 'rev-parse', REF, WITNESS_REF)
        hook = self.remote / 'hooks' / 'update'
        hook.write_text(f'#!/bin/sh\n[ "$1" != "{WITNESS_REF}" ]\n', encoding='utf-8')
        hook.chmod(0o755)
        sender = Mock()
        with self.assertRaises(JournalError):
            alerts.run(self.archive, ON, store, sender, lambda: NOW)
        self.assertEqual(self.git(self.remote, 'rev-parse', REF, WITNESS_REF), before)
        sender.assert_not_called()

    def test_protected_anchor_detects_coordinated_notes_rollback(self):
        from scangoal.dropping_odds_journal import ANCHOR_REF
        first, second = self.stores
        first.guard = second.guard = lambda: None
        first.anchor(first.tip)
        origin = first.tip
        second.require_anchor = True
        sender = Mock()
        self.assertEqual(alerts.run(self.archive, ON, second, sender, lambda: NOW,
                                    before_send=second.assert_current), {'sent': 1})
        self.assertEqual(self.git(self.remote, 'rev-parse', ANCHOR_REF).decode().strip(), second.tip)
        for ref in (REF, WITNESS_REF):
            self.git(self.remote, 'update-ref', ref, origin)
        fresh = GitJournal(self.path / '0', require_anchor=True, guard=lambda: None)
        with self.assertRaises(JournalError):
            alerts.run(self.archive, ON, fresh, sender, lambda: NOW)
        sender.assert_called_once()

    def test_anchor_absent_or_policy_missing_never_sends(self):
        sender = Mock()
        fresh = GitJournal(self.path / '1', require_anchor=True, guard=lambda: None)
        with self.assertRaises(JournalError):
            alerts.run(self.archive, ON, fresh, sender, lambda: NOW)
        first = self.stores[0]
        with self.assertRaises(JournalError):
            first.anchor(first.tip)
        sender.assert_not_called()

    def test_pilot_deadline_and_expiry_after_intent(self):
        env = dict(ON, GOAL_ROI_DROPPING_ODDS_ON_SINCE=SENT.isoformat(),
                   GOAL_ROI_DROPPING_ODDS_ON_UNTIL=(NOW + timedelta(minutes=1)).isoformat())
        self.assertEqual(alerts.pilot_deadline(env, NOW), NOW + timedelta(minutes=1))
        for changes in ({'GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN': '20'},
                        {'GOAL_ROI_DROPPING_ODDS_ON_UNTIL': (NOW + timedelta(days=1)).isoformat()}):
            with self.assertRaises(ValueError):
                alerts.pilot_deadline(dict(env, **changes), NOW)
        sender = Mock()
        self.assertEqual(alerts.run(self.archive, ON, self.stores[0], sender, lambda: NOW,
                                    deadline=NOW), {'expired': 1})
        sender.assert_not_called()

    def test_concurrent_runners_only_one_claims_and_main_is_untouched(self):
        barrier = threading.Barrier(2)
        before = self.git(self.remote, 'for-each-ref', 'refs/heads')
        sender = Mock()

        def worker(store):
            original_publish = store.publish
            def publish(journal):
                if journal.status.get(1) == 'pending':
                    barrier.wait(timeout=5)
                original_publish(journal)
            with patch.object(store, 'publish', side_effect=publish):
                try:
                    return alerts.run(self.archive, ON, store, sender, lambda: NOW)
                except JournalError:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(worker, self.stores)
        self.assertIn({'sent': 1}, (a, b))
        self.assertIn(None, (a, b))
        sender.assert_called_once()
        self.assertEqual(self.stores[0].read().status, {1: 'sent'})
        self.assertEqual(self.git(self.remote, 'for-each-ref', 'refs/heads'), before)


if __name__ == '__main__':
    unittest.main()

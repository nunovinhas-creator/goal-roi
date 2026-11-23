"""Fixture identity of notices: the same bet is never notified twice, distinct fixtures are never merged by names.

Regression for PR #74: `legacy_hit` matched every `watch_sent` entry by team names and selection within three days, so
a second fixture between the same teams, or the same selection in another market, was silently suppressed."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scangoal import match_identity, notify, polymarket_alerts

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
MIGRATION = NOW - timedelta(days=7)
ITEM = 'item|id:%s|Asian Total|Over 2.5'


def iso(minutes=0):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def state(watch_sent=(), notified=None, migration=True):
    migrations = {notify.STATUS_MIGRATION: {'at_utc': MIGRATION.isoformat()},
                  polymarket_alerts.ACTIVATION: {'at_utc': MIGRATION.isoformat()}}
    if migration:
        migrations[notify.FORMAT_MIGRATION] = {'at_utc': MIGRATION.isoformat(), 'sent_total': 0}
    return {'sent_total': len(watch_sent), 'watch_sent': sorted(watch_sent), 'statuses': {}, 'hello': True,
            'notified_items': dict(notified or {}), 'migrations': migrations}


def watch(match_id, *, market='Asian Total', selection='Over 2.5', minute=-5):
    return {'date_utc': iso(minute), 'match': 'Alfa FC — Beta FC', 'match_id': match_id, 'canonical_match_id': match_id,
            'type': 'BETFAIR', 'phase': 'pre', 'market': market, 'selection': selection, 'odds': 1.9,
            'kickoff_utc': iso(30), 'period': 'regulation', 'finished': False,
            'reasons': [{'id': 'h1', 'status': 'promissora', 'certified': False}]}


def sends(st, *items):
    memory, texts = notify.MemoryStore(st), []
    notify.deliver(memory, {'generated_at_utc': NOW.isoformat(), 'brain': {'watch': list(items)}}, memory.load_state(),
                   NOW, lambda *a: texts.append(a[-1]), 't', 'c', publish=lambda _: True, clock=lambda: NOW)
    return texts, memory.load_state()


def entry(minutes_ago, selection='Over 2.5'):
    return '%s|Alfa FC — Beta FC|%s' % (iso(-minutes_ago), selection)


class NoticeIdentity(unittest.TestCase):
    def test_second_fixture_between_the_same_teams_two_days_later_is_notified(self):
        st = state([entry(2 * 24 * 60)], {ITEM % 'alfa-vs-beta-20261121': {'at_utc': iso(-2 * 24 * 60)}})
        self.assertEqual(len(sends(st, watch('alfa-vs-beta-20261123'))[0]), 1)

    def test_same_selection_in_another_market_of_the_same_fixture_is_notified(self):
        st = state([entry(30)], {ITEM % 'alfa-vs-beta-20261123': {'at_utc': iso(-30)}})
        self.assertEqual(len(sends(st, watch('alfa-vs-beta-20261123', market='Goal Line'))[0]), 1)

    def test_edited_or_republished_signal_of_the_same_bet_is_not_resent(self):
        first, st = sends(state(), watch('alfa-vs-beta-20261123', minute=-20))
        again, _ = sends(st, watch('alfa-vs-beta-20261123', minute=-3))
        self.assertEqual((len(first), len(again)), (1, 0))

    def test_notices_from_before_item_identities_still_block_by_names(self):
        old = (MIGRATION - timedelta(hours=1)).isoformat()
        st = state(['%s|Alfa FC — Beta FC|Over 2.5' % old])
        with patch.object(notify, 'still_bettable', lambda *_: True):
            texts, _ = sends(st, watch('alfa-vs-beta-x', minute=-(7 * 24 * 60 - 60)))
        self.assertEqual(texts, [])

    def test_without_a_readable_migration_every_entry_stays_legacy(self):
        st = state([entry(60)], migration=False)
        self.assertEqual(sends(st, watch('alfa-vs-beta-20261123'))[0], [])
        st = state([entry(60)])
        st['migrations'][notify.FORMAT_MIGRATION]['at_utc'] = 'not a date'
        self.assertEqual(notify.legacy_sent(st), {entry(60)})

    def test_legacy_set_is_split_at_the_migration(self):
        before, after = (MIGRATION - timedelta(minutes=1)).isoformat(), (MIGRATION + timedelta(minutes=1)).isoformat()
        st = state(['%s|A — B|1' % before, '%s|A — B|1' % after])
        self.assertEqual(notify.legacy_sent(st), {'%s|A — B|1' % before})

    def test_competitions_and_similar_names_stay_separate_fixtures(self):
        def sig(mid, home, away, league, n):
            return {'match_id': mid, 'teams': [home, away], 'league': league, 'kickoff_utc': iso(30),
                    'date_utc': iso(-10), 'signal_seen_utc': iso(-9), 'message_id': n, 'phase': 'pre',
                    'bet': True, 'in_roi': True}

        rows = [sig('a', 'Real Madrid', 'Barcelona', 'La Liga', 1), sig('b', 'Real Madrid', 'Barcelona', 'Liga F', 2),
                sig('c', 'Real Madrid Castilla', 'Barcelona B', 'Segunda', 3)]
        self.assertEqual([r['canonical_match_id'] for r in match_identity.resolve(rows, NOW)[0]], ['a', 'b', 'c'])

    def test_a_stake_claim_blocks_the_general_notice_of_the_same_bet(self):
        memory, texts = notify.MemoryStore(state()), []
        notify.deliver(memory, {'generated_at_utc': NOW.isoformat(), 'brain': {'watch': [watch('fx')]}},
                       memory.load_state(), NOW, lambda *a: texts.append(a), 't', 'c', publish=lambda _: True,
                       clock=lambda: NOW, excluded_items={ITEM % 'fx'})
        self.assertEqual(texts, [])

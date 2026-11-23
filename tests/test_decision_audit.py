"""Adversarial integration tests for the offline decision audit."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import chronology, decision_audit, opportunity_forward, report, strategy_board
from scangoal.decision_audit_provenance import Registration
from test_chronological_validity import T0, at, hyp, sig
from test_opportunity_forward import ACTIVATION, PROTOCOL, result, signal


ROOT = Path(__file__).resolve().parents[1]


def when(minutes):
    return T0 + timedelta(minutes=minutes)


def version(bet):
    stamp = bet['signal_seen_utc']
    return {'first_read_utc': stamp, 'published_utc': bet['date_utc'],
            'text_sha256_matches_journal': True, 'receipt_quality': 'completed_page'}


def journal_row(message_id, minute, *, observed, edit=None, price=1.9, score=None):
    lines = ["🔥 Open game", "Example League · 50'", "Alpha 1", "Beta 0",
             "Rhythm 95/100 · Over 97%", f"💰 O 2.5: {price}", "https://scangoal.com/match/test-fixture"]
    if score is not None:
        lines.append(f"🏁 FT {score[0]}:{score[1]}")
    message = '\n'.join(lines)
    return {'message_id': message_id, 'text': message, 'date_utc': at(minute),
            'edit_date_utc': at(edit) if edit is not None else None, 'collected_at_utc': at(observed),
            'receipt_version': 1, 'sha256': hashlib.sha256(message.encode()).hexdigest()}


class TemporalDecisions(unittest.TestCase):
    def compare(self, rows, *, reg=None):
        hypothesis = hyp(conditions=(), born=at(-30))
        reg = reg or Registration(when(-30), when(-30), when(-30))
        return decision_audit.compare_hypothesis('h', hypothesis, reg, rows, rows,
                                                 {b['message_id']: version(b) for b in rows}, when(60))

    def test_minute_89_and_missing_prematch_kickoff(self):
        rows = [dict(sig('live', None, 0, message_id=1, seen=1), minute=89),
                sig('pre', None, 0, message_id=2, phase='pre', canonical_match_id='pre')]
        rows[0]['canonical_match_id'] = 'live'
        out = self.compare(rows)
        self.assertEqual(out['comparison']['admission']['current_only'], 2)
        self.assertEqual(out['current']['reported_economics']['admitted'], 2)
        self.assertEqual(out['current']['reconciled']['adverse']['roi'], -1)
        self.assertEqual(out['proposed']['admitted'], 0)
        decisions = {row['fixture_id']: row for row in out['decisions']}
        self.assertIn('live_window_may_reach_regulation_end', decisions['live']['difference_reasons'])
        self.assertIn('pre_kickoff_unproven', decisions['pre']['difference_reasons'])

    def test_declared_timestamp_before_git_proof_does_not_open_proposed_window(self):
        bet = sig('live', None, 0, message_id=1, canonical_match_id='live')
        reg = Registration(when(-30), when(2), when(2), flags=('registration_claim_precedes_git',))
        out = self.compare([bet], reg=reg)
        self.assertEqual(out['comparison']['admission']['current_only'], 1)
        self.assertIn('registration_claim_precedes_git', out['registration']['flags'])
        self.assertIn('signal_not_strictly_after_registration', out['decisions'][0]['difference_reasons'])

    def test_first_half_market_has_its_own_boundary(self):
        bet = dict(sig('live', None, 0, message_id=1, market='1st Half - Asian Total'), minute=40)
        out = self.compare([bet])
        self.assertEqual(out['comparison']['admission']['current_only'], 1)
        self.assertIn('live_window_may_reach_first_half_end', out['decisions'][0]['difference_reasons'])

    def test_simultaneous_contracts_keep_current_tie_break(self):
        rows = [sig('live', None, 0, message_id=1, selection='Over 3.5'),
                sig('live', None, 0, message_id=2, selection='Over 2.5')]
        out = self.compare(rows)
        item = out['decisions'][0]
        self.assertEqual(item['current']['message_id'], 2)
        self.assertEqual(item['proposed'], item['current'])

    def test_frozen_pr55_keeps_first_quote_cutoff_and_settlement(self):
        before = signal(message_id=1, date_utc='2026-11-19T15:18:00Z', collected_at_utc='2026-11-19T15:19:00Z')
        first = signal(message_id=2)
        later = signal(message_id=3, date_utc='2026-11-19T15:39:00Z', collected_at_utc='2026-11-19T15:40:00Z', odds=5.0)
        inputs = report.BetInputs([before, first, later], [], {}, {}, {first['match_id']: [result()]}, {})
        clock = chronology.parse_utc('2026-11-20T12:00:00Z')
        receipt = version(dict(first, signal_seen_utc=first['collected_at_utc']))
        out = decision_audit._pr55(inputs, {2: receipt}, clock, PROTOCOL, ACTIVATION)
        current = opportunity_forward.settled_statistics(
            opportunity_forward.select_signals(inputs.messages, chronology.parse_utc(ACTIVATION), clock)[0],
            inputs.observations, clock)[0]
        self.assertEqual(out['current']['reported_economics'], current)
        entry = out['decisions'][0]
        self.assertEqual(entry['current'], entry['proposed'])
        self.assertEqual(entry['current']['message_id'], 2)
        self.assertEqual(entry['signals'][0]['time_window']['decision_by_utc'], '2026-11-19T15:51:00+00:00')

    def test_replay_ignores_future_edit_alias_and_result(self):
        early = journal_row(1, 0, observed=1)
        late = journal_row(2, 10, observed=11)
        future_edit = journal_row(2, 10, observed=82, edit=81, price=3.8, score=[1, 2])
        correction = journal_row(2, 10, observed=102, edit=101, price=5, score=[1, 1])
        future_collision = journal_row(3, 110, observed=112)
        future_collision['text'] = future_collision['text'].replace('Alpha', 'Gamma')
        future_collision['sha256'] = hashlib.sha256(future_collision['text'].encode()).hexdigest()
        hypothesis = hyp(conditions=(), born=at(5))
        reg = Registration(when(5), when(6), when(6), entry=hypothesis)
        with TemporaryDirectory() as directory, patch.dict('os.environ', {'SCANGOAL_DATA_DIR': directory}),                 patch.object(decision_audit, 'registration', return_value=reg):
            journal = Path(directory) / 'messages.jsonl'
            journal.write_text(''.join(json.dumps(row) + '\n' for row in (early, late)), encoding='utf-8')
            def replay(clock):
                return decision_audit.build(clock, root=ROOT, hypotheses=('h',))
            first = replay(when(40))
            self.assertFalse(first['shortlist_visible_at_cutoff'])
            self.assertFalse(any(c['scope'] == 'frozen_pr76' for c in first['cohorts']))
            with journal.open('a', encoding='utf-8') as stream:
                for row in (future_edit, correction, future_collision):
                    stream.write(json.dumps(row) + '\n')
            self.assertEqual(replay(when(40)), first)
            settled = replay(when(84))['cohorts'][-1]['decisions'][0]
            revised = replay(when(104))['cohorts'][-1]['decisions'][0]

        view = first['cohorts'][-1]
        item = view['decisions'][0]
        self.assertEqual(view['comparison']['admission']['proposed_only'], 1)
        self.assertEqual(item['current_commitment_message_id'], 1)
        self.assertIn('earlier_signal_then_eligible_signal', item['difference_reasons'])
        self.assertEqual(item['proposed']['message_id'], 2)
        self.assertEqual(item['signals'][1]['signal_version']['journal_line'], 2)
        self.assertEqual(item['proposed']['outcome']['state'], 'pending')
        self.assertEqual(view['proposed']['adverse']['roi'], -1)
        self.assertEqual(settled['proposed']['outcome']['confirmed_payoff'], 1)
        self.assertEqual(revised['proposed']['outcome']['confirmed_payoff'], -1)
        self.assertEqual(settled['signals'], item['signals'])
        self.assertEqual(revised['signals'], item['signals'])

    def test_frozen_pr76_proof_is_unchanged(self):
        data = strategy_board.load_shortlist()
        start = strategy_board.shortlist_evidence_start(data)
        entry = next(entry for entry in data['entries'] if entry.get('conditions'))
        hypothesis = dict(entry, source='shortlist', conditions=[])
        previous = (start - timedelta(minutes=1)).isoformat()
        following = (start + timedelta(minutes=1)).isoformat()
        rows = [sig('first', None, 0, message_id=1, date_utc=previous, signal_seen_utc=previous),
                sig('second', None, 0, message_id=2, date_utc=following, signal_seen_utc=following)]
        reg = Registration(chronology.parse_utc(data['registered_utc']), start, start,
                           proof=strategy_board.SHORTLIST_PROOF)
        view = decision_audit.compare_hypothesis(entry['id'], hypothesis, reg, rows, rows,
                                                  {b['message_id']: version(b) for b in rows},
                                                  start + timedelta(hours=2), frozen=True)
        self.assertEqual(view['registration']['effective_utc'], '2026-11-23T14:40:13+00:00')
        self.assertEqual(view['current']['reported_economics']['admitted'], 1)
        self.assertIsNone(view['decisions'][0]['proposed'])
        self.assertEqual(view['decisions'][1]['proposed']['message_id'], 2)

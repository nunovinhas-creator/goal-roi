"""P1 regressions: exploratory winners cannot change operational conclusions."""
import copy
import json
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

from scangoal import insights, notify, report
import test_scangoal as fixtures


class OperationalEvidence(unittest.TestCase):
    def population(self):
        fixture = fixtures.EvidencePerMatch()
        def row(i, win, day, valid=True):
            return dict(fixture.row(str(i), 1 if win else -1, day=day),
                        in_roi=valid, ft=[2, 1], settled_by='resultado')
        valid = [row(i, i % 2 == 0, 1 + i // 40) for i in range(80)]
        invalid = [row(80 + i, True, 3, False) for i in range(40)]
        return valid, invalid

    def test_ineligible_wins_change_only_exploratory_statistics(self):
        valid, invalid = self.population()
        before, after = report.operational_evidence(valid), report.operational_evidence(valid + invalid)
        self.assertEqual(before, after)
        self.assertEqual(insights.regime(valid)[0]['state'], 'estável')
        self.assertEqual(insights.regime(valid + invalid)[0]['state'], 'melhorou')
        self.assertNotEqual(report.accuracy(valid), report.accuracy(valid + invalid))

    def test_report_build_exposes_independent_eligible_population(self):
        fixture = fixtures.Hardening()
        first = fixture.row(1, fixtures.BETFAIR_PRE, '2026-11-11T10:00:00+00:00')
        invalid = fixture.row(2, fixtures.BETFAIR_PRE.replace('barnsley-vs-oldham', 'other-vs-team'), '2026-11-11T11:00:00+00:00')
        invalid['edit_date_utc'] = '2026-11-11T10:30:00+00:00'
        rep, _ = fixture.build([first, invalid])
        self.assertEqual(rep['operational']['scope'], 'eligible_bets_only')
        self.assertEqual(sum(g['signals'] for g in rep['accuracy'].values()), 2)
        self.assertEqual(sum(g['signals'] for g in rep['operational']['accuracy'].values()), 1)
        self.assertIsNot(rep['accuracy'], rep['operational']['accuracy'])

    def test_ineligible_wins_do_not_create_behavior_alerts(self):
        valid, invalid = self.population()
        now = datetime.fromisoformat('2026-11-14T12:00:00+00:00')
        state = {'hello': True}
        def rep(rows):
            return {'operational': report.operational_evidence(rows), 'insights': {'regime': insights.regime(rows)}}
        self.assertEqual(notify.messages(rep(valid), state, now), [])
        self.assertEqual(notify.messages(rep(valid + invalid), state, now), [])
        # Identical wins with valid provenance must still produce the existing alert.
        eligible_wins = [dict(b, in_roi=True) for b in invalid]
        alerts = notify.messages(rep(valid + eligible_wins), state, now)
        self.assertEqual(len(alerts), 1)
        self.assertIn('Mudança de comportamento (evidência elegível)', alerts[0])
        self.assertIn('melhorou', alerts[0])
        self.assertEqual(notify.messages(rep(valid + eligible_wins), state, now), [])

    def test_missing_or_wrong_scope_never_falls_back_or_changes_state(self):
        now = datetime.fromisoformat('2026-11-14T12:00:00+00:00')
        changed = {'type': 'OPEN_GAME', 'state': 'melhorou', 'before': {'hit_rate': .5}, 'recent': {'hit_rate': 1}}
        for operational in (None, {'regime': [changed]}, {'scope': 'all_signals', 'regime': [changed]}):
            state = {'hello': True, 'regime': {'OPEN_GAME': 'piorou'}, 'operational_regime': {'OPEN_GAME': 'estável'}}
            old = copy.deepcopy(state)
            self.assertEqual(notify.messages({'insights': {'regime': [changed]}, 'operational': operational}, state, now), [])
            self.assertEqual(state['regime'], old['regime'])
            self.assertEqual(state['operational_regime'], old['operational_regime'])

    def test_migration_seeds_silently_and_preserves_legacy_history(self):
        valid, invalid = self.population()
        state = {'hello': True, 'regime': {'OPEN_GAME': 'piorou'}, 'watch_sent': ['old-notice']}
        rep = {'operational': report.operational_evidence(valid + [dict(b, in_roi=True) for b in invalid])}
        self.assertEqual(notify.messages(rep, state, datetime.fromisoformat('2026-11-14T12:00:00+00:00')), [])
        self.assertEqual(state['regime'], {'OPEN_GAME': 'piorou'})
        self.assertEqual(state['operational_regime'], {'OPEN_GAME': 'melhorou'})
        self.assertEqual(state['watch_sent'], ['old-notice'])

    def test_real_dashboard_answer_ignores_exploratory_statistics(self):
        valid, invalid = self.population()
        data = {'operational': report.operational_evidence(valid), 'accuracy': report.accuracy(valid),
                'min_settled': 300, 'messages': 80, 'unique_bets': 80, 'settled_bets': 80}
        result = subprocess.run(['node', 'tests/dashboard_operational.cjs'], input=json.dumps(data), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

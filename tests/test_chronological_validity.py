"""PR 3: chronological validity, prospective evidence and multiple testing under dependence (offline, deterministic).

Every fixture states its own provenance: when the system read each signal (signal_seen_utc) and when it first observed each
result (result_known_utc). Nothing here touches data/, the network or Telegram.
"""
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from scangoal import brain, chronology, insights, notify, store

UTC = timezone.utc
T0 = datetime(2026, 11, 20, tzinfo=UTC)
EARLY = '2026-01-01T00:00:00+00:00'


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec='seconds')


def sig(match, w, minute, *, seen=0, known=145, typ='OPEN_GAME', market='Asian Total', selection='Over 2.5', odds=2.0, **extra):
    """Eligible signal sent at T0+minute, read `seen` min later; result first observed `known` min after the signal."""
    d = at(minute)
    return dict({'match_id': match, 'w': w, 'odds': odds, 'type': typ, 'phase': 'live', 'market': market, 'selection': selection,
                 'date_utc': d, 'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga', 'teams': ['A', 'B'],
                 'in_roi': True, 'key': (match, market, selection, d), 'n_signals': 1, 'max_mult': 1, 'no_source': False,
                 'share': None, 'total': None, 'n_bets': None,
                 'signal_seen_utc': None if seen is None else at(minute + seen),
                 'result_known_utc': None if w is None or known is None else at(minute + known)}, **extra)


def hyp(born=EARLY, family='Jogo aberto', conditions=(('Minuto', '31 a 60'),), source='gerador'):
    """A tradable hypothesis (every fixture signal is at minute 50); conditions=() is the whole family (walk-forward skips it)."""
    return {'family': family, 'conditions': [list(c) for c in conditions], 'born_utc': born, 'source': source}


def series(outcomes, start=0, every=60, prefix='g', **kw):
    """One signal per game, every `every` minutes."""
    return [sig('%s%d' % (prefix, start + i), w, (start + i) * every, **kw) for i, w in enumerate(outcomes)]


def rows_by_id(signals, registry):
    rows, tested, bar = brain.evaluate(signals, registry, 300)
    return {r['id']: r for r in rows}, tested, bar


SEVEN_IN_TEN = ([1.0, 1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0, 1.0, -1.0] * 11)[:102]   # tail ~5e-5 at 100 games: passes m=1, not m=401


def follows(wf):
    return wf['followed']['bets']


class ResultTiming(unittest.TestCase):
    def test_result_observed_after_the_decision_cannot_influence_it(self):
        reg = {'h': hyp()}
        decision = sig('d', None, 12 * 60)
        # Ten wins played early; the system only observed them 15 h after each signal (e.g. a late result source).
        late = series([1.0] * 10, known=15 * 60)
        self.assertEqual(follows(brain.walk_forward(late + [decision], reg)), 0)
        # The same wins observed 145 min after each signal are known at the decision: it follows.
        soon = series([1.0] * 10, known=145)
        self.assertEqual(follows(brain.walk_forward(soon + [decision], reg)), 1)
        # The PR 2 approximation (150 min after the signal) would have called the late results "known": that was leakage.
        self.assertTrue(all(chronology.result_known(b) > chronology.decision_time(decision) for b in late))

    def test_replay_follows_proven_observation_order_not_game_order(self):
        # Games played in order g0..g39, but the system observed the first 30 only after the last 10.
        first = series([1.0] * 30, known=5000)
        last = series([-1.0] * 10, start=30, known=145)
        steps = brain.game_steps(first + last)
        self.assertEqual([s[0] for s in steps], list(range(30, 41)))
        known = [s[2] for s in steps]
        self.assertEqual(known, sorted(known))                                   # chronological by observation
        # Observed first: the 10 losses, then the wins. The 30th observed game is the 20th win, not game g29.
        self.assertEqual(steps[0][3]['at'], chronology.result_known(first[19]))
        self.assertEqual(steps[0][1], insights.summarize(last + first[:20])['prob_above'])

    def test_a_game_is_known_only_when_all_its_bets_are(self):
        g = [sig('m', 1.0, 0, market='1X2', selection='1', known=100), sig('m', 1.0, 1, market='BTTS', selection='Yes', known=900)]
        games = brain.ordered_games(g)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0][0], chronology.result_known(g[1]))          # the last of its results


class UnknownOrContradictoryTimes(unittest.TestCase):
    def test_unknown_time_is_never_presumed_known(self):
        reg = {'h': hyp()}
        wins = series([1.0] * 12, known=None)                                      # result exists, no proof of when
        wf = brain.walk_forward(wins + [sig('d', None, 50 * 60)], reg)
        self.assertEqual(follows(wf), 0)
        self.assertEqual(wf['chronology']['results_unknown_time'], 12)
        for b in wins:
            self.assertEqual(chronology.result_check(b), (None, 'unknown'))

    def test_malformed_naive_or_contradictory_times_fail_closed(self):
        base = sig('m', 1.0, 600)
        for bad in ('garbage', '', 7, '2026-11-20T12:00:00', None):              # naive: no presumed timezone
            self.assertEqual(chronology.result_check(dict(base, result_known_utc=bad))[1], 'unknown', bad)
        self.assertEqual(chronology.result_check(dict(base, result_known_utc=at(500)))[1], 'contradictory')   # before the signal
        ko = dict(base, kickoff_utc=at(900), result_known_utc=at(700))                                       # before kick-off
        self.assertEqual(chronology.result_check(ko)[1], 'contradictory')
        self.assertEqual(chronology.result_check(base)[1], 'verified')
        self.assertIsNone(chronology.signal_seen(dict(base, signal_seen_utc=at(590))))      # read 10 min before it was sent
        self.assertIsNotNone(chronology.signal_seen(dict(base, signal_seen_utc=at(599))))   # stamp at the start of the read

    def test_unknown_results_count_now_but_after_every_proven_one(self):
        proven = series([1.0] * 30, known=145)
        unknown = series([-1.0] * 5, start=30, known=None)
        rows, _, _ = rows_by_id(proven + unknown, {'h': hyp()})
        self.assertEqual(rows['h']['games'], 35)                                          # current evidence is complete
        self.assertEqual(rows['h']['result_time_unknown'], 5)
        steps = brain.game_steps(proven + unknown)
        self.assertEqual([s[2] is None for s in steps], [False] + [True] * 5)             # never placed in history
        self.assertEqual(rows['h']['prob_above'], insights.summarize(proven + unknown)['prob_above'])


class HypothesisBirth(unittest.TestCase):
    def test_hypothesis_registered_after_a_signal_cannot_decide_it(self):
        wins = series([1.0] * 10)
        decision = sig('d', None, 20 * 60)
        before = {'h': hyp(born=at(20 * 60 + 30))}                                     # registered 30 min after the decision
        after = {'h': hyp(born=at(19 * 60))}
        self.assertEqual(follows(brain.walk_forward(wins + [decision], before)), 0)
        self.assertEqual(follows(brain.walk_forward(wins + [decision], after)), 1)

    def test_pre_registration_evidence_is_one_step_at_birth_and_needs_confirmation(self):
        history = series([1.0] * 40)                                                   # all known before registration
        born = at(100 * 60)
        rows, _, _ = rows_by_id(history, {'h': hyp(born=born)})
        r = rows['h']
        self.assertGreaterEqual(r['prob_above'], brain.PROMOTE_AT)
        self.assertEqual((r['status'], r['pending_status']), ('em observação', 'promissora'))   # never decided before birth
        self.assertEqual(r['at_birth']['games'], 40)
        self.assertEqual(r['evidence_level'], 'exploratória')
        one_more = history + [sig('after', 1.0, 101 * 60)]
        r = rows_by_id(one_more, {'h': hyp(born=born)})[0]['h']
        self.assertEqual(r['status'], 'promissora')
        self.assertGreaterEqual(r['status_changes'][0]['known_utc'], born)                 # dated after registration

    def test_hypothesis_added_later_never_rewrites_fixed_decisions(self):
        own = series(SEVEN_IN_TEN)                                                     # 102 games, validated with m = 1
        base = {'a': hyp()}
        rows1, _, _ = rows_by_id(own, base)
        self.assertEqual(rows1['a']['status'], 'forte')
        # 400 hypotheses registered long after a's last game, on games of another family played afterwards.
        later = [sig('s%d' % i, 1.0 if i % 2 else -1.0, 10000 + i * 60, typ='STAKE_LOAD') for i in range(30)]
        crowd = dict(base, **{'s%d' % k: hyp(born=at(9000), family='Stake') for k in range(400)})
        rows2, tested2, _ = rows_by_id(own + later, crowd)
        self.assertEqual(tested2, 401)
        self.assertEqual(rows1['a']['status_changes'], rows2['a']['status_changes'])
        self.assertEqual(rows1['a']['status'], rows2['a']['status'])
        wf1 = brain.walk_forward(own, base)
        wf2 = brain.walk_forward(own, crowd)
        self.assertEqual((wf1['followed'], wf1['skipped']), (wf2['followed'], wf2['skipped']))

    def test_missing_or_malformed_birth_never_decides(self):
        wins = series([1.0] * 40)
        for born in (None, '', 'ontem', '2026-11-01T00:00:00', 12):
            h = hyp()
            h['born_utc'] = born
            rows, _, _ = rows_by_id(wins, {'h': h})
            r = rows['h']
            self.assertEqual((r['status'], r['pending_status'], r['born_verified'], r['evidence_level']),
                             ('em observação', None, False, 'não verificável'), born)
            wf = brain.walk_forward(wins + [sig('d', None, 60 * 60)], {'h': h})
            self.assertEqual((follows(wf), wf['chronology']['hypotheses_without_valid_birth']), (0, 1))
        llm = dict(hyp(source='llm'), born_utc='not a date')
        self.assertEqual(rows_by_id(wins, {'h': llm})[0]['h']['games'], 0)                # nothing counts without a birth


class ChronologicalBar(unittest.TestCase):
    def own(self):
        return series(SEVEN_IN_TEN)

    def test_bar_is_the_one_in_force_at_each_decision(self):
        rows, tested, bar = rows_by_id(self.own(), {'a': hyp()})
        change = rows['a']['status_changes'][-1]
        self.assertEqual((rows['a']['status'], change['tested']), ('forte', 1))
        self.assertEqual(change['bar'], round(brain.corrected_bar(1, change['games']), 6))

    def test_looks_are_paid_for(self):
        # α is spent over the looks n >= STRONG_AT: STRONG_AT/(n(n+1)) telescopes to exactly 1.
        last = 200000
        total = sum(brain.look_share(n) for n in range(brain.STRONG_AT, last))
        self.assertAlmostEqual(total, 1 - brain.STRONG_AT / last, places=9)            # partial sums < 1, limit 1
        self.assertGreater(brain.corrected_bar(1, 300), brain.corrected_bar(1, 100))

    def test_a_family_that_already_existed_does_apply(self):
        # 400 hypotheses with 30 games known BEFORE a's games: they were the family at a's decisions.
        own = [dict(b, date_utc=at(5000 + i * 60), signal_seen_utc=at(5000 + i * 60), result_known_utc=at(5000 + i * 60 + 145))
               for i, b in enumerate(self.own())]
        earlier = [sig('s%d' % i, 1.0 if i % 2 else -1.0, i * 60, typ='STAKE_LOAD') for i in range(30)]
        reg = dict({'a': hyp()}, **{'s%d' % k: hyp(family='Stake') for k in range(400)})
        rows, tested, _ = rows_by_id(own + earlier, reg)
        self.assertEqual((tested, rows['a']['status']), (401, 'promissora'))

    def test_family_timeline(self):
        t = [T0 + timedelta(hours=h) for h in (1, 2, 2, 5)]
        f = brain.family_timeline(t + [None])
        self.assertEqual(f(T0)[1], 0)
        self.assertEqual(f(T0 + timedelta(hours=2))[1], 3)
        self.assertEqual(f(T0 + timedelta(hours=9))[1], 4)
        self.assertEqual(f(None), (brain.corrected_bar(5), 5))                              # "now": everyone, unknown included


class ProspectiveGate(unittest.TestCase):
    def test_historical_evidence_alone_never_validates(self):
        history = series([1.0] * 90 + [-1.0] * 30)                                      # 120 games, p ~ 1, all before birth
        born = at(200 * 60)
        r = rows_by_id(history + [sig('x', 1.0, 201 * 60)], {'h': hyp(born=born)})[0]['h']
        self.assertEqual(r['status'], 'promissora')
        self.assertEqual(r['prospective']['games'], 1)
        self.assertNotIn(r['entry_status'], brain.VALIDATED)

    def test_prospective_confirmation_validates(self):
        history = series([1.0] * 90 + [-1.0] * 30)
        after = series([1.0] * 24 + [-1.0] * 8, start=200)                            # 32 prospective games, p ~ 0.998
        r = rows_by_id(history + after, {'h': hyp(born=at(150 * 60))})[0]['h']
        self.assertEqual((r['status'], r['evidence_level']), ('forte', 'prospetiva'))
        last = r['status_changes'][-1]
        self.assertGreaterEqual(last['prospective_games'], brain.PROSPECTIVE_MIN)

    def test_weak_prospective_evidence_blocks_validation(self):
        history = series([1.0] * 90 + [-1.0] * 30)
        after = series([1.0, -1.0] * 16, start=200)                                    # 32 prospective games at break-even
        r = rows_by_id(history + after, {'h': hyp(born=at(150 * 60))})[0]['h']
        h, m, be, n = insights.clustered(history + after)
        self.assertLess(insights.tail_below(h, m, be), 1 - brain.corrected_bar(1, n))        # the pooled history passes the bar
        self.assertNotIn(r['status'], brain.VALIDATED)

    def test_rounded_probability_never_passes_a_stricter_bar(self):
        # 102 games at 7/10: prob_above rounds to 0.9999 or 1.0, but the true tail (~3e-5) is far above 1.2e-6.
        h, m, be, n = insights.clustered(series(SEVEN_IN_TEN))
        bar = brain.corrected_bar(401, n)
        self.assertGreaterEqual(insights.evidence(h, m, be)['prob_above'], 0.9999)
        self.assertFalse(brain.passes_bar(insights.evidence(h, m, be)['prob_above'], insights.tail_below(h, m, be), bar))


class SameGameIsOneUnit(unittest.TestCase):
    def test_signals_of_one_game_are_one_step_and_one_walk_forward_game(self):
        same = [sig('m%d' % g, 1.0, g * 60 + j, market=mk, selection='x', known=145 + j, typ='POLYMARKET')
                for g in range(6) for j, mk in enumerate(('1X2', 'BTTS', 'Asian Total'))]
        self.assertEqual(len(brain.ordered_games(same)), 6)
        poly = {'h': hyp(family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        decision = sig('d', None, 20 * 60, typ='POLYMARKET', market='1X2', selection='x')
        wf = brain.walk_forward(same + [decision], poly)
        self.assertEqual(follows(wf), 0)                                                 # 18 signals, 6 games < BOARD_MIN
        more = [sig('n%d' % g, 1.0, 600 + g * 60, typ='POLYMARKET', market='1X2', selection='x') for g in range(2)]
        self.assertEqual(follows(brain.walk_forward(same + more + [decision], poly)), 1)   # control: 8 games are enough


class OverlappingHypotheses(unittest.TestCase):
    def test_overlap_is_reported_and_never_relaxes_the_bar(self):
        g = series([1.0] * 30 + [-1.0] * 6 + [1.0, 1.0])
        idx = brain.earlier_index(g)
        common = sorted(set.intersection(*(set(brain.atoms(dict(b), set(), idx)) for b in g)) - {('Dia', 'fim de semana'), ('Dia', 'dia de semana')})
        picks = [[], [common[0]], [common[1]], [common[0], common[1]]]
        reg = {'h%d' % i: hyp(conditions=c) for i, c in enumerate(picks)}
        rows, tested, bar = rows_by_id(g, reg)
        self.assertEqual(len({r['game_set'] for r in rows.values()}), 1)               # the same evidence four times
        self.assertEqual((tested, bar), (4, brain.corrected_bar(4)))                     # counted as 4 tests: conservative
        out = brain.build(g, reg, 300)['multiplicity']
        self.assertEqual((out['promising_or_better'], out['distinct_game_sets_promising_or_better']), (4, 1))
        self.assertTrue(out['valid_under_dependence'])


class PhysicalOrder(unittest.TestCase):
    def test_reordering_records_and_registry_changes_nothing(self):
        rng = random.Random(9)
        sigs = series([1.0 if rng.random() < 0.6 else -1.0 for _ in range(60)], known=145)
        sigs += [sig('m%d' % g, -1.0 if g % 3 else 1.0, 4000 + g, market='BTTS', selection='Yes', known=300, typ='POLYMARKET') for g in range(10)]
        reg = {'h%d' % i: hyp(born=at(i * 300), conditions=c) for i, c in enumerate(([], [('Minuto', '31 a 60')], [('Parte do jogo', '2.ª parte')]))}
        reg['p'] = hyp(family='Polymarket', conditions=(('Momento', 'durante o jogo'),))
        a = brain.evaluate(sigs, reg, 300)
        shuffled, rreg = list(sigs), dict(reversed(list(reg.items())))
        rng.shuffle(shuffled)
        b = brain.evaluate(shuffled, rreg, 300)
        strip = lambda rows: json.dumps(sorted(({k: v for k, v in r.items() if k != 'open'} for r in rows[0]), key=lambda r: r['id']), sort_keys=True,
                                       default=lambda x: sorted(x) if isinstance(x, (set, frozenset)) else str(x))
        self.assertEqual(strip(a), strip(b))
        self.assertEqual(json.dumps(brain.walk_forward(sigs, reg), sort_keys=True), json.dumps(brain.walk_forward(shuffled, rreg), sort_keys=True))


class Decidability(unittest.TestCase):
    def test_signals_read_too_late_are_not_reconstructible(self):
        reg = {'h': hyp()}
        wins = series([1.0] * 10)
        backfill = sig('late', 1.0, 20 * 60, seen=24 * 60)                              # read a day later (initial backfill)
        unread = sig('unread', 1.0, 20 * 60 + 1, seen=None)
        wf = brain.walk_forward(wins + [backfill, unread], reg)
        self.assertEqual((follows(wf), wf['not_reconstructible']['bets']), (0, 2))
        self.assertFalse(chronology.decidable(sig('x', None, 0, seen=20)))              # 20 + 15 min of report delay > 30
        self.assertTrue(chronology.decidable(sig('x', None, 0, seen=10)))
        pre = sig('p', None, 0, seen=300, kickoff_utc=at(400))                           # pre-match: still bettable
        self.assertTrue(chronology.decidable(pre))

    def test_decision_window_matches_the_alert_window(self):
        self.assertEqual(chronology.DECISION_MAX_LAG_MIN, notify.ALERT_MAX_AGE_MIN)


class Scenarios(unittest.TestCase):
    """Parameters fixed before running (large true edge, exact break-even, sustained decline); no tuning on the outcome."""
    SEEDS = range(10)

    def run_case(self, rates, seed):
        rng = random.Random(seed)
        outcomes = [1.0 if rng.random() < p else -1.0 for p in rates]
        sigs = series(outcomes)
        rows, _, _ = rows_by_id(sigs, {'h': hyp(born=at(-60))})
        return rows['h'], sigs

    def test_true_edge_is_validated_prospectively(self):
        for seed in self.SEEDS:
            r, sigs = self.run_case([0.75] * 160, seed)
            self.assertIn(r['status'], brain.VALIDATED, seed)
            wf = brain.walk_forward(sigs + [sig('d', None, 200 * 60)], {'h': hyp(born=at(-60))})
            self.assertGreater(wf['followed']['roi'], 0, seed)

    def test_no_edge_is_never_validated(self):
        for seed in self.SEEDS:
            r, _ = self.run_case([0.5] * 160, seed)
            self.assertNotIn(r['status'], brain.VALIDATED, seed)
            self.assertFalse(any(c['to'] in brain.VALIDATED for c in r['status_changes']), seed)

    def test_deterioration_removes_validation(self):
        for seed in self.SEEDS:
            r, _ = self.run_case([0.75] * 120 + [0.2] * 120, seed)
            self.assertNotIn(r['status'], brain.VALIDATED, seed)
            self.assertTrue(any(c['from'] in brain.VALIDATED for c in r['status_changes']), seed)


class TelegramRulesMigration(unittest.TestCase):
    """New rules key => exactly one silent re-baseline; PR 1 and PR 2 markers and delivery history stay intact."""

    def production_like_state(self):
        return {'hello': True, 'statuses': {'a': 'promissora', 'b': 'promissora', 'c': 'promissora'}, 'watch_sent': ['k'], 'sent_total': 300,
                'migrations': {notify.STATUS_MIGRATION: {'at_utc': '2026-11-15T09:27:59+00:00'},
                               'status_rules:independent_games_hysteresis_v1': {'at_utc': '2026-11-15T12:04:00+00:00'}}}

    def report(self, statuses):
        return {'generated_at_utc': at(0), 'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES, 'status_list': statuses}}

    def test_pr3_rules_rebaseline_silently_once(self):
        self.assertEqual(brain.STATUS_RULES, 'chronological_prospective_v1')
        state = self.production_like_state()
        before = json.loads(json.dumps(state))
        out = notify.messages(self.report({'a': 'promissora'}), state, T0)        # b and c lose their status under PR 3 rules
        self.assertEqual([m for m in out if 'Hipótese' in m or 'Tática' in m], [])
        key = 'status_rules:chronological_prospective_v1'
        self.assertEqual(sorted(state['migrations']), sorted(list(before['migrations']) + [key]))
        for k, v in before['migrations'].items():
            self.assertEqual(state['migrations'][k], v)
        self.assertEqual(state['migrations'][key]['previous_statuses'], before['statuses'])
        self.assertEqual((state['watch_sent'], state['sent_total']), (before['watch_sent'], before['sent_total']))
        again = notify.messages(self.report({'a': 'promissora'}), state, T0)       # no second migration, nothing to say
        self.assertEqual([m for m in again if 'Hipótese' in m], [])
        later = notify.messages(self.report({}), state, T0)                        # a genuine later change is announced
        self.assertEqual(sum('Hipótese' in m for m in later), 1)


class ReportProvenance(unittest.TestCase):
    """report.build_bets records when the system first saw each signal and the result it used. Temporary data dir only."""
    OPEN = ("🔥 Open game — heading for over\n UEFA Women's Europa Cup · 45'\nCzarni Sosnowiec Women 0\nSK Brann Women         4\n"
            "Rhythm 87/100 · Over 100%\n💰 O 5: 1.81\nhttps://scangoal.com/match/czarni-sosnowiec-women-vs-sk-brann-women-20261111?lang=en")

    def build(self, versions, extra_files=None):
        from scangoal import report
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions(versions)
                for name, content in (extra_files or {}).items():
                    with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
                        json.dump(content, f)
                chron = {}
                msgs, ft, marks, notes, _ = report.load(chron)
                bsd = {}
                for name in ('hl',):
                    st = (extra_files or {}).get('results_%s.json' % name) or {}
                    for m, r in (st.get('results') or {}).items():
                        bsd[m] = dict(r, provider=name.upper())
                manual = (extra_files or {}).get('manual')
                for m, r in (manual or {}).items():
                    bsd[m] = {'ft': r['ft'], 'provider': 'manual', 'known_utc': r.get('observed_utc')}
                return report.build_bets(msgs, ft, marks, notes, strict=False, bsd=bsd, chronology=chron)
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def version(self, text, collected, edited=None):
        return {'message_id': 1, 'date_utc': '2026-11-11T17:49:00+00:00', 'edit_date_utc': edited, 'collected_at_utc': collected, 'text': text}

    def test_result_time_is_the_first_collection_of_the_result(self):
        v1 = self.version(self.OPEN, '2026-11-11T17:50:00+00:00')
        v2 = self.version(self.OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅', '2026-11-11T19:40:00+00:00', '2026-11-11T19:38:00+00:00')
        v3 = self.version(self.OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅\n⚽  91\'   1 - 5', '2026-11-11T21:00:00+00:00', '2026-11-11T20:59:00+00:00')
        b = self.build([v1, v2, v3])[0]
        self.assertEqual((b['signal_seen_utc'], b['result_known_utc'], b['result_known_source']),
                         ('2026-11-11T17:50:00+00:00', '2026-11-11T19:40:00+00:00', 'bot_message'))

    def test_external_and_manual_results(self):
        v1 = self.version(self.OPEN, '2026-11-11T17:50:00+00:00')
        mid = 'czarni-sosnowiec-women-vs-sk-brann-women-20261111'
        hl = {'results_hl.json': {'results': {mid: {'ft': [1, 5], 'matched_at_utc': '2026-11-11T22:00:00+00:00'}}}}
        b = self.build([v1], hl)[0]
        self.assertEqual((b['result_known_utc'], b['result_known_source']), ('2026-11-11T22:00:00+00:00', 'HL'))
        b = self.build([v1], {'manual': {mid: {'ft': [1, 5]}}})[0]
        self.assertEqual((b['w'], b['result_known_utc']), (1.0, None))                   # settled, but time unknown
        self.assertEqual(chronology.result_check(b)[1], 'unknown')


if __name__ == '__main__':
    unittest.main()

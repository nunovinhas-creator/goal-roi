"""PR 3: chronological validity, prospective evidence and multiple testing under dependence (offline, deterministic).

Every fixture states its own provenance: when the system read each signal (signal_seen_utc) and when it first observed each
result (result_known_utc). Nothing here touches data/, the network or Telegram.
"""
import json
import math
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from pr5_fixtures import body, changes, evening, present
from scangoal import brain, certification, chronology, insights, notify, store

UTC = timezone.utc
T0 = datetime(2026, 11, 20, tzinfo=UTC)
EARLY = '2026-01-01T00:00:00+00:00'


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec='seconds')


def sig(match, w, minute, *, seen=0, known=145, typ='OPEN_GAME', market='Asian Total', selection='Over 2.5', odds=2.0, first='result', **extra):
    """Eligible signal sent at T0+minute, read `seen` min later; result first observed `known` min after the signal.

    first: minutes after the signal at which the system first saw ANY result of this game (report.match_first_seen);
    'result' (default) = the same instant as this bet's result; None = not proven.
    """
    d = at(minute)
    first = known if first == 'result' else first
    return dict({'match_id': match, 'w': w, 'odds': odds, 'type': typ, 'phase': 'live', 'market': market, 'selection': selection,
                 'date_utc': d, 'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga',
                 'teams': ['Home ' + str(match), 'Away ' + str(match)],
                 'in_roi': True, 'key': (match, market, selection, d), 'n_signals': 1, 'max_mult': 1, 'no_source': False,
                 'share': None, 'total': None, 'n_bets': None,
                 'signal_seen_utc': None if seen is None else at(minute + seen),
                 'result_known_utc': None if w is None or known is None else at(minute + known),
                 'match_result_first_seen_utc': None if w is None or first is None else at(minute + first)}, **extra)


def hyp(born=EARLY, family='Jogo aberto', conditions=(('Minuto', '31 a 60'),), source='gerador'):
    """A tradable hypothesis (every fixture signal is at minute 50); conditions=() is the whole family (walk-forward skips it)."""
    return {'family': family, 'conditions': [list(c) for c in conditions], 'born_utc': born, 'source': source}


def series(outcomes, start=0, every=60, prefix='g', **kw):
    """One signal per game, every `every` minutes."""
    return [sig('%s%d' % (prefix, start + i), w, (start + i) * every, **kw) for i, w in enumerate(outcomes)]


def make_ledger(registry, picks, activated=-24 * 60, legacy=()):
    """Selection ledger as production would write it: activation, then real-time selections [(ids, minute)] in order."""
    led = certification.activate({h: 'promissora' for h in legacy}, T0 + timedelta(minutes=activated))
    for ids, minute in picks:
        certification.append(led, ids, registry, T0 + timedelta(minutes=minute))
    return led


def rows_by_id(signals, registry, ledger=None):
    selected, legacy = None, ()
    if ledger is not None:
        selected, problems = certification.selections(ledger, registry)
        assert not problems, problems
        legacy = {x['id'] for x in ledger['legacy']}
    rows, tested, bar = brain.evaluate(signals, registry, 300, selected=selected, legacy=legacy)
    return {r['id']: r for r in rows}, tested, bar


THREE_IN_FOUR = ([1.0, 1.0, 1.0, -1.0] * 26)[:102]   # 102 games at 3/4 and odds 2.0: a large, certifiable edge
CHAIN = 180   # minutes between games: each result (+145 min) is seen before the next decision, so the certification chain
              # admits every game (review #5911747123: a game decided while the previous one is open never enters it)


def follows(wf):
    return wf['followed']['bets']


class EconomicEvidence(unittest.TestCase):
    def test_high_hit_rate_and_negative_roi_is_not_favorable(self):
        rows = [sig(str(i), 1.0 if i < 80 else -1.0, i,
                    odds=1.10 if i < 80 else 20.0) for i in range(100)]
        hypothesis = rows_by_id(rows, {'h': hyp()})[0]['h']
        self.assertGreater(hypothesis['prob_above'], brain.BOARD_GOOD)
        economy = hypothesis['prospective']['economics']
        self.assertEqual((economy['confirmed_hit_rate'], economy['roi_advertised']), (0.8, -0.12))
        self.assertEqual(economy['status'], 'nonpositive_exploratory')
        self.assertIsNone(economy['expected_profit'])
        self.assertEqual(brain.board([hypothesis], T0)['best'], [])

    def test_first_committed_bet_and_timeout_define_one_unit_per_game(self):
        rows = [sig('m1', None, 0), sig('m1', 1.0, 1, market='1X2', selection='1', odds=7.0),
                sig('m2', 1.0, 20)]
        summary = brain.economic_summary(rows, chronology.parse_utc(EARLY), T0 + timedelta(days=4))
        self.assertEqual((summary['admitted'], summary['results_confirmed'], summary['imputed_losses']), (2, 1, 1))
        self.assertEqual((summary['resolved_stake_units'], summary['profit_advertised'], summary['roi_advertised']), (2, 0.0, 0.0))
        self.assertEqual(summary['roi_odds_minus_2pct'], -0.02)


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
        own = series(THREE_IN_FOUR, every=CHAIN)                                       # 102 games, certified with rank 1
        base = {'a': hyp()}
        led = make_ledger(base, [(['a'], -30)])
        rows1, _, _ = rows_by_id(own, base, led)
        self.assertEqual(rows1['a']['status'], 'forte')
        # 400 hypotheses registered long after a's last game (one of them selected later), on games played afterwards.
        later = [sig('s%d' % i, 1.0 if i % 4 else -1.0, 20000 + i * 60, typ='STAKE_LOAD') for i in range(40)]
        crowd = dict(base, **{'s%d' % k: hyp(born=at(9000), family='Stake', conditions=()) for k in range(400)})
        rows2, tested2, _ = rows_by_id(own + later, crowd, led)
        self.assertEqual(tested2, 401)
        self.assertEqual(rows1['a']['status_changes'], rows2['a']['status_changes'])
        self.assertEqual((rows1['a']['status'], rows1['a']['confirmation']), (rows2['a']['status'], rows2['a']['confirmation']))
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


class OnlineConfirmation(unittest.TestCase):
    """Validation = e-process on operational games after the REGISTERED selection, alpha/(j(j+1)) by ledger order."""

    def test_certified_tactic_and_its_certificate(self):
        reg = {'a': hyp()}
        sigs = series(THREE_IN_FOUR, every=CHAIN)
        r = rows_by_id(sigs, reg, make_ledger(reg, [(['a'], -30)]))[0]['a']
        c = r['confirmation']
        self.assertEqual((r['status'], c['rank'], c['alpha'], c['threshold'], c['certifiable']), ('forte', 1, 0.025, 40.0, True))
        self.assertGreaterEqual(c['log10_e_max'], 1.602)
        self.assertGreaterEqual(c['certified_utc'], c['selected_utc'])
        self.assertEqual(c['selected_utc'], at(-30))                                    # the ledger time, not a replay
        self.assertEqual(c['games'], 102)

    def test_alpha_budget_follows_the_ledger(self):
        self.assertAlmostEqual(sum(brain.alpha_for_rank(j) for j in range(1, 100000)), brain.FWER_ALPHA * (1 - 1 / 100000), places=12)
        early = series(THREE_IN_FOUR, every=CHAIN)
        late = series(THREE_IN_FOUR, start=200, every=CHAIN, prefix='p', typ='POLYMARKET', market='1X2', selection='1')
        reg = {'a': hyp(), 'b': hyp(family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        led = make_ledger(reg, [(['a'], -30), (['b'], 150 * CHAIN)])
        rows = rows_by_id(early + late, reg, led)[0]
        self.assertEqual((rows['a']['confirmation']['rank'], rows['b']['confirmation']['rank']), (1, 2))
        self.assertEqual((rows['a']['confirmation']['threshold'], rows['b']['confirmation']['threshold']), (40.0, 120.0))
        alone = rows_by_id(early, {'a': reg['a']}, make_ledger({'a': reg['a']}, [(['a'], -30)]))[0]['a']
        self.assertEqual(alone['confirmation'], rows['a']['confirmation'])             # a later selection changes nothing earlier

    def test_e_process_keeps_its_level_under_the_null(self):
        # Boundary null: win probability makes the expected profit at odds -2 % exactly zero; odds 1.4-3.5, some half wins.
        rng, crossed, means = random.Random(77), 0, []
        for run in range(300):
            games = []
            for i in range(400):
                o = round(rng.uniform(1.4, 3.5), 2)
                p = 1 / (o * (1 - insights.ODDS_LOSS))
                w = 1.0 if rng.random() < p else -1.0
                if w > 0 and rng.random() < 0.1:   # some wins are half wins: lower expected profit, still inside the null
                    w = 0.5 if rng.random() < 0.5 else 1.0
                games.append(sig('n%d-%d' % (run, i), w, i * CHAIN, odds=o))
            c = brain.confirmation(games, T0 - timedelta(days=1), 0.05)
            self.assertEqual(c['games'], 400)
            crossed += c['log10_e_max'] >= 1.30103
            means.append(c['e_value'])
        self.assertLessEqual(crossed / 300, 0.05)                                      # Ville: P(sup E >= 1/alpha) <= alpha
        self.assertLess(sum(means) / len(means), 1.25)                                 # supermartingale: E[E_n] <= 1 (noisy)

    def test_strong_history_without_post_selection_evidence_is_not_certified(self):
        history = series([1.0] * 90 + [-1.0] * 30, every=CHAIN)
        weak_after = series([1.0, -1.0] * 20, start=200, every=CHAIN)                  # break-even after the selection
        reg = {'h': hyp(born=at(150 * CHAIN))}
        r = rows_by_id(history + weak_after, reg, make_ledger(reg, [(['h'], 180 * CHAIN)]))[0]['h']
        self.assertGreaterEqual(r['prob_above'], 0.9999)
        self.assertIsNone(r['confirmation']['certified_utc'])
        self.assertNotIn(r['status'], brain.VALIDATED)

    def test_no_validated_status_without_a_certificate_even_if_the_machine_says_so(self):
        real = brain.stable_status
        try:
            brain.stable_status = lambda *a, **k: ('forte', None, [])
            r = rows_by_id(series([1.0] * 40), {'h': hyp()})[0]['h']
        finally:
            brain.stable_status = real
        self.assertEqual((r['status'], r.get('validation_blocked')), ('promissora', True))


class ProspectiveEligibility(unittest.TestCase):
    """Review #5911487915, P1-A and P1-B: one definition of prospective evidence, per game, used everywhere."""
    BORN = 100 * 60

    def summary(self, sigs):
        rows = rows_by_id(sigs, {'h': hyp(born=at(self.BORN))})[0]['h']
        steps = brain.game_steps(sigs, chronology.parse_utc(at(self.BORN)), prospective_from=chronology.parse_utc(at(self.BORN)))
        return rows, (steps[-1][3]['prospective_games'] if steps else 0)

    def test_pre_registration_signals_recovered_late_are_never_prospective(self):
        # 40 wins sent BEFORE registration but read by the system only after it (late recovery / backfill).
        late = [sig('b%d' % i, 1.0, i * 60, seen=self.BORN - i * 60 + 30 + i, known=self.BORN - i * 60 + 200 + i) for i in range(40)]
        r, replay = self.summary(late)
        self.assertTrue(all(chronology.decision_time(b) > chronology.parse_utc(at(self.BORN)) for b in late))   # read after birth
        self.assertEqual((r['prospective']['games'], replay), (0, 0))
        self.assertEqual(r['prospective']['categories']['before_registration'], 40)
        self.assertEqual(r['evidence_level'], 'exploratória')
        self.assertNotIn(r['status'], brain.VALIDATED)
        self.assertTrue(r['confirmation'] is None or r['confirmation']['games'] == 0)

    def test_signals_sent_after_registration_but_not_operational(self):
        read_late = [sig('l%d' % i, 1.0, self.BORN + 60 + i * 60, seen=24 * 60, first=25 * 60) for i in range(35)]   # not bettable
        unknown = [sig('u%d' % i, 1.0, self.BORN + 60 + i * 60, known=None, first=145) for i in range(35)]            # result time unproven
        unseen = [sig('v%d' % i, 1.0, self.BORN + 60 + i * 60, first=None) for i in range(35)]                     # first result unproven
        for case, cat in ((read_late, 'after_registration_not_operational'), (unknown, 'after_registration_not_operational'),
                          (unseen, 'result_time_unproven')):
            r, replay = self.summary(case)
            self.assertEqual((r['prospective']['games'], replay), (0, 0))
            self.assertEqual(r['prospective']['categories'][cat], 35, cat)

    def test_mixed_games_count_nowhere_and_full_games_count_everywhere(self):
        def game(i, both_after):
            # Two bets per game, both decided before the game's first result; in a mixed game the first is sent (and read)
            # before registration, the second after it.
            if both_after:
                first, second = self.BORN + 30 + i * 60, self.BORN + 100 + i * 60
            else:   # before / after registration; the second is decided (read + 15 min) before the first result (+145 min)
                first, second = self.BORN - 30 - i, self.BORN + 10 + i
            return [sig('m%d' % i, 1.0, first, typ='POLYMARKET', market='1X2', selection='1'),
                    sig('m%d' % i, 1.0, second, typ='POLYMARKET', market='BTTS', selection='Yes')]
        reg = {'h': hyp(born=at(self.BORN), family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        for both_after, expected, cat in ((False, 0, 'mixed'), (True, 35, 'prospective')):
            sigs = [b for i in range(35) for b in game(i, both_after)]
            r = rows_by_id(sigs, reg)[0]['h']
            born = chronology.parse_utc(at(self.BORN))
            steps = brain.game_steps(sigs, born, prospective_from=born)
            self.assertEqual((r['prospective']['games'], steps[-1][3]['prospective_games']), (expected, expected))
            self.assertEqual(r['prospective']['categories'][cat], 35)

    def test_report_and_state_machine_agree_on_random_data(self):
        rng = random.Random(12)
        sigs = []
        for g in range(120):
            for j in range(rng.choice((1, 1, 2, 3))):
                minute = g * 50 + j * 7 + rng.choice((-3000, 0, 0, 3000))
                sigs.append(sig('r%d' % g, rng.choice((1.0, -1.0, 0.5)), minute, seen=rng.choice((0, 1, 2, 60, None)),
                                known=rng.choice((145, 145, 200, None, -10)), typ='POLYMARKET', market=rng.choice(('1X2', 'BTTS')), selection='x'))
        born = chronology.parse_utc(at(3000))
        reg = {'h': hyp(born=at(3000), family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        r = rows_by_id(sigs, reg)[0]['h']
        ordered = brain.ordered_games([b for b in sigs if b['w'] is not None])
        direct = sum(1 for _, _, rows in ordered if brain.operational_game(rows, born))
        steps = brain.game_steps(sigs, born, prospective_from=born)
        self.assertGreater(direct, 0)
        self.assertEqual(r['prospective']['games'], direct)
        self.assertEqual(steps[-1][3]['prospective_games'], direct)
        self.assertEqual(sum(r['prospective']['categories'].values()), len(ordered))


class OverlappingHypotheses(unittest.TestCase):
    def test_overlap_is_reported_and_every_hypothesis_pays_its_own_alpha(self):
        g = series(THREE_IN_FOUR, every=CHAIN)
        idx = brain.earlier_index(g)
        common = sorted(set.intersection(*(set(brain.atoms(dict(b), set(), idx)) for b in g)) - {('Dia', 'fim de semana'), ('Dia', 'dia de semana')})
        picks = [[], [common[0]], [common[1]], [common[0], common[1]]]
        reg = {'h%d' % i: hyp(conditions=c) for i, c in enumerate(picks)}
        led = make_ledger(reg, [(sorted(reg), -30)])                                    # selected together, ranked by identity
        rows, tested, _ = rows_by_id(g, reg, led)
        self.assertEqual(len({r['game_set'] for r in rows.values()}), 1)               # the same evidence four times
        self.assertEqual(sorted(r['confirmation']['rank'] for r in rows.values()), [1, 2, 3, 4])   # four tests, four budgets
        self.assertEqual(sorted(r['confirmation']['threshold'] for r in rows.values()), [40.0, 120.0, 240.0, 400.0])
        out = brain.build(g, reg, 300, led, history_problems=[])
        self.assertEqual((out['multiplicity']['promising_or_better'], out['multiplicity']['distinct_game_sets_promising_or_better']), (4, 1))
        self.assertEqual(out['validation']['method'], notify.VALIDATION_METHOD)


class TelegramNeverClaimsUncertifiedValidation(unittest.TestCase):
    def report(self, validation=None, certified_reason=False):
        reason = {'id': 'h1', 'status': 'forte', 'n': 120, 'hit_rate': 0.7, 'prob_above': 0.9999, 'break_even': 0.5, 'edge': 0.1,
                  'edge_ci': [0.05, 0.15], 'prudent_rate': 0.68, 'certified': certified_reason}
        brain_ = {'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES, 'status_list': {'h1': 'forte'},
                  'watch': [{'date_utc': at(0), 'kickoff_utc': at(120), 'match': 'A — B', 'selection': 'Over 2.5', 'odds': 1.9, 'reasons': [reason]}]}
        if validation is not None:
            brain_['validation'] = validation
        return {'generated_at_utc': at(0), 'brain': brain_}

    def test_forte_without_certificate_is_announced_as_promising_only(self):
        for validation, flag in ((None, True), ({'method': 'bonferroni', 'certified': ['h1']}, True),
                                 ({'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': []}, True),
                                 ({'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': 'h1'}, True),
                                 ({'method': notify.VALIDATION_METHOD, 'state': 'not_activated', 'ledger_valid': False, 'certified': ['h1']}, True),
                                 ({'method': notify.VALIDATION_METHOD, 'state': 'ledger_invalid', 'ledger_valid': True, 'certified': ['h1']}, True)):
            state = {'hello': True, 'statuses': {'h1': 'promissora'}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
            out = notify.messages(self.report(validation, flag), state, T0)
            self.assertFalse(any('ALERTA' in m or 'validada' in m or 'forte' in m for m in out), (validation, flag, out))
            self.assertEqual(state['statuses'], {'h1': 'promissora'})

    def test_alert_needs_the_certificate_on_the_reason_too(self):
        state = {'hello': True, 'statuses': {'h1': 'forte'}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
        out = notify.messages(self.report({'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': ['h1']}, False), state, T0)
        self.assertFalse(any('ALERTA' in m for m in out))
        self.assertTrue(any(m.startswith('🟠 INDÍCIO · ') and 'hipótese promissora · sem certificação' in m for m in out))

    def test_certified_tactic_is_announced(self):
        # PR 5: the certificate must belong to the hypothesis itself (its row carries the confirmation) and the scientific
        # state comes first in every message.
        state = {'hello': True, 'statuses': {'h1': 'promissora'}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
        rep = present(self.report({'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': ['h1']}, True), certified=['h1'])
        out = notify.messages(rep, state, T0)
        self.assertTrue(any(m.startswith('🟢 ALERTA') for m in out))                    # the bet's eligibility changes at once
        self.assertFalse(any(m.startswith('Resumo') for m in out))                      # the status change waits for 22:00
        self.assertTrue(all(m.split('\n')[-1].startswith('Estado científico: CERTIFICADA') for m in out if 'validada' in m))
        later = notify.messages(rep, state, evening(T0))
        self.assertEqual(changes(later), [('h1', '✅ Tática validada, passou a forte (antes: promissora): h1')])
        self.assertIn(notify.scientific_label(rep, 'h1')[0], later[0])
        state = {'hello': True, 'statuses': {'h1': 'promissora'}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
        rep = self.report({'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': ['h1']}, True)
        for t in (T0, evening(T0)):                                                      # listed but no own certificate
            self.assertFalse(any('ALERTA' in m or 'validada' in m for m in notify.messages(rep, state, t)))


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
        sigs = series(outcomes, every=CHAIN)
        reg = {'h': hyp(born=at(-60))}
        rows, _, _ = rows_by_id(sigs, reg, make_ledger(reg, [(['h'], -30)]))
        return rows['h'], sigs

    def test_true_edge_is_certified_prospectively(self):
        for seed in self.SEEDS:
            r, sigs = self.run_case([0.75] * 160, seed)
            self.assertIn(r['status'], brain.VALIDATED, seed)
            wf = brain.walk_forward(sigs + [sig('d', None, 200 * CHAIN)], {'h': hyp(born=at(-60))})
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
        self.assertEqual(brain.STATUS_RULES, 'chronological_eprocess_v3')
        state = self.production_like_state()
        before = json.loads(json.dumps(state))
        out = notify.messages(self.report({'a': 'promissora'}), state, T0)        # b and c lose their status under PR 3 rules
        self.assertEqual([m for m in out if 'Hipótese' in m or 'Tática' in m], [])
        key = 'status_rules:chronological_eprocess_v3'
        # PR 5 adds its own silent format marker in the same pass; the earlier markers are untouched.
        self.assertEqual(sorted(state['migrations']), sorted(list(before['migrations']) + [key, notify.FORMAT_MIGRATION]))
        for k, v in before['migrations'].items():
            self.assertEqual(state['migrations'][k], v)
        self.assertEqual(state['migrations'][key]['previous_statuses'], before['statuses'])
        self.assertEqual((state['watch_sent'], state['sent_total']), (before['watch_sent'], before['sent_total']))
        again = notify.messages(self.report({'a': 'promissora'}), state, T0)       # no second migration, nothing to say
        self.assertEqual([m for m in again if 'Hipótese' in m], [])
        self.assertEqual(notify.messages(self.report({}), state, T0), [])          # PR 5: absence is "unknown", not a return
        later = notify.messages(present(self.report({}), observed=['a']), state, evening(T0))   # a genuine later change: 22:00 summary
        self.assertEqual(changes(later), [('a', 'Estado: Em observação (antes: promissora)')])


class AsOfMembership(unittest.TestCase):
    """Review #5911603577, P1-C: certification only on conditions known at the first signal; later facts never change it."""

    def betfair(self, match, w, minute, **kw):
        base = dict(typ='BETFAIR', market='1X2', selection='1', share=90, mkt_volume=5000, lead_min=None, odds_last=2.0, n_signals=1, max_mult=1)
        base.update(kw)
        return sig(match, w, minute, **base)

    def test_only_as_of_dimensions_are_certifiable(self):
        for dim in sorted(brain.POST) + ['Sinal anterior de outro tipo no mesmo jogo', 'Dimensão nova']:
            self.assertFalse(brain.certifiable(hyp(conditions=((dim, 'x'),))), dim)
        for dim in sorted(brain.AS_OF_DIMENSIONS):
            self.assertTrue(brain.certifiable(hyp(conditions=((dim, 'x'),))), dim)
        self.assertFalse(brain.certifiable(dict(hyp(), born_utc='ontem')))
        self.assertFalse(brain.certifiable({'family': 'Jogo aberto', 'conditions': 'x', 'born_utc': EARLY}))

    def test_a_later_signal_of_another_family_changes_nothing(self):
        own = series(THREE_IN_FOUR, every=CHAIN)
        reg = {'a': hyp(), 'post': hyp(conditions=(('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'),))}
        led = make_ledger(reg, [(['a', 'post'], -30)])
        before = rows_by_id(own, reg, led)[0]
        later = [sig('g%d' % i, 1.0, i * CHAIN + 90, typ='POLYMARKET', market='1X2', selection='1') for i in range(102)]   # other family, later
        after = rows_by_id(own + later, reg, led)[0]
        self.assertEqual(before['a']['confirmation'], after['a']['confirmation'])
        self.assertEqual(before['a']['status'], after['a']['status'])
        for rows in (before, after):                                                   # the label moved, but it is never certifiable
            self.assertFalse(rows['post']['confirmation']['certifiable'])
            self.assertIsNone(rows['post']['confirmation']['certified_utc'])
            self.assertNotIn(rows['post']['status'], brain.VALIDATED)

    def test_later_reinforcement_or_odds_move_changes_nothing(self):
        games = [self.betfair('b%d' % i, w, i * CHAIN) for i, w in enumerate(THREE_IN_FOUR)]
        moved = [dict(b, max_mult=4, n_signals=3, odds_last=1.7) for b in games]          # what later messages add to the same bet
        reg = {'safe': hyp(family='Betfair', conditions=(('Momento', 'durante o jogo'),)),
               'post': hyp(family='Betfair', conditions=(('Reforço', 'sem reforço'),))}
        led = make_ledger(reg, [(['post', 'safe'], -30)])
        a, b = rows_by_id(games, reg, led)[0], rows_by_id(moved, reg, led)[0]
        self.assertEqual(a['safe']['confirmation'], b['safe']['confirmation'])
        self.assertIsNotNone(a['safe']['confirmation']['certified_utc'])
        self.assertFalse(a['post']['confirmation']['certifiable'])
        self.assertNotIn(a['post']['status'], brain.VALIDATED)

    def test_other_bets_of_a_game_never_change_the_committed_one(self):
        own = series(THREE_IN_FOUR, every=CHAIN)
        reg = {'a': hyp()}
        led = make_ledger(reg, [(['a'], -30)])
        base = rows_by_id(own, reg, led)[0]['a']['confirmation']
        for offset, first in ((200, 'result'), (20, 125)):                                # after / before the first result
            extra = [sig('g%d' % i, -1.0, i * CHAIN + offset, selection='Over 3.5', first=first) for i in range(102)]
            self.assertEqual(rows_by_id(own + extra, reg, led)[0]['a']['confirmation'], base, offset)
        # Positive control: changing the committed (first) bet's outcome does change the e-process.
        flipped = [dict(b, w=-b['w']) if i % 4 == 0 else b for i, b in enumerate(own)]
        self.assertNotEqual(rows_by_id(flipped, reg, led)[0]['a']['confirmation']['log10_e'], base['log10_e'])


class ResultKnownBeforeDecision(unittest.TestCase):
    """Review #5911603577, P1-D: a result the system already had when the bet could still be decided never counts."""

    def run_case(self, **kw):
        sigs = series(THREE_IN_FOUR[:40], every=CHAIN, **kw)
        reg = {'a': hyp()}
        return rows_by_id(sigs, reg, make_ledger(reg, [(['a'], -30)]))[0]['a'], sigs

    def test_result_seen_before_reading_gives_zero_certification_games(self):
        r, sigs = self.run_case(seen=8, known=4)      # sent 12:00, result seen 12:04, signal read 12:08 (still "decidable")
        self.assertTrue(all(chronology.decidable(b) for b in sigs))
        self.assertEqual(r['confirmation']['games'], 0)
        self.assertEqual(r['prospective']['games'], 0)
        self.assertEqual(r['prospective']['categories']['result_known_before_decision'], 40)

    def test_report_latency_is_respected(self):
        r, _ = self.run_case(seen=0, known=10)        # result seen 10 min after the read: inside the 15-min decision window
        self.assertEqual(r['confirmation']['games'], 0)
        r, _ = self.run_case(seen=0, known=145)       # positive control
        self.assertEqual(r['confirmation']['games'], 40)

    def test_another_source_seeing_the_result_first_also_counts(self):
        r, _ = self.run_case(seen=0, known=145, first=5)   # the bot settled at +145, but Highlightly had it at +5
        self.assertEqual(r['confirmation']['games'], 0)


class SelectionLedger(unittest.TestCase):
    """Review #5911603577, P1-E: rank, alpha and start fixed once, verifiable; legacy never certified; off by default."""

    def setUp(self):
        self.reg = {'a': hyp(), 'b': hyp(family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        self.data = series(THREE_IN_FOUR, every=CHAIN) + series(THREE_IN_FOUR, start=200, every=CHAIN, prefix='p', typ='POLYMARKET', market='1X2', selection='1')
        self.led = make_ledger(self.reg, [(['b'], -30), (['a'], -20)])

    def test_late_data_never_reorders_or_rewrites_the_ledger(self):
        first = rows_by_id(self.data, self.reg, self.led)[0]
        # Late facts: results of old games seen again by another source, extra late bets, late signals of other families.
        late = [sig('g%d' % i, 1.0, i * CHAIN + 300, selection='Over 1.5') for i in range(50)]
        late += [sig('p%d' % (200 + i), 1.0, (200 + i) * CHAIN + 300, typ='STAKE_LOAD') for i in range(50)]
        second = rows_by_id(self.data + late, self.reg, self.led)[0]
        for hid in ('a', 'b'):
            self.assertEqual(first[hid]['confirmation'], second[hid]['confirmation'], hid)
        self.assertEqual((first['b']['confirmation']['rank'], first['a']['confirmation']['rank']), (1, 2))   # ledger order, not replay

    def test_any_tampering_disables_all_certification(self):
        def mutate(f):
            led = json.loads(json.dumps(self.led))
            f(led)
            return led
        cases = {'alpha': lambda l: l['entries'][0].update(alpha=0.05), 'rank': lambda l: l['entries'][1].update(rank=1),
                 'reorder': lambda l: l['entries'].reverse(), 'earlier_time': lambda l: l['entries'][1].update(selected_utc=at(-500)),
                 'chain': lambda l: l['entries'][1].update(prev_sha256='0' * 64), 'method': lambda l: l.update(method='bonferroni'),
                 'legacy_injected': lambda l: l['legacy'].append({'id': 'a'}), 'genesis': lambda l: l.update(activated_utc=at(-10)),
                 'duplicate': lambda l: l['entries'].append(dict(l['entries'][0])), 'not_a_list': lambda l: l.update(entries={})}
        for name, f in cases.items():
            led = mutate(f)
            self.assertTrue(certification.verify(led, self.reg), name)
            out = brain.build(self.data, self.reg, 300, led)
            self.assertEqual((out['validation']['state'], out['validation']['certified']), ('ledger_invalid', []), name)
            self.assertFalse(any(v in brain.VALIDATED for v in out['status_list'].values()), name)
        changed = dict(self.reg, a=hyp(conditions=(('Minuto', '61 em diante'),)))   # registry entry rewritten after selection
        self.assertTrue(certification.verify(self.led, changed))
        self.assertEqual(certification.verify(self.led, self.reg), [])

    def test_validation_is_off_by_default(self):
        out = brain.build(self.data, self.reg, 300)                                    # no ledger: not activated
        self.assertEqual((out['validation']['state'], out['validation']['certified'], out['validation']['selected']), ('not_activated', [], 0))
        self.assertFalse(any(v in brain.VALIDATED for v in out['status_list'].values()))
        self.assertIn('no claim', out['validation']['claim'])
        state = {'hello': True, 'statuses': {}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
        msgs = notify.messages({'generated_at_utc': at(0), 'brain': out}, state, T0 + timedelta(days=30))
        self.assertFalse(any('ALERTA' in m or 'validada' in m for m in msgs))

    def test_legacy_hypotheses_are_never_certified(self):
        led = make_ledger(self.reg, [(['b'], -30)], legacy=('a',))
        self.assertEqual(certification.append(led, ['a'], self.reg, T0), 0)               # never selected afterwards
        rows = rows_by_id(self.data, self.reg, led)[0]
        self.assertIsNone(rows['a']['confirmation'])
        self.assertEqual(rows['a']['certification'], 'legado: não certificável')
        self.assertNotIn(rows['a']['status'], brain.VALIDATED)
        self.assertEqual(rows['b']['confirmation']['rank'], 1)                          # legacy consumes no alpha

    def test_unwitnessed_ledger_has_no_selection_candidates(self):
        reg = dict(self.reg, post=hyp(conditions=(('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'),)))
        led = make_ledger(reg, [], legacy=())
        out = brain.build(self.data[:40], reg, 300, led, history_problems=[], as_of=T0 + timedelta(days=5))
        self.assertEqual((out['validation']['state'], out['validation']['candidates']), ('history_unverified', []))
        self.assertEqual(led['entries'], [])


class NumericalRobustness(unittest.TestCase):
    def test_thousands_of_winning_games_never_overflow(self):
        games = [sig('w%d' % i, 1.0, i * CHAIN, odds=3.0) for i in range(5000)]
        c = brain.confirmation(games, T0 - timedelta(days=1), brain.alpha_for_rank(50))
        self.assertEqual(c['games'], 5000)
        self.assertIsNone(c['e_value'])                                                 # beyond e^700: only its log is shown
        self.assertGreater(c['log10_e'], 1000)
        self.assertIsNotNone(c['certified_at'])
        json.dumps(c, default=str)
        losses = [dict(b, w=-1.0) for b in games]
        c = brain.confirmation(losses, T0 - timedelta(days=1), 0.05)
        self.assertLess(c['log10_e'], -60)                                              # 5000 * log10(1 - 1/32) dominates the mixture
        self.assertEqual(c['e_value'], 0.0)
        self.assertIsNone(c['certified_at'])


class SimultaneouslyOpenGames(unittest.TestCase):
    """Review #5911747123, P1-F: 120 games decided before any result, all driven by the SAME fair coin X."""
    SINCE = T0 - timedelta(days=1)

    def correlated(self, x, spacing=1, known=1000, edge_p=None, seed=0):
        rng = random.Random(seed)
        out = []
        for i in range(120):
            w = x if edge_p is None else (1.0 if rng.random() < edge_p else -1.0)
            out.append(sig('c%d' % i, w, i * spacing, known=known - i * spacing))   # every result revealed after all decisions
        return out

    def test_120_correlated_open_games_never_certify(self):
        for x in (1.0, -1.0):                                       # each branch has probability 1/2 under the stated null
            games = self.correlated(x)
            c = brain.confirmation(games, self.SINCE, brain.alpha_for_rank(1))
            self.assertEqual((c['admitted'], c['skipped']['overlap']), (1, 119), x)
            self.assertIsNone(c['certified_at'], x)
            self.assertLess(c['log10_e_max'], 1)
        # What the audited rule did: the 120 factors multiplied in reveal order (all wins) certify with a huge margin.
        log_e = max(120 * math.log1p(lam * (2.0 * 0.98 - 1)) for lam in brain.EPROCESS_LAMBDAS) - math.log(len(brain.EPROCESS_LAMBDAS))
        self.assertGreater(log_e, math.log(40))

    def test_whole_hypothesis_never_validated_from_correlated_open_games(self):
        reg = {'a': hyp()}
        rows = rows_by_id(self.correlated(1.0), reg, make_ledger(reg, [(['a'], -30)]))[0]['a']
        self.assertIsNone(rows['confirmation']['certified_utc'])
        self.assertNotIn(rows['status'], brain.VALIDATED)

    def test_controls_sequential_games_with_and_without_edge(self):
        edge = [sig('e%d' % i, w, i * CHAIN) for i, w in enumerate(THREE_IN_FOUR)]
        self.assertIsNotNone(brain.confirmation(edge, self.SINCE, brain.alpha_for_rank(1))['certified_at'])     # real edge
        rng, crossed = random.Random(5), 0
        for run in range(100):                                       # sequential, no edge (fair coin per game at the boundary)
            p = 1 / (2.0 * (1 - insights.ODDS_LOSS))
            games = [sig('z%d-%d' % (run, i), 1.0 if rng.random() < p else -1.0, i * CHAIN) for i in range(200)]
            crossed += brain.confirmation(games, self.SINCE, brain.alpha_for_rank(1))['certified_at'] is not None
        self.assertLessEqual(crossed / 100, brain.alpha_for_rank(1))

    def test_real_edge_but_overlapping_games_is_the_price(self):
        # Honest cost: a true edge on games that are all open together cannot be certified (one admitted game).
        games = self.correlated(None, edge_p=0.75, seed=3)
        c = brain.confirmation(games, self.SINCE, brain.alpha_for_rank(1))
        self.assertEqual((c['admitted'], c['certified_at']), (1, None))


class AdaptiveBetsPerGame(unittest.TestCase):
    """Review #5911747123, P1-G: a second bet only when a later signal points to X = -1 (no result seen yet)."""
    WIN, LOSS = 2.0 * (1 - insights.ODDS_LOSS) - 1, -1.0

    def game(self, i, x, y, minute):
        rows = [sig('a%s' % i, x, minute, market='Asian Total', selection='Over 2.5', first=145)]
        if x < 0:                                                    # the adaptive extra bet, decided after the first one
            rows.append(sig('a%s' % i, y, minute + 30, market='Asian Total', selection='Over 1.5', first=115))
        return rows

    def test_exact_expectation_of_the_committed_bet_and_of_the_old_average(self):
        committed, averaged = [], []
        for x in (1.0, -1.0):
            for y in (1.0, -1.0):
                rows = self.game(0, x, y, 0)
                committed.append(brain.bet_profit(brain.committed_bet(rows)))
                profits = [brain.bet_profit(b) for b in rows]
                averaged.append(sum(profits) / len(profits))         # the per-game mean used before this review
        self.assertAlmostEqual(sum(committed) / 4, (self.WIN + self.LOSS) / 2)   # = -0.02 <= 0 (null preserved)
        self.assertGreater(sum(averaged) / 4, 0.2)                                # +0.225: the selective average had an edge

    def test_sequential_adaptive_games_never_certify_under_the_null(self):
        rng, crossed, old_mean = random.Random(9), 0, []
        p = 1 / (2.0 * (1 - insights.ODDS_LOSS))                     # boundary: each single bet has zero expected profit
        for run in range(200):
            games = []
            for i in range(200):
                x = 1.0 if rng.random() < p else -1.0
                y = 1.0 if rng.random() < p else -1.0
                rows = self.game('%d-%d' % (run, i), x, y, i * CHAIN)
                games += rows
                prof = [brain.bet_profit(b) for b in rows]
                old_mean.append(sum(prof) / len(prof))
            crossed += brain.confirmation(games, T0 - timedelta(days=1), 0.05)['certified_at'] is not None
        self.assertLessEqual(crossed / 200, 0.05)
        self.assertGreater(sum(old_mean) / len(old_mean), 0.2)       # the old per-game average drifted upwards


class LedgerHardening(unittest.TestCase):
    """Review #5911747123, P2: malformed entries never raise; append-only proven against history; activation guarded."""

    def setUp(self):
        self.reg = {'a': hyp(), 'b': hyp(family='Polymarket', conditions=(('Momento', 'durante o jogo'),))}
        self.led = make_ledger(self.reg, [(['a'], -30), (['b'], -20)])

    def test_malformed_ledgers_are_problems_not_crashes(self):
        bad = [None, [], 'x', {}, dict(self.led, entries=None), dict(self.led, legacy=[[]]), dict(self.led, legacy=[{'id': []}]),
               dict(self.led, entries=[{}]), dict(self.led, entries=[dict(self.led['entries'][0], id=[])]),
               dict(self.led, entries=[dict(self.led['entries'][0], rank='1')]), dict(self.led, entries=[dict(self.led['entries'][0], alpha=True)]),
               dict(self.led, entries=[dict(self.led['entries'][0], prev_sha256=123)]), dict(self.led, entries=[dict(self.led['entries'][0], extra=1)]),
               dict(self.led, alpha=True), dict(self.led, activated_utc=7), {'invalid': 'unreadable'}]
        for led in bad:
            problems = certification.verify(led, self.reg)
            self.assertTrue(problems, led)
            out = brain.build(series([1.0] * 10), self.reg, 300, led)
            self.assertIn(out['validation']['state'], ('ledger_invalid', 'not_activated'))
            self.assertEqual(out['validation']['certified'], [])
        self.assertEqual(certification.verify(self.led, self.reg), [])

    def test_history_detects_rewrites_that_the_internal_chain_cannot(self):
        rewritten = certification.activate({}, T0 - timedelta(days=1))   # same activation, entries in another order, rehashed
        certification.append(rewritten, ['b'], self.reg, T0 - timedelta(minutes=30))
        certification.append(rewritten, ['a'], self.reg, T0 - timedelta(minutes=20))
        self.assertEqual(certification.verify(rewritten, self.reg), [])                  # internally consistent
        self.assertTrue(certification.verify_history([self.led, rewritten]))             # but not an extension of what was published
        first = make_ledger(self.reg, [(['a'], -30)])
        self.assertEqual(certification.verify_history([first, self.led]), [])            # a genuine append
        diverged = json.loads(json.dumps(first))
        certification.append(diverged, ['b'], self.reg, T0 - timedelta(minutes=10))
        self.assertTrue(certification.verify_history([self.led, diverged]))              # divergent prefix
        self.assertTrue(certification.verify_history([self.led, first]))                 # truncation
        self.assertNotEqual(certification.head(self.led), certification.head(first))

    def rows(self):
        return [{'id': 'p', 'status': 'promissora', 'pending_status': None, 'status_changes': [{'to': 'promissora'}]},
                {'id': 'q', 'status': 'em observação', 'pending_status': 'promissora', 'status_changes': []},
                {'id': 'r', 'status': 'em observação', 'pending_status': None, 'status_changes': [{'to': 'promissora'}, {'to': 'em observação'}]},
                {'id': 's', 'status': 'em observação', 'pending_status': None, 'status_changes': []}]

    def test_activation_preconditions_and_complete_legacy(self):
        now = T0
        report = {'generated_at_utc': at(-5), 'brain': {'status_rules': brain.STATUS_RULES, 'status_list': {'p': 'promissora'},
                                                        'validation': {'state': 'not_activated'}}}
        state = {'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}
        ok = dict(collection_paused=True, ledger_exists=False)
        led, problems = certification.activation_plan(report, state, self.rows(), brain.STATUS_RULES, now, **ok)
        self.assertEqual(problems, [])
        self.assertEqual([x['id'] for x in led['legacy']], ['p', 'q', 'r'])            # current, pending and past promising
        self.assertEqual(certification.verify(led), [])
        cases = {'paused': dict(ok, collection_paused=False), 'exists': dict(ok, ledger_exists=True)}
        for name, kw in cases.items():
            self.assertTrue(certification.activation_plan(report, state, self.rows(), brain.STATUS_RULES, now, **kw)[1], name)
        variants = {'old_rules': dict(report, brain=dict(report['brain'], status_rules='independent_games_hysteresis_v1')),
                    'stale': dict(report, generated_at_utc=at(-60)), 'future': dict(report, generated_at_utc=at(5)),
                    'differs': dict(report, brain=dict(report['brain'], status_list={})),
                    'active': dict(report, brain=dict(report['brain'], validation={'state': 'active'}))}
        for name, rep in variants.items():
            self.assertTrue(certification.activation_plan(rep, state, self.rows(), brain.STATUS_RULES, now, **ok)[1], name)
        self.assertTrue(certification.activation_plan(report, {'migrations': {}}, self.rows(), brain.STATUS_RULES, now, **ok)[1])


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


    def full(self, versions, files=None, register=False, patch_build=None):
        """report.build() on a temporary data dir; -> (eligible signals, data dir files after the build)."""
        from unittest.mock import patch
        from scangoal import report
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions(versions)
                for name, content in (files or {}).items():
                    with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
                        json.dump(content, f)
                if patch_build:
                    with patch.object(report.brain, 'build', patch_build(report.brain.build)):
                        report.build(None, register=register)
                else:
                    report.build(None, register=register)
                out = {}
                for name in os.listdir(d):
                    with open(os.path.join(d, name), encoding='utf-8') as f:
                        out[name] = f.read()
                return list(report.LAST['signals']), out
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def test_first_result_of_the_game_by_any_source(self):
        mid = 'czarni-sosnowiec-women-vs-sk-brann-women-20261111'
        v1 = self.version(self.OPEN, '2026-11-11T17:50:00+00:00')
        v2 = self.version(self.OPEN + '\n\n🏁 FT 1:4 · Over 5 ❌', '2026-11-11T19:40:00+00:00', '2026-11-11T19:38:00+00:00')
        v3 = self.version(self.OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅', '2026-11-11T21:00:00+00:00', '2026-11-11T20:59:00+00:00')
        b = self.full([v1, v2, v3])[0][0]
        self.assertEqual((b['result_known_utc'], b['match_result_first_seen_utc']), ('2026-11-11T21:00:00+00:00', '2026-11-11T19:40:00+00:00'))
        hl = {'results_hl.json': {'results': {mid: {'ft': [1, 5], 'matched_at_utc': '2026-11-11T19:10:00+00:00',
                                                   'event_date': '2026-11-11T17:10:00+00:00', 'bsd_id': 127}}}}
        b = self.full([v1, v3], hl)[0][0]                                              # Highlightly saw it before the bot edit
        self.assertEqual((b['result_known_utc'], b['match_result_first_seen_utc']), ('2026-11-11T21:00:00+00:00', '2026-11-11T19:10:00+00:00'))
        b = self.full([v1, v3], {'manual_results.json': {mid: {'ft': [1, 5], 'source': 'x'}}})[0][0]
        self.assertEqual(b['match_result_first_seen_utc'], '2026-11-11T21:00:00+00:00')  # an undated source supplies no observation

    def test_the_ledger_is_never_created_by_the_collection(self):
        v1 = self.version(self.OPEN, '2026-11-11T17:50:00+00:00')
        _, files = self.full([v1], register=True)
        self.assertNotIn(certification.FILE, files)
        bad = {'method': 'x'}                                                           # an invalid ledger is never extended
        _, files = self.full([v1], {certification.FILE: bad}, register=True)
        self.assertEqual(json.loads(files[certification.FILE]), bad)

class LedgerHistoryEndToEnd(unittest.TestCase):
    """Review #5911845265, P1-H: the real path (report.build) only certifies with a verified published history (git)."""
    OPEN = ReportProvenance.OPEN
    ACT = datetime(2026, 11, 1, tzinfo=UTC)

    def setUp(self):
        import subprocess
        self.sp = subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.data = os.path.join(self.root, 'data')
        os.makedirs(self.data)
        self.git('init', '-q')
        os.environ['SCANGOAL_DATA_DIR'] = self.data
        store.append_versions([{'message_id': 1, 'date_utc': '2026-11-11T17:49:00+00:00', 'edit_date_utc': None,
                                'collected_at_utc': '2026-11-11T17:50:00+00:00', 'text': self.OPEN}])
        self.reg = {'x': hyp()}
        self.write('hypotheses.json', self.reg)
        self.commit(self.ACT - timedelta(days=1))

    def tearDown(self):
        del os.environ['SCANGOAL_DATA_DIR']
        self.tmp.cleanup()

    def git(self, *args, when=None, cwd=None):
        env = dict(os.environ, GIT_AUTHOR_DATE=(when or self.ACT).isoformat(), GIT_COMMITTER_DATE=(when or self.ACT).isoformat())
        return self.sp.run(['git', '-C', cwd or self.root, '-c', 'user.name=t', '-c', 'user.email=t@invalid', '-c', 'commit.gpgsign=false', *args],
                           check=True, capture_output=True, text=True, env=env).stdout

    def write(self, name, content):
        with open(os.path.join(self.data, name), 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=1, sort_keys=True)

    def read_ledger(self):
        path = os.path.join(self.data, certification.FILE)
        return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None

    def commit(self, when):
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'data', when=when)

    def run_report(self, now, register=True, candidates=('x',)):
        from unittest.mock import patch
        from scangoal import report
        real = report.brain.build

        def build(signals, registry, min_settled, ledger=None, **kw):
            out = real(signals, registry, min_settled, ledger, **kw)
            if out['validation']['ledger_valid']:
                out['validation']['candidates'] = list(candidates)
            return out
        with patch.object(report.brain, 'build', build):
            return report.build(None, register=register, now=now)['brain']['validation']

    def published(self):
        led = certification.activate({}, self.ACT)
        self.write(certification.FILE, led)
        self.commit(self.ACT + timedelta(minutes=5))
        return led

    def test_git_history_is_insufficient_to_activate_or_append(self):
        led = self.published()
        now = self.ACT + timedelta(days=1)
        validation = self.run_report(now)
        self.assertEqual((validation['state'], validation['ledger_valid']), ('history_unverified', False))
        self.assertEqual(self.read_ledger(), led)
        self.assertEqual(certification.history_proof(self.path(), led), [])

    def test_rewritten_ledger_with_recomputed_hashes_is_refused_end_to_end(self):
        self.published()
        now = self.ACT + timedelta(days=1)
        self.run_report(now)
        self.commit(now + timedelta(minutes=10))
        rewritten = certification.activate({}, self.ACT)                               # internally valid, different history
        certification.append(rewritten, ['x'], self.reg, self.ACT + timedelta(hours=2))
        self.assertEqual(certification.verify(rewritten, self.reg), [])
        self.write(certification.FILE, rewritten)
        v = self.run_report(now + timedelta(hours=1))
        self.assertEqual((v['state'], v['ledger_valid'], v['certified'], v['candidates']), ('history_unverified', False, [], []))
        self.assertEqual(self.read_ledger(), rewritten)                                 # never extended
        report = {'generated_at_utc': at(0), 'brain': {'evidence_scope': 'eligible_bets_only', 'status_list': {'x': 'forte'}, 'validation': v,
                                                       'watch': [{'date_utc': at(0), 'kickoff_utc': at(120), 'match': 'A — B', 'selection': 'Over 2.5',
                                                                  'odds': 1.9, 'reasons': [{'id': 'x', 'status': 'forte', 'n': 120, 'hit_rate': 0.7,
                                                                                            'prob_above': 0.99, 'certified': True}]}]}}
        msgs = notify.messages(report, {'hello': True, 'statuses': {}, 'migrations': {'status_rules:' + brain.STATUS_RULES: {}}}, T0)
        self.assertFalse(any('ALERTA' in m or 'validada' in m for m in msgs))
        self.commit(now + timedelta(hours=2))                                           # even once the rewrite is committed
        self.assertTrue(certification.history_proof(os.path.join(self.data, certification.FILE), rewritten))
        self.assertEqual(self.run_report(now + timedelta(hours=3))['state'], 'history_unverified')

    def test_incomplete_or_missing_history_fails_closed(self):
        led = certification.activate({}, self.ACT)
        self.write(certification.FILE, led)                                            # never committed
        self.assertEqual(self.run_report(self.ACT + timedelta(days=1))['state'], 'history_unverified')
        self.assertEqual(self.read_ledger()['entries'], [])                            # nothing appended
        self.assertEqual(certification.verify_history([]), ['no published version: nothing proves the history'])
        self.commit(self.ACT + timedelta(minutes=5))
        clone = os.path.join(self.root, 'shallow')                                     # truncated history
        self.sp.run(['git', 'clone', '-q', '--depth', '1', 'file://' + self.root, clone], check=True, capture_output=True)
        problems = certification.history_proof(os.path.join(clone, 'data', certification.FILE), led)
        self.assertEqual(problems, ['history unavailable: shallow repository'])
        outside = tempfile.TemporaryDirectory()                                        # no git at all
        path = os.path.join(outside.name, certification.FILE)
        certification.save(__import__('pathlib').Path(path), led)
        self.assertEqual(certification.history_proof(path, led), ['history unavailable: no git repository'])
        outside.cleanup()
        os.remove(os.path.join(self.data, certification.FILE))                         # deleted, then re-created
        self.commit(self.ACT + timedelta(minutes=10))
        self.write(certification.FILE, led)
        self.commit(self.ACT + timedelta(minutes=15))
        self.assertTrue(any('deleted' in p for p in certification.history_proof(os.path.join(self.data, certification.FILE), led)))

    def path(self):
        return os.path.join(self.data, certification.FILE)

    def test_genesis_published_before_its_activation(self):
        led = certification.activate({}, self.ACT)
        self.write(certification.FILE, led)
        self.commit(self.ACT - timedelta(minutes=1))
        self.assertIn('genesis published out of time', certification.history_proof(self.path(), led))

    def test_entry_published_too_late_after_its_selection(self):
        self.published()
        late = self.read_ledger()
        certification.append(late, ['x'], self.reg, self.ACT + timedelta(days=1))
        self.write(certification.FILE, late)
        self.commit(self.ACT + timedelta(days=1, hours=2))                             # 2 h > 30 min tolerance
        self.assertIn('entry 1 published out of time', certification.history_proof(self.path(), late))
        self.assertEqual(self.run_report(self.ACT + timedelta(days=2))['state'], 'history_unverified')

    def test_unpublished_entries_must_be_after_the_last_publication_and_not_in_the_future(self):
        self.published()
        back = self.read_ledger()
        certification.append(back, ['x'], self.reg, self.ACT)                         # dated before the last publication
        self.assertIn('unpublished entry 1 out of time', certification.history_proof(self.path(), back))
        future = self.read_ledger()
        certification.append(future, ['x'], self.reg, self.ACT + timedelta(days=9))
        self.assertIn('unpublished entry 1 out of time', certification.history_proof(self.path(), future, as_of=self.ACT + timedelta(days=1)))
        self.assertEqual(certification.history_proof(self.path(), future, as_of=self.ACT + timedelta(days=9)), [])

    def test_brain_without_a_history_proof_is_closed(self):
        led = make_ledger(self.reg, [(['x'], -30)])
        g = series(THREE_IN_FOUR, every=CHAIN)
        self.assertEqual(brain.build(g, self.reg, 300, led)['validation']['state'], 'history_unverified')   # proof not performed
        self.assertEqual(brain.build(g, self.reg, 300, led, history_problems=['x'])['validation']['state'], 'history_unverified')
        out = brain.build(g, self.reg, 300, led, history_problems=[])
        self.assertEqual((out['validation']['state'], out['validation']['certified']), ('history_unverified', []))


class ThreeDayDeadline(unittest.TestCase):
    """Review #5911845265, P2-I: an admitted game without a proven result is a loss once the report clock passes 3 days."""
    SINCE = T0 - timedelta(days=1)

    def lonely(self):
        return [sig('o', None, 0)]                                                    # admitted, never settled, nothing after it

    def test_just_before_and_just_after_the_deadline(self):
        before = brain.confirmation(self.lonely(), self.SINCE, 0.025, as_of=T0 + timedelta(days=2, hours=23))
        self.assertEqual((before['admitted'], before['games'], before['pending'], before['voided_as_loss']), (1, 0, 1, 0))
        after = brain.confirmation(self.lonely(), self.SINCE, 0.025, as_of=T0 + timedelta(days=3, hours=1))
        self.assertEqual((after['admitted'], after['games'], after['pending'], after['voided_as_loss']), (1, 1, 0, 1))
        self.assertLess(after['log10_e'], 0)
        exact = brain.confirmation(self.lonely(), self.SINCE, 0.025, as_of=T0 + timedelta(days=3))
        self.assertEqual(exact['voided_as_loss'], 1)                                  # closes at the deadline itself

    def test_no_clock_never_anticipates_a_loss(self):
        c = brain.confirmation(self.lonely(), self.SINCE, 0.025)
        self.assertEqual((c['games'], c['pending']), (0, 1))

    def test_late_result_after_the_deadline_stays_a_loss_and_same_instant_same_answer(self):
        late = [sig('o', 1.0, 0, known=4 * 24 * 60)]                                    # result seen on day 4: too late
        c1 = brain.confirmation(late, self.SINCE, 0.025, as_of=T0 + timedelta(days=5))
        c2 = brain.confirmation(self.lonely(), self.SINCE, 0.025, as_of=T0 + timedelta(days=5))
        self.assertEqual((c1['games'], c1['voided_as_loss'], c1['log10_e']), (c2['games'], c2['voided_as_loss'], c2['log10_e']))
        reg = {'a': hyp()}
        led = make_ledger(reg, [(['a'], -30)])
        g = series(THREE_IN_FOUR[:40], every=CHAIN) + [sig('open', None, 41 * CHAIN)]
        a = brain.build(g, reg, 300, led, history_problems=[], as_of=T0 + timedelta(days=30))
        b = brain.build(g, reg, 300, led, history_problems=[], as_of=T0 + timedelta(days=30))
        self.assertEqual(a['validation'], b['validation'])                             # register=False/True use the same clock


class PublishedLedgerConsistency(unittest.TestCase):
    """Review #5912303516, P2-J: the report and the ledger written by the same register=True run describe the same state."""
    OPEN, ACT = LedgerHistoryEndToEnd.OPEN, LedgerHistoryEndToEnd.ACT
    git, write, read_ledger, commit = LedgerHistoryEndToEnd.git, LedgerHistoryEndToEnd.write, LedgerHistoryEndToEnd.read_ledger, LedgerHistoryEndToEnd.commit
    published, tearDown = LedgerHistoryEndToEnd.published, LedgerHistoryEndToEnd.tearDown

    def setUp(self):
        LedgerHistoryEndToEnd.setUp(self)
        # 40 real bot signals of one tactic (rhythm < 90), one every 3 h, each settled by an edit 2 h later: a genuine
        # promising hypothesis built through report.load/build_bets, not injected.
        versions = []
        for i in range(40):
            sent = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(hours=3 * i)
            text = self.OPEN.replace('czarni-sosnowiec-women-vs-sk-brann-women-20261111', 'club-a%d-vs-club-b%d-20261102' % (i, i))
            versions.append({'message_id': 100 + i, 'date_utc': sent.isoformat(), 'edit_date_utc': None,
                             'collected_at_utc': (sent + timedelta(minutes=1)).isoformat(), 'text': text})
            versions.append({'message_id': 100 + i, 'date_utc': sent.isoformat(), 'edit_date_utc': (sent + timedelta(hours=2)).isoformat(),
                             'collected_at_utc': (sent + timedelta(hours=2, minutes=5)).isoformat(), 'text': text + '\\n\\n🏁 FT 1:5 · Over 5 ✅'})
        store.append_versions(versions)
        self.reg = {'x': {'family': 'Jogo aberto', 'conditions': [['Ritmo indicado pelo bot', 'menos de 90']],
                          'born_utc': '2026-11-01T00:00:00+00:00', 'source': 'gerador'}}
        self.write('hypotheses.json', self.reg)
        self.commit(self.ACT - timedelta(hours=1))
        self.published()

    def real_report(self, now, register=True):
        from scangoal import report
        return report.build(None, register=register, now=now)['brain']

    def test_reporting_preserves_a_ledger_without_independent_receipts(self):
        now = datetime(2026, 11, 7, 12, tzinfo=UTC)
        before = self.read_ledger()
        for register in (True, False):
            validation = self.real_report(now, register=register)['validation']
            self.assertEqual((validation['state'], validation['newly_selected'], validation['ledger_entries']),
                             ('history_unverified', [], 0))
            self.assertEqual(self.read_ledger(), before)


class AsOfHasNoFutureInformation(unittest.TestCase):
    """Review #5912303516: an evaluation at as_of uses only signals read and results seen by then."""
    AS_OF = T0 + timedelta(minutes=60 * CHAIN)

    def full(self):
        early = series(THREE_IN_FOUR[:60], every=CHAIN)
        later = series([1.0] * 60, start=61, every=CHAIN)                                 # read after as_of
        straddle = [sig('s', 1.0, 59 * CHAIN + 60, known=600)]                           # read before, result seen after
        return early + later + straddle

    def as_known_then(self):
        early = series(THREE_IN_FOUR[:60], every=CHAIN)
        return early + [sig('s', None, 59 * CHAIN + 60)]                                  # still open at as_of

    def test_future_signals_and_results_change_nothing(self):
        reg = {'a': hyp()}
        led = make_ledger(reg, [(['a'], -30)])
        a = brain.build(self.full(), reg, 300, led, history_problems=[], as_of=self.AS_OF)
        b = brain.build(self.as_known_then(), reg, 300, led, history_problems=[], as_of=self.AS_OF)
        for key in ('status_list', 'counts', 'validation', 'walk_forward', 'tested'):
            self.assertEqual(a[key], b[key], key)
        ta = {r['id']: r for r in a['top']}['a']
        tb = {r['id']: r for r in b['top']}['a']
        self.assertEqual((ta['games'], ta['confirmation']), (tb['games'], tb['confirmation']))
        self.assertNotEqual(brain.build(self.full(), reg, 300, led, history_problems=[])['validation'], a['validation'])  # control

    def test_a_signal_read_exactly_at_as_of_exists_with_its_window_open(self):
        b = sig('edge', None, 60 * CHAIN)
        self.assertEqual(len(chronology.as_of_view([b], self.AS_OF)), 1)
        c = brain.confirmation([b], T0 - timedelta(days=1), 0.025, as_of=self.AS_OF)
        self.assertEqual(c['skipped']['decision_window_open'], 1)

    def test_view_rules(self):
        t = self.AS_OF
        read_later = sig('r', 1.0, 0, seen=int((t - T0).total_seconds() // 60) + 1)
        seen_later = sig('k', 1.0, 0, known=int((t - T0).total_seconds() // 60) + 10)
        view = chronology.as_of_view([read_later, seen_later, sig('ok', 1.0, 0)], t)
        self.assertEqual([b['match_id'] for b in view], ['k', 'ok'])
        self.assertEqual((view[0]['w'], view[0]['result_known_utc'], view[0]['match_result_first_seen_utc']), (None, None, None))
        self.assertEqual(view[1]['w'], 1.0)

    def test_decision_window_still_open_is_not_a_candidate(self):
        g = [sig('w', 1.0, 0, known=145)]
        c = brain.confirmation(g, T0 - timedelta(days=1), 0.025, as_of=T0 + timedelta(minutes=10))   # read + 15 min not over
        self.assertEqual((c['admitted'], c['skipped']['decision_window_open']), (0, 1))
        c = brain.confirmation(g, T0 - timedelta(days=1), 0.025, as_of=T0 + timedelta(hours=3))
        self.assertEqual((c['admitted'], c['games']), (1, 1))


class AsOfNeedsProofOfReading(unittest.TestCase):
    """Review #5912461690, P2-K: in an evaluation at as_of = T, a signal only exists if its first reading is proven by T."""
    T = T0 + timedelta(hours=10)

    def test_missing_invalid_or_contradictory_reading_is_absent(self):
        cases = {'missing': None, 'empty': '', 'garbage': 'ontem', 'naive': '2026-11-20T01:00:00', 'number': 7,
                 'read_before_sent': at(-30)}                                               # contradictory (> 5 min before sending)
        for name, seen in cases.items():
            b = dict(sig('m', 1.0, 0), signal_seen_utc=seen)                                # sent long before T
            self.assertEqual(chronology.as_of_view([b], self.T), [], name)
        self.assertEqual(len(chronology.as_of_view([sig('ok', 1.0, 0)], self.T)), 1)        # control: proven reading

    def test_unproven_signals_never_shape_statuses_at_as_of(self):
        reg = {'a': hyp()}
        proven = series([1.0, -1.0] * 15, every=CHAIN)                                     # 30 games at break-even
        unproven = [dict(b, signal_seen_utc=None) for b in series([1.0] * 40, start=100, every=10)]   # sent before T, no reading
        t = T0 + timedelta(days=30)
        with_unproven = brain.build(proven + unproven, reg, 300, as_of=t)
        without = brain.build(proven, reg, 300, as_of=t)
        for key in ('status_list', 'counts', 'walk_forward', 'tested'):
            self.assertEqual(with_unproven[key], without[key], key)
        self.assertEqual({r['id']: r['games'] for r in with_unproven['top']}, {r['id']: r['games'] for r in without['top']})
        self.assertNotEqual(brain.build(proven + unproven, reg, 300)['status_list'], without['status_list'])   # control: no as_of


if __name__ == '__main__':
    unittest.main()


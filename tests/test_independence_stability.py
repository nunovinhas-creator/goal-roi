"""PR 2: independent statistical units and status stability (offline, synthetic and deterministic)."""
import copy
import random
import unittest
from datetime import datetime, timedelta

from scangoal import brain, certification, insights, notify
from scangoal.eligibility import eligible, exclusion_reasons

NOW = datetime.fromisoformat('2026-11-20T12:00:00+00:00')
BAR = 0.9997   # corrected bar of the same order as production (≈120 tested hypotheses)


def sig(match, w, k, *, market='Asian Total', selection='Over 2.5', odds=2.0, typ='OPEN_GAME', phase='live', **extra):
    """One eligible signal; k orders games in time (results known 150 min after the signal).

    PR 3 provenance: the system read the signal when it was sent and first observed the result 145 min later (available
    150 min after the signal, with the 5-minute observation margin) - the same timing these fixtures always assumed.
    """
    d = '2026-11-%02dT%02d:00:00+00:00' % (1 + k // 20, k % 20)
    seen = (datetime.fromisoformat(d) + timedelta(minutes=145)).isoformat() if w is not None else None
    return dict({'match_id': match, 'w': w, 'odds': odds, 'type': typ, 'phase': phase, 'market': market, 'selection': selection,
                 'date_utc': d, 'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga', 'teams': ['A', 'B'],
                 'in_roi': True, 'key': (match, market, selection, d), 'n_signals': 1, 'max_mult': 1, 'no_source': False,
                 'share': None, 'total': None, 'n_bets': None, 'signal_seen_utc': d, 'result_known_utc': seen,
                 'match_result_first_seen_utc': seen}, **extra)


def games(outcomes, typ='OPEN_GAME', start=0):
    """One signal per game, chronological."""
    return [sig('g%d' % (start + i), w, start + i, typ=typ) for i, w in enumerate(outcomes)]


def family_only(family='Jogo aberto'):
    return {'h': {'family': family, 'conditions': [], 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}}


def evaluate(signals, registry=None):
    rows, tested, bar = brain.evaluate(signals, registry or family_only(), 300)
    return {r['id']: r for r in rows}, tested, bar


def steps(probs, start_games=30):
    return [(start_games + i, p, 't%d' % i) for i, p in enumerate(probs)]


class IndependentUnits(unittest.TestCase):
    def test_repeated_signals_on_the_same_bet_count_once(self):
        v = insights.Votes()
        for _ in range(3):
            v.add(sig('m1', 1.0, 0))
        v.add(sig('m2', -1.0, 1))
        self.assertEqual(v.totals(), (1.0, 1.0, 0.5, 2))
        self.assertEqual(v.bets(), 2)

    def test_correlated_markets_in_one_game_share_one_vote(self):
        over = sig('m', 1.0, 0, typ='POLYMARKET', market='Asian Total', selection='Over 2.5')
        winner = sig('m', -1.0, 0, typ='POLYMARKET', market='1X2', selection='1')
        v = insights.Votes()
        for b in (over, over, over, winner):   # the repeated Over 2.5 is one bet, not three
            v.add(b)
        h, m, be, g = v.totals()
        self.assertEqual((h, m, g, v.bets()), (0.5, 0.5, 1, 2))
        s = insights.summarize([over, over, over, winner])
        self.assertEqual((s['n'], s['bets'], s['games']), (4, 2, 1))

    def test_contradictory_results_on_one_bet_keep_their_mass(self):
        v = insights.Votes()
        v.add(sig('m', 1.0, 0))
        v.add(sig('m', -1.0, 0))
        self.assertEqual(v.totals()[:2], (0.5, 0.5))

    def test_totals_do_not_depend_on_input_order(self):
        rows = [sig('m%d' % (i % 7), random.Random(i).choice([1.0, -1.0, 0.5]), i, odds=1.5 + i / 10,
                    market=('1X2', 'Asian Total')[i % 2], selection=('1', 'Over 2.5')[i % 2], typ='POLYMARKET') for i in range(40)]
        a = insights.Votes()
        for r in rows:
            a.add(r)
        shuffled = rows[:]
        random.Random(7).shuffle(shuffled)
        b = insights.Votes()
        for r in shuffled:
            b.add(r)
        self.assertEqual(a.totals(), b.totals())


class ThresholdsUseIndependentGames(unittest.TestCase):
    def test_many_signals_from_the_same_game_never_reach_a_status(self):
        # 40 winning signals in 10 games (four markets each): 40 "results" before, 10 independent games now.
        signals = [sig('m%d' % g, 1.0, g, typ='POLYMARKET', market=mk, selection=sl)
                   for g in range(10) for mk, sl in (('1X2', '1'), ('Asian Total', 'Over 2.5'), ('Asian Total', 'Over 1.5'), ('BTTS', 'Yes'))]
        rows, tested, _ = evaluate(signals, family_only('Polymarket'))
        r = rows['h']
        self.assertEqual((r['n'], r['bets'], r['games']), (40, 40, 10))
        self.assertGreaterEqual(r['prob_above'], 0.95)
        self.assertEqual(brain.status_of(r['n'], r['prob_above'], BAR, 300), 'promissora')   # what counting signals concluded
        self.assertEqual(r['status'], 'em observação')                                         # 10 games are not 30
        self.assertEqual(tested, 0)                                                            # nor a tested hypothesis

    def test_stake_weekend_pattern_33_signals_14_games(self):
        # Audited example: "Stake | Dia = fim de semana" had 33 results in 14 games. Losing heavily, it was "abandonada".
        signals = []
        for g in range(14):
            for j in range(3 if g < 5 else 2):
                signals.append(sig('s%d' % g, -1.0 if (g + j) % 4 else 1.0, g, typ='STAKE_LOAD', market='Asian Total', selection='Over %d.5' % j))
        rows, _, _ = evaluate(signals, family_only('Stake'))
        r = rows['h']
        self.assertEqual((r['n'], r['games']), (33, 14))
        self.assertLessEqual(r['prob_above'], 0.10)
        self.assertEqual(brain.status_of(r['n'], r['prob_above'], BAR, 300), 'abandonada')
        self.assertEqual(r['status'], 'em observação')

    def test_board_agenda_and_walk_forward_need_independent_games(self):
        # 12 winning signals over 4 games (three markets each): >= BOARD_MIN signals, < BOARD_MIN games.
        signals = [sig('m%d' % g, 1.0, g, typ='POLYMARKET', market=mk, selection='x')
                   for g in range(4) for mk in ('1X2', 'BTTS', 'Asian Total')]
        reg = {'h': {'family': 'Polymarket', 'conditions': [['Momento', 'durante o jogo']], 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}}
        rows, _, _ = evaluate(signals, reg)
        r = rows['h']
        self.assertEqual((r['n'], r['games']), (12, 4))
        self.assertGreaterEqual(r['prob_above'], brain.BOARD_GOOD)          # good enough for the board if signals counted
        self.assertEqual(brain.board([r], NOW)['best'], [])
        open_ = dict(sig('live', None, 50, typ='POLYMARKET', market='1X2', selection='x'), date_utc=NOW.isoformat())
        r_open = dict(r, open=[open_])
        self.assertEqual(brain.agenda([r_open], signals + [open_], NOW)['counts']['favorável'], 0)
        later = [sig('z%d' % i, -1.0, 40 + i, typ='POLYMARKET', market='1X2', selection='y') for i in range(3)]
        wf = brain.walk_forward(signals + later, reg)
        self.assertEqual((wf['followed']['bets'], wf['skipped']['bets']), (0, 15))   # 4 known games < BOARD_MIN = 8 games


class Stability(unittest.TestCase):
    def test_promotion_at_the_edge_needs_a_second_game(self):
        st, pending, changes = brain.stable_status(steps([0.90, 0.92, 0.94, 0.956]), BAR, 300)
        self.assertEqual((st, pending, changes), ('em observação', 'promissora', []))
        st, pending, changes = brain.stable_status(steps([0.90, 0.92, 0.94, 0.956, 0.937]), BAR, 300)
        self.assertEqual((st, pending, changes), ('em observação', None, []))     # the next loss: nothing happened
        st, _, changes = brain.stable_status(steps([0.90, 0.92, 0.94, 0.956, 0.967]), BAR, 300)
        self.assertEqual((st, len(changes)), ('promissora', 1))                    # confirmed by a second game

    def test_small_oscillations_do_not_change_status_in_series(self):
        flip = [0.94, 0.96] * 12                     # crosses 0.95 every game: 23 changes when the status had no memory
        stateless = [brain.status_of(30 + i, p, BAR, 300) for i, p in enumerate(flip)]
        self.assertEqual(sum(a != b for a, b in zip(stateless, stateless[1:])), 23)
        self.assertEqual(brain.stable_status(steps(flip), BAR, 300)[2], [])
        promoted_then_noisy = [0.96, 0.97] + [0.93, 0.955] * 10
        st, _, changes = brain.stable_status(steps(promoted_then_noisy), BAR, 300)
        self.assertEqual((st, len(changes)), ('promissora', 1))                    # 0.93 is inside the band: stays
        noisy_low = [0.08, 0.06] + [0.12, 0.18] * 10
        st, _, changes = brain.stable_status(steps(noisy_low), BAR, 300)
        self.assertEqual((st, len(changes)), ('abandonada', 1))

    def test_genuine_deterioration_is_detected_with_bounded_delay(self):
        path = [0.97, 0.98, 0.97, 0.95, 0.92, 0.89, 0.85, 0.80, 0.60, 0.30, 0.09, 0.05, 0.04]
        st, _, changes = brain.stable_status(steps(path), BAR, 300)
        self.assertEqual(st, 'abandonada')
        self.assertEqual([(c['from'], c['to'], c['games']) for c in changes],
                         [('em observação', 'promissora', 31),     # second game >= 0.95
                          ('promissora', 'em observação', 36),     # second consecutive game below 0.90 (35, 36)
                          ('em observação', 'abandonada', 41)])    # second consecutive game <= 0.10 (40, 41)

    def test_legitimate_return_to_observation_and_out_of_abandonment(self):
        st, _, changes = brain.stable_status(steps([0.97, 0.97, 0.91, 0.89, 0.88]), BAR, 300)
        self.assertEqual((st, changes[-1]['to']), ('em observação', 'em observação'))
        st, _, changes = brain.stable_status(steps([0.05, 0.05, 0.15, 0.19, 0.25, 0.30]), BAR, 300)
        self.assertEqual((st, [c['to'] for c in changes]), ('em observação', ['abandonada', 'em observação']))

    def test_status_held_by_hysteresis_is_visible(self):
        # Promoted with 21/9 and 22/9, then two losses: 0.92 is below entry but inside the keep band.
        signals = games([1.0] * 19 + [-1.0] * 9 + [1.0, 1.0, -1.0, -1.0])
        rows, _, _ = evaluate(signals)
        r = rows['h']
        self.assertLess(r['prob_above'], brain.PROMOTE_AT)
        self.assertGreaterEqual(r['prob_above'], brain.KEEP_PROMISING)
        self.assertEqual((r['status'], r['entry_status'], r['pending_status']), ('promissora', 'em observação', None))
        top = brain.build(signals, family_only(), 300)['top'][0]
        self.assertEqual((top['status'], top['entry_status']), ('promissora', 'em observação'))

    def test_validated_status_keeps_a_doubled_error_band(self):
        keep = 1 - 2 * (1 - BAR)
        self.assertEqual(brain.next_status('forte', 120, BAR - (BAR - keep) / 2, BAR, 300), 'forte')
        self.assertEqual(brain.next_status('em observação', 120, BAR - (BAR - keep) / 2, BAR, 300), 'promissora')
        self.assertEqual(brain.next_status('forte', 120, keep - 1e-6, BAR, 300), 'promissora')
        self.assertEqual(brain.next_status('forte', 300, BAR, BAR, 300), 'confirmada')
        self.assertEqual(brain.next_status('promissora', 29, 0.99, BAR, 300), 'em observação')

    def test_no_new_evidence_no_change_and_order_independence(self):
        outcomes = [1.0] * 22 + [-1.0] * 11
        random.Random(3).shuffle(outcomes)
        signals = games(outcomes)
        a, _, _ = evaluate(signals)
        b, _, _ = evaluate(copy.deepcopy(signals)[::-1])
        self.assertEqual({k: (r['status'], r['pending_status'], r['status_changes']) for k, r in a.items()},
                         {k: (r['status'], r['pending_status'], r['status_changes']) for k, r in b.items()})
        before = copy.deepcopy(signals)
        evaluate(signals)
        self.assertEqual(signals, before)    # evaluation never mutates the signals it reads

    def test_replay_final_step_matches_published_probability(self):
        signals = games([1.0, -1.0, 1.0] * 15)
        rows, _, _ = evaluate(signals)
        last = brain.game_steps(signals)[-1]
        self.assertEqual((last[0], last[1]), (rows['h']['games'], rows['h']['prob_above']))


class KashiwaKawasakiCase(unittest.TestCase):
    """Four redundant hypotheses over the same games, just below 0.95, receive one more win (15/11/2026 10:00 UTC)."""

    def setUp(self):
        base = [1.0] * 19 + [-1.0] * 11
        random.Random(11).shuffle(base)
        self.history = games(base + [1.0, 1.0])            # 32 games, 21/11: p = 0.9414
        idx = brain.earlier_index(self.history)
        # Conditions shared by every signal (day and hour vary): four hypotheses over exactly the same games, as in production.
        atoms = sorted(set.intersection(*(set(brain.atoms(dict(b), set(), idx)) for b in self.history)) - {('Dia', 'fim de semana'), ('Dia', 'dia de semana')})
        picks = [[], [list(atoms[0])], [list(atoms[1])], [list(atoms[0]), list(atoms[1])]]
        self.registry = {'h%d' % i: {'family': 'Jogo aberto', 'conditions': c, 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}
                         for i, c in enumerate(picks)}

    def report(self, signals, stateless=False):
        rows, _, bar = brain.evaluate(signals, self.registry, 300)
        if stateless:   # PR1 behaviour: entry thresholds only, no memory, no rules key
            st = {r['id']: brain.status_of(r['games'], r['prob_above'], bar, 300) for r in rows}
            return {'brain': {'evidence_scope': 'eligible_bets_only', 'status_list': {k: v for k, v in st.items() if v != 'em observação'}}}
        return {'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES,
                          'status_list': {r['id']: r['status'] for r in rows if r['status'] != 'em observação'}}}

    def notices(self, sequence, stateless):
        state = {'hello': True, 'statuses': {}}
        notify.messages(self.report(self.history, stateless), state, NOW)        # baseline (silent migration)
        sent, signals = [], list(self.history)
        for k, w in enumerate(sequence):
            signals.append(sig('next%d' % k, w, 100 + k))
            sent.append(sum(m.startswith('Hipótese') for m in notify.messages(self.report(signals, stateless), state, NOW)))
        return sent

    def test_one_win_at_the_edge_no_longer_announces_four_promotions(self):
        rows, _, _ = brain.evaluate(self.history, self.registry, 300)
        self.assertEqual({round(r['prob_above'], 4) for r in rows}, {0.9414})
        self.assertEqual(self.notices([1.0], stateless=True), [4])     # what production sent at 10:00 UTC
        self.assertEqual(self.notices([1.0], stateless=False), [0])    # pending confirmation, nothing sent

    def test_win_then_loss_is_silent_and_win_then_win_promotes_once(self):
        self.assertEqual(self.notices([1.0, -1.0], stateless=True), [4, 4])     # promote four, then demote four
        self.assertEqual(self.notices([1.0, -1.0], stateless=False), [0, 0])
        self.assertEqual(self.notices([1.0, 1.0, -1.0], stateless=False), [0, 4, 0])   # confirmed promotion; one loss keeps it

    def test_redundant_hypotheses_are_still_four_notices_when_confirmed(self):
        # Redundancy (same games behind several hypotheses) is PR 3/5 scope: documented, not hidden.
        rows, _, _ = brain.evaluate(self.history + [sig('a', 1.0, 100), sig('b', 1.0, 101)], self.registry, 300)
        self.assertEqual({r['status'] for r in rows}, {'promissora'})
        self.assertEqual(len({(r['games'], r['hits']) for r in rows}), 1)


class TelegramRulesMigration(unittest.TestCase):
    def pr1_state(self):
        return {'hello': True, 'statuses': {'a': 'promissora', 'b': 'abandonada', 'c': 'promissora'}, 'watch_sent': ['k'], 'sent_total': 269,
                'summary_day': '2026-11-14', 'regime': {}, 'operational_regime': {'OPEN_GAME': 'estável'},
                'migrations': {notify.STATUS_MIGRATION: {'baseline_statuses': {'a': 'promissora'}}}}

    def pr2_report(self, statuses):
        return {'generated_at_utc': NOW.isoformat(), 'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES,
                                                               'status_list': statuses},
                'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]}}

    def test_rule_change_rebaselines_silently_once_and_keeps_history(self):
        state, before = self.pr1_state(), self.pr1_state()
        self.assertEqual(notify.messages(self.pr2_report({'c': 'promissora', 'd': 'promissora'}), state, NOW), [])
        key = notify.migration_key(self.pr2_report({}))
        self.assertEqual(key, 'status_rules:' + brain.STATUS_RULES)
        self.assertEqual(state['migrations'][key]['previous_statuses'], before['statuses'])
        self.assertEqual(state['migrations'][notify.STATUS_MIGRATION], before['migrations'][notify.STATUS_MIGRATION])   # PR1 marker intact
        for k in ('watch_sent', 'sent_total', 'summary_day', 'regime', 'operational_regime', 'hello'):
            self.assertEqual(state[k], before[k], k)
        frozen = copy.deepcopy(state)
        self.assertEqual(notify.messages(self.pr2_report({'c': 'promissora', 'd': 'promissora'}), state, NOW), [])
        self.assertEqual(state, frozen)                                                                    # idempotent
        self.assertEqual(notify.messages(self.pr2_report({'c': 'promissora'}), state, NOW),
                         ['Hipótese voltou a "em observação":\nd'])                                         # genuine change still notified

    def test_pr1_reports_keep_the_original_key(self):
        pr1 = {'brain': {'evidence_scope': 'eligible_bets_only', 'status_list': {}}}
        self.assertEqual(notify.migration_key(pr1), notify.STATUS_MIGRATION)
        state = self.pr1_state()
        self.assertFalse(notify.migrate_status_reference(pr1, state, NOW))   # the PR1 migration is never repeated

    def test_brain_report_declares_rules_and_units(self):
        rows = brain.build(games([1.0] * 5), family_only(), 300)
        self.assertEqual(rows['status_rules'], brain.STATUS_RULES)
        self.assertEqual((rows['rules']['unit'], rows['rules']['confirm_steps'], rows['rules']['keep_promising']),
                         ('independent_games', brain.CONFIRM_STEPS, brain.KEEP_PROMISING))
        self.assertEqual(rows['walk_forward']['rule']['unit'], 'independent_games')



class GameIdentity(unittest.TestCase):
    """Review #5910343843 (P1): without a provable match_id there is no independent game, ever."""

    def unidentified(self, count, w=1.0, match_id=None):
        return [sig(match_id, w, i, selection='Over %d.5' % (i % 4)) for i in range(count)]   # distinct objects and bets

    def test_31_wins_without_match_id_are_not_31_games(self):
        rows = self.unidentified(31)
        votes = insights.Votes()
        for r in rows:
            votes.add(r)
        self.assertEqual((votes.totals()[3], votes.bets(), votes.unidentified), (0, 0, 31))
        s = insights.summarize(rows)
        self.assertEqual((s['n'], s['games'], s['unidentified'], s['prob_above']), (31, 0, 31, None))
        # What one invented game per signal would have concluded (commit 1635c30): 31 "games", promissora.
        self.assertEqual(brain.status_of(31, insights.evidence(31, 0, 0.5)['prob_above'], BAR, 300), 'promissora')
        for exploratory in (False, True):
            got, tested, _ = brain.evaluate(rows, family_only(), 300, exploratory=exploratory)
            self.assertEqual((got[0]['games'], got[0]['status'], got[0]['pending_status'], tested), (0, 'em observação', None, 0), exploratory)
        self.assertEqual(brain.game_steps(rows), [])

    def test_blank_or_non_text_match_id_is_not_an_identity(self):
        for bad in (None, '', '   ', 0, 17, ['x']):
            self.assertIn('no_match_identity', exclusion_reasons(sig(bad, 1.0, 0)), repr(bad))
            v = insights.Votes()
            v.add(sig(bad, 1.0, 0))
            self.assertEqual((v.totals()[3], v.unidentified), (0, 1), repr(bad))
        self.assertTrue(eligible(sig('jogo-1', 1.0, 0)))

    def test_unidentified_signals_never_add_games_to_identified_ones(self):
        rows = self.unidentified(31) + games([1.0] * 10)
        s = insights.summarize(rows)
        self.assertEqual((s['games'], s['unidentified']), (10, 31))
        got, tested, _ = evaluate(rows)
        self.assertEqual((got['h']['n'], got['h']['games'], got['h']['status'], tested), (10, 10, 'em observação', 0))   # 31 are ineligible

    def test_missing_selection_never_splits_a_game_or_a_bet(self):
        a = sig('m', 1.0, 0, selection=None)
        b = sig('m', -1.0, 0, selection='')
        c = sig('m', 1.0, 0, selection='Over 2.5')
        for r in (a, b):
            self.assertIn('no_selection', exclusion_reasons(r))
        self.assertEqual(exclusion_reasons(c), [])
        v = insights.Votes()
        for r in (a, b, c):
            v.add(r)
        self.assertEqual(v.bets(), 2)                       # missing selections merge into one bet of that market
        self.assertEqual(v.totals(), (0.75, 0.25, 0.5, 1))  # (0.5 + 1.0) / 2 hits; still one game

    def test_unidentified_results_cannot_enable_following(self):
        # A condition every signal has (walk-forward only follows hypotheses with conditions).
        reg = {'h': {'family': 'Jogo aberto', 'conditions': [['Parte do jogo', '2.ª parte']], 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}}
        early = self.unidentified(12)
        identified = [sig('e%d' % i, 1.0, i) for i in range(12)]
        late_known = [sig('later%d' % i, -1.0, 60 + i) for i in range(3)]
        # Control: with 12 identified games the rule follows (2 early signals once 8 results are known, plus the 3 later).
        self.assertEqual(brain.walk_forward(identified + late_known, reg)['followed']['bets'], 5)
        late = [sig('later%d' % i, -1.0, 60 + i) for i in range(3)]
        wf = brain.walk_forward(early + late, reg)
        self.assertEqual(wf['followed']['bets'], 0)


class CorrectedBarIsChronological(unittest.TestCase):
    """PR 2 documented limitation (review #5910343843), fixed in PR 3. In PR 2 the corrected bar used TODAY's count of tested
    hypotheses for the whole replay, so the same 102 games gave 'forte' with 1 tested hypothesis and 'promissora' with 401:
    a validated status changed without any new game of that hypothesis. In PR 3 (review #5911487915) validation is an
    e-process on games after the selection, with an alpha budget fixed by the order of selection: hypotheses that reach 30
    games (or are selected) later never change an earlier certificate."""

    def test_validated_status_no_longer_depends_on_hypotheses_tested_later(self):
        base = ([1.0, 1.0, 1.0, -1.0] * 26)[:102]
        own = games(base)                                               # 102 games of "Jogo aberto" (days 1-6)
        others = [sig('s%d' % i, 1.0 if i % 2 else -1.0, 200 + i, typ='STAKE_LOAD') for i in range(30)]   # days 11-12
        alone = {'a': family_only()['h']}
        crowded = dict(alone, **{'s%d' % k: dict(family_only('Stake')['h']) for k in range(400)})
        # Selection registered in real time before a's games (ledger), rank 1; the 400 others are never selected.
        led = certification.activate({}, datetime.fromisoformat('2026-10-31T00:00:00+00:00'))
        certification.append(led, ['a'], alone, datetime.fromisoformat('2026-10-31T12:00:00+00:00'))
        sel = lambda reg: certification.selections(led, reg)[0]
        rows1, tested1, _ = brain.evaluate(own + others, alone, 300, selected=sel(alone))
        rows2, tested2, _ = brain.evaluate(own + others, crowded, 300, selected=sel(crowded))
        a1, a2 = rows1[0], next(r for r in rows2 if r['id'] == 'a')
        self.assertEqual((a1['games'], a1['prob_above']), (a2['games'], a2['prob_above']))   # same games, same evidence
        self.assertEqual((tested1, tested2), (1, 401))                                       # today's family differs
        self.assertEqual((a1['status'], a2['status']), ('forte', 'forte'))                   # PR 2: ('forte', 'promissora')
        self.assertEqual(a1['status_changes'], a2['status_changes'])
        self.assertEqual(a1['confirmation'], a2['confirmation'])
        self.assertEqual(a2['confirmation']['rank'], 1)                                      # budget fixed in the ledger
        self.assertNotIn(brain.evaluate(own + others, alone, 300)[0][0]['status'], brain.VALIDATED)   # no ledger: never validated


if __name__ == '__main__':
    unittest.main()

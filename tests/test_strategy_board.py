"""Issue #56 — strategy board: adversarial tests that try to make the board declare a profit it has not shown.

Offline and deterministic. Synthetic rows only; nothing touches data/, the network or Telegram.
"""
import copy
import json
import random
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from test_chronological_validity import T0, hyp, rows_by_id, series, sig
from scangoal import brain, llm, strategy_board as sb

AS_OF = brain.AS_OF_DIMENSIONS
POST = brain.POST
NO_VALIDATION = {'state': 'not_activated'}


def econ(confirmed, roi, se=0.05, days=12, share=0.1, phases=None, **extra):
    wins = extra.pop('wins', confirmed // 2)
    base = dict(admitted=confirmed, results_confirmed=confirmed, wins=wins, losses=confirmed - wins, voids=0,
                imputed_losses=0, pending=0, quarantined=0, roi_advertised=roi, roi_odds_minus_2pct=roi,
                se_day_clustered_odds_minus_2pct=se, decision_days=days, largest_day_share=share,
                phases=phases if phases is not None else {'live': confirmed}, confirmed_hit_rate=0.5,
                mean_advertised_odds=2.0)
    base.update(extra)
    return base


def row(hid='Jogo aberto | Minuto = 31 a 60', *, conditions=(('Minuto', '31 a 60'),), economics=None, **extra):
    out = {'id': hid, 'family': hid.split(' | ')[0], 'conditions': [list(c) for c in conditions], 'source': 'gerador',
           'born_utc': '2026-11-01T00:00:00+00:00', 'born_verified': True, 'experimental': False,
           'status': 'em observação', 'expect': None, 'game_set': hid, 'confirmation': None,
           'prospective': {'economics': economics if economics is not None else econ(0, None, se=None, days=0)}}
    out.update(extra)
    return out


def board(rows, **kw):
    return sb.build(rows, kw.pop('validation', NO_VALIDATION), as_of_dimensions=AS_OF, post_dimensions=POST, **kw)


def classify_one(r, **kw):
    full = []
    sb.build([r], kw.pop('validation', NO_VALIDATION), as_of_dimensions=AS_OF, post_dimensions=POST, full=full, **kw)
    return full[0]


class FrozenCriteria(unittest.TestCase):
    def test_criteria_are_frozen_by_hash(self):
        self.assertEqual(sb.criteria_sha256(), sb.CRITERIA_SHA256)

    def test_changing_a_threshold_after_seeing_results_breaks_the_hash(self):
        changed = dict(sb.CRITERIA, decision_min_results=20)
        self.assertNotEqual(sb.criteria_sha256(changed), sb.CRITERIA_SHA256)

    def test_shortlist_registration_is_frozen(self):
        data = sb.load_shortlist()
        self.assertEqual(data['registered_utc'], '2026-11-23T14:00:00Z')
        self.assertEqual(len(data['entries']), 2)

    def test_hypothesis_edited_after_registration_is_refused(self):
        data = json.loads(sb.SHORTLIST_PATH.read_text(encoding='utf-8'))
        data['entries'][0]['conditions'][1][1] = 'odd 2,00 a 3,00'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                sb.load_shortlist(path)


class NeverValidated(unittest.TestCase):
    def test_validated_is_empty_and_verdict_says_not_demonstrated(self):
        result = board([row(economics=econ(400, 0.30, se=0.01, days=60, share=0.03))])
        self.assertEqual(result['counts']['validada'], 0)
        self.assertEqual(result['validated'], [])
        self.assertEqual(result['verdict'], 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA')
        self.assertEqual(result['statistical_claim'], 'none')

    def test_perfect_statistics_with_announced_odds_only_stay_insufficient(self):
        card = classify_one(row(economics=econ(400, 0.30, se=0.01, days=60, share=0.03)))
        self.assertEqual(card['state'], sb.B)
        for reason in ('no_executable_prices', 'costs_unknown', 'not_certified'):
            self.assertIn(reason, card['reasons'])

    def test_validated_path_requires_every_gate_at_once(self):
        certified = row(economics=econ(400, 0.30, se=0.01, days=60, share=0.03),
                        confirmation={'certified_utc': '2026-11-22T00:00:00+00:00'})
        with patch.object(sb, 'EXECUTABLE_PRICE_EVIDENCE', True):
            self.assertEqual(classify_one(certified, validation={'state': 'active'})['reasons'], ['costs_unknown'])
            with patch.object(sb, 'COSTS_KNOWN', True):
                self.assertEqual(classify_one(certified, validation={'state': 'not_activated'})['reasons'], ['not_certified'])
                self.assertEqual(classify_one(certified, validation={'state': 'active'})['state'], sb.D)


class Adversarial(unittest.TestCase):
    def test_1_historical_positive_but_prospective_negative_is_rejected(self):
        r = row(economics=econ(60, -0.15, se=0.04, days=15), roi=0.9, prob_above=0.999, n=500, hit_rate=0.8)
        card = classify_one(r)
        self.assertEqual(card['state'], sb.C)
        self.assertIn('negative_return', card['reasons'])

    def test_2_strategy_registered_after_the_results_has_no_evidence(self):
        signals = series([1.0] * 40, every=180)
        late = (T0 + timedelta(days=30)).isoformat()
        rows, _, _ = rows_by_id(signals, {'h': hyp(born=late, source='llm')})
        card = classify_one(dict(rows['h'], id='h', game_set=None))
        self.assertEqual(card['state'], sb.A)
        self.assertEqual(card['confirmed'], 0)

    def test_3_many_bets_in_one_match_count_one_unit(self):
        signals = [sig('m1', 1.0, 0), sig('m1', 1.0, 1, selection='Over 3.5'), sig('m1', -1.0, 2, selection='Under 2.5')]
        rows, _, _ = rows_by_id(signals, {'h': hyp(conditions=())})
        economics = rows['h']['prospective']['economics']
        self.assertEqual(economics['admitted'], 1)
        self.assertEqual(economics['results_confirmed'], 1)

    def test_4_hundreds_of_null_hypotheses_never_produce_a_corrected_winner(self):
        rng = random.Random(56)
        rows = []
        for i in range(600):
            n = 40
            profits = [rng.choice((1.0 * 0.98, -1.0)) for _ in range(n)]   # odds 2.0, no edge, -1 % per game
            mean = sum(profits) / n
            sd = (sum((p - mean) ** 2 for p in profits) / (n - 1)) ** 0.5
            rows.append(row('Jogo aberto | Liga = L%d' % i, conditions=(('Liga', 'L%d' % i),),
                            economics=econ(n, round(mean, 4), se=round(sd / n ** 0.5, 4), days=14, share=0.1)))
        result = board(rows)
        uncorrected = sum(1 for r in rows
                          if r['prospective']['economics']['roi_odds_minus_2pct']
                          - 1.6449 * r['prospective']['economics']['se_day_clustered_odds_minus_2pct'] > 0)
        self.assertGreater(uncorrected, 0)        # chance alone produces "significant" winners without correction
        self.assertEqual(result['multiplicity']['candidates_tested'], 600)
        self.assertTrue(all(c['lower_corrected'] <= 0 for c in result['insufficient']))
        self.assertEqual(result['counts']['validada'], 0)

    def test_5_announced_odds_are_never_treated_as_executable(self):
        self.assertFalse(sb.EXECUTABLE_PRICE_EVIDENCE)
        self.assertIn('no_executable_prices', classify_one(row(economics=econ(50, 0.2, days=12)))['reasons'])

    def test_6_and_7_quarantined_or_corrected_results_block_coverage(self):
        r = row(economics=econ(30, 0.25, admitted=40, quarantined=6, imputed_losses=4))
        card = classify_one(r)
        self.assertEqual(card['state'], sb.C)
        self.assertIn('result_coverage', card['reasons'])

    def test_8_result_seen_before_decision_is_excluded_from_evidence(self):
        bet = sig('m1', 1.0, 0, seen=200, known=145)      # result observed before the system read the signal
        rows, _, _ = rows_by_id([bet], {'h': hyp(conditions=())})
        self.assertEqual(rows['h']['prospective']['economics']['admitted'], 0)

    def test_9_post_signal_and_unprovable_conditions_are_rejected(self):
        post = classify_one(row('Betfair | Reforço = sem reforço', conditions=(('Reforço', 'sem reforço'),),
                                economics=econ(50, 0.3)))
        later = classify_one(row('Polymarket | Sinal anterior de outro tipo no mesmo jogo = sim',
                                 conditions=(('Sinal anterior de outro tipo no mesmo jogo', 'sim'),), economics=econ(50, 0.5)))
        self.assertIn('post_signal_condition', post['reasons'])
        self.assertIn('not_known_at_decision', later['reasons'])
        self.assertEqual({post['state'], later['state']}, {sb.C})

    def test_10_llm_with_insufficient_data_gets_explained_rejection(self):
        r = row('Jogo aberto | Minuto = 61 em diante', conditions=(('Minuto', '61 em diante'),), source='llm',
                economics=econ(5, 0.9, days=2))
        result = board([r])
        self.assertEqual(result['llm_verdicts'][0]['state'], sb.A)
        lines = sb.llm_feedback(result)
        text = '\n'.join(lines)
        self.assertIn('Em observação', text)
        self.assertIn('menos de 10 resultados', text)
        self.assertIn('RENTABILIDADE NÃO DEMONSTRADA', text)
        prompt = llm.build_prompt({'Jogo aberto': {'Minuto': {'61 em diante': 9}}}, [], 3, strategies=result)
        self.assertIn('Decisão do motor científico', prompt)
        self.assertIn('Jogo aberto | Minuto = 61 em diante -> Em observação', prompt)

    def test_10b_llm_intake_still_rejects_thin_support(self):
        reasons = {}
        raw = json.dumps({'hypotheses': [{'family': 'Jogo aberto', 'conditions': [['Minuto', '61 em diante']]}]})
        good, bad = llm.accept(raw, {'Jogo aberto': {'Minuto': {'61 em diante': 9}}}, {}, support=lambda f, c: 2,
                               reasons=reasons)
        self.assertEqual((good, bad, reasons), ([], 1, {'support': 1}))

    def test_11_overlapping_benchmark_never_changes_the_state(self):
        r = row(economics=econ(40, 0.05, days=12, phases={'live': 40}))
        good = {'roi_odds_minus_2pct': -0.5, 'overlapping': True}
        bad = {'roi_odds_minus_2pct': 0.9, 'overlapping': True}
        a = board([r], baseline=lambda *a: good)['insufficient'][0]
        b = board([r], baseline=lambda *a: bad)['insufficient'][0]
        self.assertEqual((a['state'], a['reasons']), (b['state'], b['reasons']))
        self.assertTrue(a['baseline']['overlapping'])

    def test_12_unknown_costs_block_validation(self):
        self.assertFalse(sb.COSTS_KNOWN)
        self.assertIn('costs_unknown', classify_one(row(economics=econ(50, 0.2)))['reasons'])

    def test_13_incomplete_data_fail_closed(self):
        broken = row(prospective=None)
        empty = row('Jogo aberto | Minuto = até ao 30', conditions=(('Minuto', 'até ao 30'),), prospective={})
        result = board([broken, empty])
        self.assertEqual(result['counts']['validada'], 0)
        self.assertIn('no_prospective_evidence_possible', result['reasons'])
        unverified = classify_one(row(born_verified=False, economics=econ(50, 0.2)))
        self.assertIn('unverified_registration', unverified['reasons'])

    def test_14_eight_wins_in_nine_games_is_only_observation(self):
        card = classify_one(row(economics=econ(9, 0.69, se=0.1, days=4, share=0.4, wins=8)))
        self.assertEqual(card['state'], sb.A)
        self.assertIsNone(card['lower_corrected'])

    def test_14b_few_days_give_no_uncertainty_bound(self):
        card = classify_one(row(economics=econ(60, 0.4, se=0.005, days=3, share=0.5)))
        self.assertEqual(card['state'], sb.B)
        self.assertIsNone(card['lower_corrected'])
        self.assertIn('concentrated_in_few_days', card['reasons'])

    def test_15_reprocessing_is_deterministic_and_order_free(self):
        rows = [row('Jogo aberto | Liga = L%d' % i, conditions=(('Liga', 'L%d' % i),),
                    economics=econ(10 + i, 0.01 * (i - 5), days=11)) for i in range(12)]
        first = board(copy.deepcopy(rows))
        shuffled = copy.deepcopy(rows)
        random.Random(1).shuffle(shuffled)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(board(shuffled), sort_keys=True))

    def test_pre_and_live_are_never_pooled(self):
        card = classify_one(row(economics=econ(50, 0.3, phases={'pre': 20, 'live': 30})))
        self.assertEqual(card['state'], sb.C)
        self.assertIn('mixed_pre_live', card['reasons'])

    def test_redundant_game_sets_are_listed_once(self):
        a = row('Jogo aberto | Minuto = 31 a 60', economics=econ(40, 0.1), game_set='same')
        b = row('Jogo aberto | Liga = X', conditions=(('Liga', 'X'),), economics=econ(40, 0.1), game_set='same')
        result = board([a, b])
        self.assertEqual(result['reasons'].get('redundant_game_set'), 1)
        self.assertEqual(len(result['insufficient']), 1)

    def test_avoid_hypotheses_are_not_bet_candidates(self):
        result = board([row(expect='abaixo', economics=econ(50, -0.4))])
        self.assertEqual(sum(result['counts'].values()), 0)
        self.assertEqual(result['reasons'], {'avoid_hypothesis': 1})

    def test_listing_is_short(self):
        rows = [row('Jogo aberto | Liga = L%d' % i, conditions=(('Liga', 'L%d' % i),), economics=econ(40, 0.1), game_set=i)
                for i in range(50)]
        result = board(rows)
        self.assertLessEqual(len(result['insufficient']), sb.CRITERIA['listing_per_family'])


class RuleAndShortlist(unittest.TestCase):
    RULE = {'status': 'exploratory_only', 'baseline': {'description': 'b', 'ft_confirmed': 22, 'roi_odds_minus_2pct': -0.03},
            'ranking': [{'rule_id': sb.RULE_PR55_ID, 'forward_games': 6, 'ft_confirmed': 5, 'settled': 5, 'wins': 3,
                         'losses': 2, 'pending': 1, 'quarantined': 0, 'timeouts_counted_as_losses': 0,
                         'roi_advertised': 0.23, 'roi_odds_minus_2pct': 0.2054, 'se_day_clustered_odds_minus_2pct': 0.4,
                         'liquidated_by_decision_day_utc': {'groups': 3, 'largest_share': 0.6},
                         'confirmed_ft_by_decision_day_utc': {'groups': 3, 'largest_share': 0.6}}]}

    def test_pr55_rule_with_five_games_is_observation_only(self):
        result = board([], rule=self.RULE, shortlist=sb.load_shortlist())
        self.assertEqual(result['rule_pr55']['state'], sb.A)
        entries = {e['id']: e for e in result['shortlist']['entries']}
        self.assertEqual(entries[sb.RULE_PR55_ID]['state'], sb.A)
        self.assertNotIn('_ev', result['rule_pr55'])
        self.assertEqual(result['shortlist']['size'], 2)

    def test_shortlist_uses_only_its_own_small_correction(self):
        self.assertLess(sb.bonferroni_z(2), sb.bonferroni_z(2000))

    def test_shortlist_entry_without_future_games_is_observation(self):
        result = board([], shortlist=sb.load_shortlist(), shortlist_rows=[])
        self.assertTrue(all(e['state'] == sb.A for e in result['shortlist']['entries']))

    def test_selection_rule_picks_one_per_family_and_phase(self):
        cards = [dict(id='a', family='Jogo aberto', phases={'live': 40}, state=sb.B, reasons=[], confirmed=40,
                      decision_days=12, roi_odds_minus_2pct=0.1, se_day_clustered=0.03),
                 dict(id='b', family='Jogo aberto', phases={'live': 40}, state=sb.B, reasons=[], confirmed=40,
                      decision_days=12, roi_odds_minus_2pct=0.2, se_day_clustered=0.03),
                 dict(id='c', family='Stake', phases={'pre': 40}, state=sb.B, reasons=[], confirmed=40,
                      decision_days=4, roi_odds_minus_2pct=0.5, se_day_clustered=0.03),
                 dict(id='d', family='Betfair', phases={'pre': 40}, state=sb.B, reasons=[], confirmed=40,
                      decision_days=12, roi_odds_minus_2pct=-0.1, se_day_clustered=0.03)]
        self.assertEqual([c['id'] for c in sb.select_shortlist(cards)], ['b'])


class IndependentReviewRegressions(unittest.TestCase):
    """Defects found by the independent adversarial review (23/11/2026); each must stay fixed."""

    def open_gates(self):
        return [patch.object(sb, 'EXECUTABLE_PRICE_EVIDENCE', True), patch.object(sb, 'COSTS_KNOWN', True)]

    def certified(self, economics):
        return row(economics=economics, confirmation={'certified_utc': '2026-11-22T00:00:00+00:00'})

    def test_rejected_candidates_still_count_in_the_multiplicity_family(self):
        good = row(economics=econ(400, 0.05, se=0.02, days=40, share=0.05), game_set='g0')
        failed = [row('Jogo aberto | Minuto = %d' % i, conditions=(('Minuto', str(i)),), status='abandonada',
                      economics=econ(400, -0.2, se=0.02, days=40, share=0.05), game_set='g%d' % (i + 1)) for i in range(30)]
        result = board([good] + failed)
        self.assertEqual(result['multiplicity']['candidates_tested'], 31)
        self.assertLessEqual(result['insufficient'][0]['lower_corrected'], 0)

    def test_malformed_or_inconsistent_evidence_never_validates(self):
        cases = [econ(float('nan'), 0.3, se=0.01, days=float('nan')),
                 econ(1000, 0.3, se=0.01, days=100, share=None),
                 econ(1000, 0.3, se=0.01, days=100, share=0.01, phases={}),
                 econ(1000, 0.3, se=0.01, days=100, share=0.01, phases={'live': 999, 'x': 1}),
                 econ(1000, 0.3, se=0.01, days=100, share=0.01, admitted=0, quarantined=900),
                 econ(1000, '0.3', se=0.01, days=100, share=0.01),
                 econ(1000, 0.3, se=float('inf'), days=100, share=0.01)]
        with self.open_gates()[0], self.open_gates()[1]:
            for economics in cases:
                card = classify_one(self.certified(economics), validation={'state': 'active'})
                self.assertNotEqual(card['state'], sb.D, economics)
            sane = econ(1000, 0.3, se=0.01, days=100, share=0.01)
            self.assertEqual(classify_one(self.certified(sane), validation={'state': 'active'})['state'], sb.D)

    def test_unknown_phase_is_never_shortlisted(self):
        cards = [dict(id='u', family='Stake', phases={'unknown': 100}, state=sb.B, reasons=['unknown_phase'],
                      confirmed=100, decision_days=20, roi_odds_minus_2pct=0.2, se_day_clustered=0.02)]
        self.assertEqual(sb.select_shortlist(cards), [])

    def test_malformed_pr55_input_fails_closed_without_crashing(self):
        for change in ({'liquidated_by_decision_day_utc': [1, 2]}, {'losses': '3'}):
            rule = copy.deepcopy(RuleAndShortlist.RULE)
            rule['ranking'][0].update(change)
            self.assertIsNone(board([], rule=rule)['rule_pr55'])
        rule = copy.deepcopy(RuleAndShortlist.RULE)
        rule['baseline'] = [1]
        self.assertIsNotNone(board([], rule=rule)['rule_pr55'])

    def test_llm_feedback_fails_closed(self):
        self.assertEqual(sb.llm_feedback({'schema': sb.SCHEMA}), [])
        broken = board([])
        broken['llm_verdicts'] = [{'id': 'x', 'state': sb.A, 'confirmed': float('nan')}]
        self.assertTrue(all('x ->' not in line for line in sb.llm_feedback(broken)))


SHORT_ID = ('Jogo aberto | Confiança indicada pelo bot = 95 % ou mais & Faixa de odd = odd 1,50 a 2,00 '
            '& Parte do jogo = 2.ª parte')
SHORT_HID = brain.hyp_id('Jogo aberto', [('Confiança indicada pelo bot', '95 % ou mais'),
                                          ('Faixa de odd', 'odd 1,50 a 2,00'), ('Parte do jogo', '2.ª parte')])


def short_signal(match, minute_of_23rd, w=1.0):
    """A signal matching the frozen shortlist entry, sent on 2026-11-23 at the given minute (UTC), read at once."""
    return sig(match, w, 3 * 1440 + minute_of_23rd, odds=1.8, bot_over_pct=96)


class ShortlistTemporalIntegrity(unittest.TestCase):
    """Review 2 (23/11): the shortlist only counts from a GitHub-attested instant, never from its self-declared 14:00."""

    def board_at(self, signals, as_of):
        return brain.build(signals, {}, 300, as_of=as_of)['strategy_board']

    def test_ids_match_the_registered_file(self):
        self.assertEqual(SHORT_HID, SHORT_ID)
        self.assertIn(SHORT_ID, [e['id'] for e in sb.load_shortlist()['entries']])

    def test_evidence_starts_at_the_attested_marker_not_at_the_declared_time(self):
        self.assertEqual(sb.SHORTLIST_PROOF['created_at_utc'], '2026-11-23T14:40:13Z')
        self.assertEqual(sb.shortlist_evidence_start(sb.load_shortlist()).isoformat(), '2026-11-23T14:40:13+00:00')

    def test_bets_in_the_unverifiable_interval_never_count(self):
        gap = [short_signal('gap%d' % i, 14 * 60 + 5 + 3 * i) for i in range(10)]      # 14:05 … 14:32, before the marker
        after = [short_signal('aft%d' % i, 15 * 60 + 180 * i, w=-1.0) for i in range(4)]
        result = self.board_at(gap + after, T0 + timedelta(days=10))
        entry = {e['id']: e for e in result['shortlist']['entries']}[SHORT_ID]
        self.assertEqual(entry['admitted'], 4)
        self.assertEqual(entry['wins'], 0)

    def test_as_of_before_the_marker_reveals_no_selected_strategy(self):
        signals = [short_signal('aft0', 15 * 60)]
        for as_of in (T0 + timedelta(days=3, hours=14, minutes=10), T0 + timedelta(days=3, hours=14, minutes=40)):
            result = self.board_at(signals, as_of)
            self.assertEqual(result['shortlist']['entries'], [])
            self.assertEqual(result['shortlist']['status'], 'not_registered_at_as_of')
            self.assertNotIn(SHORT_ID, json.dumps(result, ensure_ascii=False))
        later = self.board_at(signals, T0 + timedelta(days=3, hours=14, minutes=41))
        self.assertEqual(len(later['shortlist']['entries']), 2)

    def test_pr55_rule_keeps_its_original_window(self):
        rule = copy.deepcopy(RuleAndShortlist.RULE)
        before = sb.build([], NO_VALIDATION, as_of_dimensions=AS_OF, post_dimensions=POST, rule=rule,
                          as_of=T0 + timedelta(days=3, hours=14))
        self.assertEqual(before['rule_pr55']['confirmed'], 5)       # 19/11 window still visible on its own


class PhaseConsistency(unittest.TestCase):
    """Review 3 (23/11): pre/live/unknown counts must add up to the admitted games, or the evidence fails closed."""

    def certified(self, **kw):
        return row(economics=econ(1000, 0.3, se=0.01, days=100, share=0.01, **kw),
                   confirmation={'certified_utc': '2026-11-22T00:00:00+00:00'})

    def test_phase_counts_that_do_not_add_up_never_validate(self):
        with patch.object(sb, 'EXECUTABLE_PRICE_EVIDENCE', True), patch.object(sb, 'COSTS_KNOWN', True):
            for phases in ({'live': 5}, {'live': 1001}, {'pre': 0, 'live': 1000, 'unknown': 3}, {'other': 1000}):
                card = classify_one(self.certified(phases=phases), validation={'state': 'active'})
                self.assertEqual(card['state'], sb.C, phases)
                self.assertIn('malformed_evidence', card['reasons'], phases)
            ok = classify_one(self.certified(phases={'live': 1000}), validation={'state': 'active'})
            self.assertEqual(ok['state'], sb.D)

    def test_pr55_phase_count_covers_every_admitted_game(self):
        card = board([], rule=RuleAndShortlist.RULE)['rule_pr55']
        self.assertEqual(card['reasons'], ['below_observe_minimum'])

    def test_brain_phase_counts_match_admitted_on_real_economics(self):
        signals = series([1.0, -1.0, None] * 8, every=180) + [sig('pre%d' % i, 1.0, 40 * 60 + i, phase='pre') for i in range(3)]
        e = brain.economic_summary(signals, brain.chronology.parse_utc('2026-01-01T00:00:00+00:00'), T0 + timedelta(days=1))
        self.assertEqual(sum(e['phases'].values()), e['admitted'])


class BrainIntegration(unittest.TestCase):
    def test_brain_build_publishes_the_board_without_validation(self):
        signals = series([1.0, -1.0, 1.0] * 15, every=180)
        out = brain.build(signals, {brain.hyp_id('Jogo aberto', [('Minuto', '31 a 60')]): hyp()}, 300, as_of=T0 + timedelta(days=20))
        b = out['strategy_board']
        self.assertEqual(b['schema'], sb.SCHEMA)
        self.assertEqual(b['counts']['validada'], 0)
        self.assertFalse(b['automatic_alerts_changed'])
        self.assertIn('RENTABILIDADE NÃO DEMONSTRADA', b['verdict'])

    def test_economics_reports_phase_and_day_concentration(self):
        signals = series([1.0, -1.0] * 10, every=60 * 30)
        e = brain.economic_summary(signals, brain.chronology.parse_utc('2026-01-01T00:00:00+00:00'), T0 + timedelta(days=40))
        self.assertEqual(e['phases'], {'live': 20})
        self.assertGreaterEqual(e['decision_days'], 10)
        self.assertIsNotNone(e['se_day_clustered_odds_minus_2pct'])


if __name__ == '__main__':
    unittest.main()

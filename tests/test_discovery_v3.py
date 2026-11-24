"""Protocol v3: immutability, risk-set comparators, layered claims and inference.

Adversarial cases: ties, dependence across days/weeks/competitions/sources,
missing FT, confounding by price, future information, exact contract identity,
defective-protocol quarantine and confirmatory registration.
"""

from contextlib import redirect_stdout
from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from scangoal import chronology, event_audit, event_intelligence as events, scientific_receipts
from scangoal.discovery import __main__ as cli, dsl, dsl_v2, engine as engine_v1, engine_v2, quarantine
from scangoal.discovery.replay import Budget, Replay
from scangoal.discovery.v3 import engine, inference, layers, protocol, registry, riskset

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('v3_world', ROOT / 'audits' / 'temporal-discovery-v3' / 'world.py')
WORLD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORLD)
CACHE = {}


def world(name, **changes):
    key = (name, tuple(sorted(changes.items())))
    if key not in CACHE:
        scenario = WORLD.scenarios()[name]
        CACHE[key] = WORLD.build(WORLD.replace(scenario, **changes))
    return CACHE[key]


def cut(replay, clock):
    audit = replay.audit
    seen = [e for e in audit['events'] if events.seen(e) <= clock]
    ids = {e['message_id'] for e in seen}
    results = {k: rows for k, v in audit['fixture_results'].items()
               if (rows := [r for r in v if chronology.parse_utc(r['available_utc']) <= clock])}
    return Replay(dict(audit, as_of_utc=events.iso(clock), events=seen, fixture_results=results,
                       episodes=[x for x in audit['episodes'] if set(x['message_ids']) <= ids]))


def pair(lower, observed=..., upper=None, **clusters):
    observed = lower if observed is ... else observed
    return dict(lower=lower, observed=observed, upper=lower if upper is None else upper,
                clusters=dict(dict(kickoff_week=0, kickoff_day=0, source='S', competition='C'), **clusters))


class Immutability(unittest.TestCase):
    def test_v1_and_v2_protocols_and_interpreters_are_unchanged(self):
        self.assertEqual(engine_v2.PROTOCOL_SHA256, protocol.V2_PROTOCOL_SHA256)
        self.assertEqual(engine_v2.INTERPRETER_SHA256,
                         'a22d681b4d66767ee5b3cf06bbe05e446beeddfd5ddc39406ac870d0f178b6c9')
        self.assertEqual(dsl.digest(engine_v1.PROTOCOL), protocol.V1_PROTOCOL_SHA256)
        self.assertEqual(engine_v1.INTERPRETER_SHA256,
                         'fce54c860b6e2ea7a357e4e3b7be62a62fec795171671dfea32290faf132d75e')

    def test_v3_protocol_digest_is_pinned(self):
        # Changing any frozen parameter requires a new protocol name and identifier.
        self.assertEqual(protocol.PROTOCOL['name'], 'temporal-risk-set-v3.1')
        self.assertEqual(protocol.PROTOCOL_SHA256, PINNED_V3)
        # v3.0 stays byte-for-byte as published and differs only in the prediction layer.
        self.assertEqual(protocol.V3_0_SHA256, PINNED_V3_0)
        self.assertEqual(protocol.PROTOCOL_V3_0['name'], 'temporal-risk-set-v3')
        changed = {k for k in protocol.PROTOCOL if protocol.PROTOCOL.get(k) != protocol.PROTOCOL_V3_0.get(k)}
        self.assertEqual(changed, {'name', 'revision', 'supersedes', 'layers'})
        layers_changed = {k for k in protocol.PROTOCOL['layers']
                          if protocol.PROTOCOL['layers'][k] != protocol.PROTOCOL_V3_0['layers'].get(k)}
        self.assertEqual(layers_changed, {'prediction', 'asian_half_settlements'})

    def test_v3_identifiers_are_distinct_from_v2(self):
        rule = dsl_v2.canonical(WORLD.RULE)
        self.assertTrue(engine.hypothesis_id(rule).startswith('tr3:'))
        self.assertNotEqual(engine.hypothesis_id(rule), dsl_v2.identifier(rule))
        self.assertEqual(engine.hypothesis_id(rule), registry._identifier(rule))

    def test_claims_never_exceed_experimental_or_execution(self):
        self.assertEqual(protocol.PROTOCOL['claims'],
                         dict(max_status='experimental', executable_profitability='not_demonstrated'))


class Inference(unittest.TestCase):
    def test_t_distribution(self):
        self.assertAlmostEqual(inference.t_sf(2.0, 10), .036694, places=5)
        self.assertAlmostEqual(inference.t_quantile(.975, 10), 2.228139, places=4)

    def test_sign_flip_is_exact_for_few_clusters(self):
        self.assertEqual(inference.sign_flip_p([1, 2, 3]), 1 / 8)
        self.assertEqual(inference.sign_flip_p([1, -1]), .75)
        self.assertEqual(inference.sign_flip_p([1.] * 16), 2 ** -16)
        self.assertIsNone(inference.sign_flip_p([]))

    def test_ties_stay_in_the_estimand(self):
        # v2 counterexample: 60 tied and 20 positive units gave median 0 with p = 2^-20.
        # v3 targets the mean and keeps every tie in numerator and denominator.
        rows = [pair(0., kickoff_week=i % 16, kickoff_day=i) for i in range(60)]
        rows += [pair(1., kickoff_week=i % 16, kickoff_day=60 + i) for i in range(20)]
        result = inference.assess(rows, inference.Settings(min_pairs=60))
        self.assertEqual(result['tied_pairs'], 60)
        self.assertAlmostEqual(result['mean_observed'], .25)
        self.assertLess(result['p'], .01)
        # Symmetric ties: no evidence, whatever the share of zeros.
        rows = [pair(0., kickoff_week=i % 16) for i in range(60)]
        rows += [pair(1. if i % 2 else -1., kickoff_week=i % 16) for i in range(20)]
        self.assertGreater(inference.assess(rows, inference.Settings(min_pairs=60))['p'], .3)

    def test_claim_must_survive_every_dependence_structure(self):
        rows = []
        for week in range(13):
            sign = 1. if week < 7 else -1.
            for day in range(7):
                rows += [pair(sign + .3, kickoff_week=week, kickoff_day=week * 7 + day, competition=f'L{day}')
                         for _ in range(3)]
        result = inference.assess(rows)
        tests = result['tests']
        self.assertLess(tests['kickoff_day']['sign_flip_p'], .01)
        self.assertGreater(tests["kickoff_week"]["sign_flip_p"], .05)
        self.assertEqual(result['p'], max(tests['kickoff_week']['sign_flip_p'], tests['competition']['sign_flip_p']))

    def test_competition_dependence_also_binds(self):
        rows = [pair(1. if league < 5 else -.9, kickoff_week=i % 13, competition=f'L{league}')
                for league in range(10) for i in range(13)]
        result = inference.assess(rows)
        self.assertTrue(result['tests']['competition']['estimable'])
        self.assertLess(result['tests']['kickoff_week']['sign_flip_p'], .01)
        self.assertGreater(result['p'], .2)

    def test_missing_ft_uses_worst_case_and_gates_coverage(self):
        rows = [pair(-.9, observed=None, upper=1.1, kickoff_week=i % 13) for i in range(20)]
        rows += [pair(.2, kickoff_week=i % 13) for i in range(80)]
        result = inference.assess(rows)
        self.assertIn('ft_coverage_below_minimum', result['insufficiency'])
        self.assertIsNone(result['p'])
        self.assertLess(result['mean_bounds'][0], 0)
        self.assertGreater(result['mean_observed'], 0)

    def test_small_support_is_insufficient(self):
        result = inference.assess([pair(1., kickoff_week=i) for i in range(20)])
        self.assertIn('matched_pairs_below_minimum', result['insufficiency'])
        self.assertIsNone(result['p'])

    def test_single_source_cannot_drive_the_claim(self):
        rows = [pair(1., kickoff_week=i % 13, source='A') for i in range(90)]
        rows += [pair(-.5, kickoff_week=i % 13, source='B') for i in range(30)]
        result = inference.assess(rows)
        self.assertFalse(result['robust_leave_one_out'])
        self.assertLessEqual(result['robustness']['source']['minimum_leave_one_out'], 0)

    def test_holm_counts_hypotheses_without_evidence(self):
        self.assertEqual(inference.holm([.01, None, .02]), [.03, None, .04])
        self.assertEqual(inference.benjamini_hochberg([.01, None, .02])[0], .03)

    def test_linear_fit(self):
        rng = Random(3)
        xs = [(rng.random(), rng.random()) for _ in range(200)]
        fit = inference.linear_fit(xs, [.5 * a - 2 * b + 1 for a, b in xs])
        self.assertAlmostEqual(fit[0], .5, places=9)
        self.assertAlmostEqual(fit[1], -2, places=9)
        self.assertEqual(inference.linear_fit([(1., 0.)] * 10, [1.] * 10), [0., 0.])


class Layers(unittest.TestCase):
    def test_push_is_not_prediction_evidence_but_is_return(self):
        push = layers.values(dict(status='settled', payoff=0.), 2.)
        won = layers.values(dict(status='settled', payoff=1.), 2.)
        self.assertIsNone(layers.pair_difference(push, won, 'prediction'))
        self.assertAlmostEqual(layers.pair_difference(won, push, 'announced')['observed'], 1 - .02)

    def test_missing_ft_bounds(self):
        missing = layers.values(dict(status='missing_ft', payoff=None), 2.5)
        lost = layers.values(dict(status='settled', payoff=-1.), 2.5)
        d = layers.pair_difference(missing, lost, 'prediction')
        self.assertIsNone(d['observed'])
        self.assertAlmostEqual(d['lower'], 0.)
        self.assertAlmostEqual(d['upper'], 1.)

    def test_execution_is_never_demonstrated(self):
        rows = [layers.values(dict(status='settled', payoff=1.), 2.2)] * 3
        rows.append(layers.values(dict(status='settled', payoff=-1.), 2.2))
        result = layers.executable(rows)
        self.assertEqual(result['status'], 'not_demonstrated')
        self.assertTrue(0 < result['break_even_price_degradation'] < 1)
        self.assertEqual(layers.executable([])['break_even_price_degradation'], None)


class AsianHalfSettlement(unittest.TestCase):
    """Mission: +0.5 / -0.5 must not be scored as full wins or losses in prediction."""

    @staticmethod
    def event(line, direction='over'):
        return dict(period='regulation', market_key='match_goals', selection=f'{direction.capitalize()} {line}',
                    direction=direction, line=line, source='Stake', phase='pre', teams=['H', 'A'])

    def test_parser_settles_wins_losses_pushes_and_halves(self):
        cases = {('2.5', (2, 1)): 1., ('2.5', (1, 0)): -1., ('2.0', (1, 1)): 0.,
                 ('2.25', (1, 1)): -.5, ('2.75', (2, 1)): .5, ('2.25', (2, 1)): 1., ('2.75', (1, 1)): -1.}
        for (line, score), expected in cases.items():
            self.assertEqual(event_audit.payoff(self.event(line), score), expected, (line, score))
        self.assertEqual(event_audit.payoff(self.event('2.25', 'under'), (1, 1)), .5)

    def test_prediction_counts_only_the_decisive_half_and_return_is_unchanged(self):
        odds = 1.9
        rows = {payoff: layers.values(dict(status='settled', payoff=payoff), odds)
                for payoff in (1., .5, 0., -.5, -1.)}
        self.assertAlmostEqual(rows[1.]['prediction'][0], 1 - 1 / odds)
        self.assertAlmostEqual(rows[-1.]['prediction'][0], -1 / odds)
        self.assertAlmostEqual(rows[.5]['prediction'][0], .5 - .5 / odds)
        self.assertAlmostEqual(rows[-.5]['prediction'][0], -.5 / odds)
        self.assertIsNone(rows[0.]['prediction'])
        self.assertTrue(rows[.5]['half'] and rows[-.5]['half'] and not rows[1.]['half'])
        for payoff, row in rows.items():
            profit = payoff * (odds - 1) if payoff > 0 else payoff
            self.assertAlmostEqual(row['announced'][0], profit - .02 * max(0., profit) - .02)
            if row['decisive']:
                self.assertAlmostEqual(row['prediction'][0], profit / odds)
        self.assertIsNone(layers.pair_difference(rows[.5], rows[0.], 'prediction'))
        self.assertAlmostEqual(layers.pair_difference(rows[.5], rows[-.5], 'prediction')['observed'], .5)

    def test_residual_is_unbiased_at_fair_prices_where_binary_scoring_is_not(self):
        from math import exp, factorial
        goals = [exp(-2.6) * 2.6 ** g / factorial(g) for g in range(15)]
        for line, direction in (('2.25', 'over'), ('2.75', 'over'), ('2.25', 'under'), ('3.75', 'under'), ('2.5', 'over')):
            event = self.event(line, direction)
            law = [(p, event_audit.payoff(event, (g, 0))) for g, p in enumerate(goals)]
            # Fair odds: expected profit zero.
            win = sum(p * max(0., w) for p, w in law)
            lose = sum(p * max(0., -w) for p, w in law)
            odds = 1 + lose / win
            v31 = sum(p * layers.values(dict(status='settled', payoff=w), odds)['prediction'][0]
                      for p, w in law if w != 0)
            binary = sum(p * ((w > 0) - 1 / odds) for p, w in law if w != 0)
            self.assertAlmostEqual(v31, 0., places=12, msg=line)
            if line == '2.5':
                self.assertAlmostEqual(binary, 0., places=12)
            else:
                self.assertGreater(abs(binary), .01, msg=(line, direction, binary))


class RiskSetDesign(unittest.TestCase):
    def test_comparators_share_instant_contract_and_are_reserved_once(self):
        replay, _ = world('null', days=21)
        evidence, cohort = engine.assess(replay, WORLD.RULE)
        self.assertGreater(len(cohort.pairs), 50)
        used = set()
        for case, control, meta in cohort.pairs:
            self.assertEqual(case.at, control.at)
            self.assertEqual(riskset.contract(case.anchor), riskset.contract(control.anchor))
            self.assertLessEqual(events.seen(control.anchor), case.at)
            self.assertLessEqual(case.at - events.emitted(control.anchor), riskset.MAX_QUOTE_AGE)
            self.assertLessEqual(abs(meta['case_clock'] - meta['control_clock']), riskset.PRE_LEAD_CALIPER)
            self.assertFalse({case.fixture, control.fixture} & used)
            used |= {case.fixture, control.fixture}
            self.assertEqual(control.witness, ())
        self.assertEqual(evidence['layers']['executable']['status'], 'not_demonstrated')

    def test_no_comparator_across_contracts(self):
        bank = WORLD.World(WORLD.Scenario('handmade'))
        ko = WORLD.START + timedelta(hours=15)
        bank.emit('case', ko, ko - timedelta(minutes=200), source='Polymarket', odds='2.00', volume=90000.,
                  league='L')
        bank.emit('case', ko, ko - timedelta(minutes=100), source='Stake', odds='2.00', league='L')
        for name, change in (('line', dict(line='3.5')), ('selection', dict(direction='under')),
                             ('source', dict(source='Polymarket'))):
            bank.emit(name, ko, ko - timedelta(minutes=105), odds='2.00', volume=1000.,
                      league='L', **dict(dict(source='Stake'), **change))
        audit = dict(as_of_utc=events.iso(ko + timedelta(days=4)), input_sha256={'messages.jsonl': '0' * 64},
                     events=bank.events, episodes=bank.episodes, fixture_results={})
        evidence, cohort = engine.assess(Replay(audit), WORLD.RULE)
        self.assertEqual(evidence['counts']['cases'], 1)
        self.assertEqual(cohort.pairs, [])
        self.assertEqual(evidence['counts']['case_without_comparator'], 1)
        # Same contract, same instant: now the comparator exists.
        bank.emit('same', ko, ko - timedelta(minutes=105), source='Stake', odds='2.05', league='L')
        audit['events'], audit['episodes'] = bank.events, bank.episodes
        _, cohort = engine.assess(Replay(audit), WORLD.RULE)
        self.assertEqual([(c.fixture, k.fixture) for c, k, _ in cohort.pairs], [('case', 'same')])

    def test_comparator_with_visible_antecedent_is_refused_and_late_exposure_counted(self):
        bank = WORLD.World(WORLD.Scenario('handmade'))
        ko = WORLD.START + timedelta(hours=15)
        bank.emit('case', ko, ko - timedelta(minutes=200), source='Polymarket', odds='2.00', volume=90000., league='L')
        bank.emit('case', ko, ko - timedelta(minutes=100), source='Stake', odds='2.00', league='L')
        # Antecedent received after this fixture's own quote but before the case instant.
        bank.emit('exposed', ko, ko - timedelta(minutes=150), source='Polymarket', odds='2.00', volume=90000.,
                  league='L', delay=49 * 60)
        bank.emit('exposed', ko, ko - timedelta(minutes=110), source='Stake', odds='2.00', league='L')
        bank.emit('late', ko, ko - timedelta(minutes=105), source='Stake', odds='2.00', league='L')
        bank.emit('late', ko, ko - timedelta(minutes=120), source='Polymarket', odds='2.00', volume=90000.,
                  league='L', delay=3 * 3600)  # received only after the case instant
        bank.emit('late', ko, ko - timedelta(minutes=10), source='Stake', odds='2.00', league='L', delay=4 * 3600)
        audit = dict(as_of_utc=events.iso(ko + timedelta(days=4)), input_sha256={'messages.jsonl': '0' * 64},
                     events=bank.events, episodes=bank.episodes, fixture_results={})
        evidence, cohort = engine.assess(Replay(audit), WORLD.RULE)
        pairs = [(c.fixture, k.fixture) for c, k, _ in cohort.pairs]
        self.assertIn(('case', 'late'), pairs)
        self.assertNotIn('exposed', [k for _, k in pairs])
        self.assertEqual(evidence['counts']['controls_later_exposed'], 1)

    def test_later_information_never_changes_earlier_pairs(self):
        full, _ = world('null', days=21)
        clock = WORLD.START + timedelta(days=10)
        early = cut(full, clock)
        _, a = engine.assess(early, WORLD.RULE)
        _, b = engine.assess(full, WORLD.RULE)
        before = [(c.anchor['message_id'], k.anchor['message_id']) for c, k, _ in b.pairs if c.at <= clock]
        self.assertEqual([(c.anchor['message_id'], k.anchor['message_id']) for c, k, _ in a.pairs], before)


class CoverageAudit(unittest.TestCase):
    def test_waterfall_reproduces_the_protocol_pairs_without_relaxing_it(self):
        spec = importlib.util.spec_from_file_location('v3_coverage', ROOT / 'audits' / 'temporal-discovery-v3' / 'coverage_audit.py')
        coverage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(coverage)
        replay, _ = world('null', days=21)
        _, cohort = engine.assess(replay, WORLD.RULE)
        result = coverage.waterfall(replay, [dict(rule=WORLD.RULE)])
        self.assertEqual(result['strict_pairs'], len(cohort.pairs))
        self.assertEqual(result['first_failing_stage']['matched'], len(cohort.pairs))
        self.assertEqual(result['case_decisions_summed_over_hypotheses'], len(cohort.cases))
        self.assertEqual(sum(result['first_failing_stage'].values()), len(cohort.cases))
        self.assertEqual(riskset.MAX_QUOTE_AGE, timedelta(minutes=30))
        self.assertEqual(riskset.LOG_ODDS_CALIPER, .15)


class EndToEnd(unittest.TestCase):
    def test_strong_effect_is_detected_and_null_is_not(self):
        positive, _ = world('positive_20pp')
        null, _ = world('null')
        evidences = [engine.assess(positive, WORLD.RULE)[0], engine.assess(null, WORLD.RULE)[0]]
        verdicts = engine.decide(evidences, 1, closed=True)
        self.assertEqual(verdicts[0]['layers']['prediction']['status'], 'experimental')
        self.assertIn('predictive_increment_beyond_announced_price', verdicts[0]['claims'])
        self.assertEqual(verdicts[0]['executable_profitability'], 'not_demonstrated')
        self.assertNotEqual(verdicts[1]['status'], 'experimental')
        self.assertEqual(engine.decide(evidences, 1, closed=False)[0]['status'], 'insufficient')

    def test_price_confounding_is_not_retained(self):
        replay, w = world('null_price_confounding')
        evidence, cohort = engine.assess(replay, WORLD.RULE)
        self.assertGreater(evidence['layers']['prediction']['bias_correction']['fitted_on_controls'], 100)
        verdict = engine.decide([evidence], 1, closed=True)[0]
        self.assertNotEqual(verdict['layers']['prediction']['status'], 'experimental')


class Registration(unittest.TestCase):
    def hypotheses(self):
        rule = dsl_v2.canonical(WORLD.RULE)
        return [dict(id=engine.hypothesis_id(rule), rule=rule, origin='oracle', mechanism=None, source_id=None)]

    def append(self, folder, published):
        return registry.append(folder, discovery_as_of=published - timedelta(hours=1), snapshot_sha256='0' * 64,
                               protocol_sha256=protocol.PROTOCOL_SHA256,
                               interpreter_sha256=engine.INTERPRETER_SHA256, hypotheses=self.hypotheses(),
                               now=published)

    def test_plans_start_empty_and_refuse_defective_protocols(self):
        with TemporaryDirectory() as folder:
            published = WORLD.START - timedelta(days=1)
            self.assertEqual(self.append(folder, published)['hypotheses'], 1)
            self.assertIsNone(self.append(folder, published))
            plan = registry.read(folder)[0]
            self.assertEqual(plan['confirmatory_results'], 0)
            for change, error in ((dict(protocol_sha256=protocol.V2_PROTOCOL_SHA256), 'defective'),
                                  (dict(confirmatory_results=3), 'zero_confirmatory'),
                                  (dict(hypotheses=[dict(self.hypotheses()[0], id='td2:' + '0' * 64)]),
                                   'fingerprint')):
                with self.assertRaisesRegex(registry.RegistryError, error):
                    registry.validate(dict(plan, **change), 1, None, None)

    def test_superseded_v3_0_plans_stay_history_and_are_never_evaluated(self):
        rule = dsl_v2.canonical(WORLD.RULE)
        old = [dict(id=registry._identifier(rule, protocol.V3_0_SHA256), rule=rule, origin='oracle',
                    mechanism=None, source_id=None)]
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(registry.RegistryError, 'only_the_current'):
                registry.append(folder, discovery_as_of=WORLD.START, snapshot_sha256='0' * 64,
                                protocol_sha256=protocol.V3_0_SHA256, interpreter_sha256='0' * 64,
                                hypotheses=old, now=WORLD.START)
        plan = dict(schema=3, number=1, previous=None, published_utc=events.iso(WORLD.START),
                    discovery_as_of_utc=events.iso(WORLD.START), snapshot_sha256='0' * 64,
                    protocol_sha256=protocol.V3_0_SHA256, interpreter_sha256='0' * 64, hypotheses=old,
                    confirmatory_results=0)
        registry.validate(plan, 1, None, None)
        self.assertNotEqual(old[0]['id'], engine.hypothesis_id(rule))
        replay, _ = world('null', days=21)
        row = engine.evaluate(replay, [plan])['investigations'][0]
        self.assertEqual((row['status'], row['reason']), ('insufficient', 'superseded_protocol_v3_0_half_settlement_defect'))
        self.assertNotIn('evidence', row)

    def test_confirmatory_window_requires_witness_and_closure(self):
        replay, _ = world('positive_20pp')
        published = WORLD.START - timedelta(days=1)

        def witness(_, domain, content, *, as_of):
            return scientific_receipts.Receipt('synthetic', registry.digest(content), published + timedelta(hours=1))

        with TemporaryDirectory() as folder:
            self.append(folder, published)
            plans = registry.read(folder)
        unwitnessed = engine.evaluate(replay, plans)
        self.assertEqual(unwitnessed['investigations'][0]['reason'], 'awaiting_independent_witness')
        early = engine.evaluate(cut(replay, WORLD.START - timedelta(hours=1)), plans, verify=witness)
        self.assertEqual(early['investigations'][0]['reason'], 'forward_window_not_started')
        report = engine.evaluate(replay, plans, verify=witness)
        row = report['investigations'][0]
        self.assertEqual(row['start_utc'], events.iso(WORLD.START))
        self.assertTrue(row['window_closed'])
        self.assertEqual(row['status'], 'experimental')
        self.assertEqual(row['executable_profitability'], 'not_demonstrated')
        self.assertTrue(report['confirmatory'])


class Quarantine(unittest.TestCase):
    def test_v1_and_v2_claims_blocked_history_kept(self):
        for digest in (protocol.V1_PROTOCOL_SHA256, protocol.V2_PROTOCOL_SHA256):
            report = dict(protocol_sha256=digest, investigations=[dict(id='x', status='experimental')])
            marked = quarantine.annotate(report)
            self.assertEqual(marked['confirmatory_claims'], 'blocked')
            self.assertEqual(marked['investigations'][0]['status'], 'experimental')
            self.assertEqual(marked['investigations'][0]['confirmatory_status'], 'blocked_defective_protocol')
            self.assertEqual(report['investigations'][0], dict(id='x', status='experimental'))
            with self.assertRaises(quarantine.ConfirmatoryClaimBlocked):
                quarantine.require_confirmatory(digest)
        quarantine.require_confirmatory(protocol.PROTOCOL_SHA256)
        notice = quarantine.notice(protocol.V3_0_SHA256)
        self.assertEqual((notice['protocol'], notice['successor']), ('temporal-risk-set-v3', 'temporal-risk-set-v3.1'))
        self.assertIn('prediction_layer_counts_asian_half_settlements_as_full_outcomes', notice['defects'])
        with self.assertRaises(quarantine.ConfirmatoryClaimBlocked):
            quarantine.require_confirmatory(protocol.V3_0_SHA256)
        self.assertIs(quarantine.annotate(dict(protocol_sha256=protocol.PROTOCOL_SHA256)).get('quarantine'), None)

    def test_cli_marks_v2_evaluation_as_blocked(self):
        fake = dict(schema='temporal_discovery_report_v2', protocol_sha256=engine_v2.PROTOCOL_SHA256,
                    as_of_utc='2026-11-24T00:00:00Z', investigations=[dict(id='td2:x', status='rejected')])
        with TemporaryDirectory() as folder, mock.patch.object(cli.event_audit, 'build', return_value=None), \
                mock.patch.object(cli, 'Replay'), mock.patch.object(cli.engine_v2, 'evaluate', return_value=fake):
            output = io.StringIO()
            with redirect_stdout(output):
                cli.main(['evaluate', '--version', '2', '--as-of', '2026-11-24T00:00:00Z', '--registry', folder])
        report = json.loads(output.getvalue())
        self.assertEqual(report['confirmatory_claims'], 'blocked')
        self.assertEqual(report['quarantine']['audit_commit'], protocol.AUDIT_COMMIT)

    def test_cli_v3_registry_is_separate_and_offline(self):
        with TemporaryDirectory() as folder:
            output = io.StringIO()
            with redirect_stdout(output):
                cli.main(['registry', '--version', '3', '--as-of', '2026-11-24T00:00:00Z', '--registry', folder])
            report = json.loads(output.getvalue())
        self.assertEqual(report['protocol_sha256'], protocol.PROTOCOL_SHA256)
        self.assertEqual(report['plans'], [])

    def test_v3_code_is_offline(self):
        for path in (ROOT / 'scangoal' / 'discovery' / 'v3').glob('*.py'):
            text = path.read_text().lower()
            for word in ('socket', 'urllib', 'requests', 'http.client', 'telethon', 'subprocess', '1xbet'):
                self.assertNotIn(word, text, (path.name, word))


PINNED_V3_0 = '1726e67ee8ff345175468471b76cccae4124a087c8f45ca0dcbf5b5b877f8939'
PINNED_V3 = 'b26e9e14479bd3ef80f4ce8c072768108c62ba9ddb136d3503a13a5bd2bc3e71'

if __name__ == '__main__':
    unittest.main()

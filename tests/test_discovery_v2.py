"""Adversarial compatibility, causal reservation, overlap and recovery tests."""

from datetime import timedelta
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scangoal import event_intelligence as events, scientific_receipts
from scangoal.discovery import blocks_v2, dsl, dsl_v2, engine, engine_v2, ledger, miner_v2, replay_v2, researcher
from scangoal.discovery.replay import Budget, Replay

PATH = Path(__file__).resolve().parents[1] / 'audits' / 'temporal-discovery-v2'
SPEC = importlib.util.spec_from_file_location('discovery_v2_benchmark', PATH / 'benchmark.py')
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)
BANK = BENCH.BANK
AT = BANK.START + timedelta(hours=9)
A, E = BANK.A, BANK.E


def rule(*steps, edges=(), version=2):
    return dict(version=version, within_seconds=6 * 3600, steps=[list(s) for s in steps], relations=list(edges))


REVERSAL = rule([A('direction', 'under')], [A('direction', 'over')], edges=(E('same_line', True, 'eq', 0, 1),))


def fixture(group):
    return next(name for i in range(100) if replay_v2.arm(name := f'{group}-{i}') == group)


def quote(world, identity, start, minute, direction='over', price='1.90', delay=10, *, side=None):
    return world.emit(identity, start + timedelta(hours=5), start + timedelta(minutes=minute),
                      source='Polymarket', signal='POLYMARKET', phase='pre', direction=direction,
                      line=side or '2.5', odds=price, delay=delay)


def audit(world, clock):
    return BENCH.at_cut(dict(as_of_utc=events.iso(clock), input_sha256={'messages.jsonl': 'a' * 64},
                             events=world.events, episodes=world.episodes, fixture_results=world.results), clock)


class LanguageV2(unittest.TestCase):
    def test_conjunctions_and_versions_are_distinct(self):
        criteria = [A('source', 'Polymarket'), A('direction', 'over'), A('market_key', 'match_goals'),
                    A('phase', 'pre'), A('point_odds', 1.5, 'ge'), A('point_odds', 4, 'lt'),
                    A('period', 'regulation')]
        definition = rule([A('phase', 'pre')], criteria)
        self.assertEqual(len(dsl_v2.canonical(definition)['steps'][-1]), 7)
        self.assertEqual(dsl_v2.identifier(definition), dsl_v2.identifier(rule([A('phase', 'pre')], criteria[::-1])))
        with self.assertRaises(dsl.Invalid):
            dsl.canonical(dict(definition, version=1))
        compatible = rule([A('phase', 'pre')], [A('direction', 'over')])
        self.assertNotEqual(dsl_v2.identifier(compatible), dsl.identifier(dict(compatible, version=1)))
        self.assertEqual(dsl.VERSION, 1)

    def test_new_relations_check_prefix_not_the_future(self):
        bank = BANK.World(7)
        game = fixture('case')
        quote(bank, game, AT, 0)
        quote(bank, game, AT, 10, 'under', delay=2000)
        quote(bank, game, AT, 20, price='1.85')
        quote(bank, game, AT, 50, price='1.79')
        persistent = rule([A('direction', 'over')], [A('direction', 'over')],
                          edges=(E('direction_persists', True, 'eq', 0, 1),))
        early = replay_v2.Timeline(audit(bank, AT + timedelta(minutes=25)))
        late = replay_v2.Timeline(audit(bank, AT + timedelta(minutes=60)))
        first = early.run(persistent, Budget(500)).decisions[0]
        later = late.run(persistent, Budget(500)).decisions[0]
        self.assertEqual(first.describe(), later.describe())
        self.assertEqual([e['message_id'] for e in first.witness], [1, 3])

    def test_persistence_proves_a_change_even_if_another_reading_is_unknown(self):
        bank = BANK.World(4)
        left = quote(bank, 'match', AT, 0, 'under')
        missing = quote(bank, 'match', AT, 1)
        missing['direction'] = None
        changed = quote(bank, 'match', AT, 2, 'over')
        right = quote(bank, 'match', AT, 3, 'under')
        self.assertIsNone(dsl_v2.relation('direction_persists', left, right, [missing]))
        self.assertFalse(dsl_v2.relation('direction_persists', left, right, [missing, changed]))
        self.assertEqual(dsl_v2.relation('observed_between', left, right, [left, missing, changed, right]), 2)

    def test_llm_v2_requires_typed_proof_and_rejects_promises(self):
        definition = rule([A('source', 'Stake')], [A('source', 'Polymarket')],
                          edges=(E('line_persists', True, 'eq', 0, 1),))
        item = dict(rule=definition, alternative=None, mechanism='Relacionar a continuidade com a fonte.',
                    refutation='Comparar com uma mudança de linha.')
        raw = dsl.encoded({'proposals': [item, dict(item, mechanism='Lucro garantido para apostar.'),
                                         dict(item, rule=rule([A('future_goals', 3)], [A('phase', 'pre')]))]})
        proposals, rejections = researcher.proposals(raw, language=dsl_v2, candidate=miner_v2.Candidate)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].id, dsl_v2.identifier(definition))
        self.assertEqual(len(rejections), 2)
        self.assertEqual(researcher.proposals(raw)[0], [])


class CausalReservation(unittest.TestCase):
    def test_first_completion_waits_and_control_keeps_original_clock(self):
        world = BANK.World(6)
        case, control = fixture('case'), fixture('control')
        quote(world, case, AT, 0)
        quote(world, case, AT, 20, 'under')
        quote(world, case, AT, 30, 'over', price='1.91', delay=200)
        quote(world, case, AT, 45, 'over', price='4.90')
        quote(world, control, AT, 3)
        quote(world, control, AT, 15, 'under')
        quote(world, control, AT, 50, 'over', price='1.89')
        before = audit(world, AT + timedelta(minutes=32))
        after = audit(world, AT + timedelta(hours=3))
        initial = replay_v2.Timeline(before).run(REVERSAL, Budget(400)).decisions
        observed = replay_v2.Timeline(after).run(REVERSAL, Budget(400)).decisions
        self.assertEqual([(d.fixture, d.state) for d in initial], [(control, 'control')])
        self.assertEqual(initial[0].describe(), observed[0].describe())
        self.assertEqual([(d.fixture, d.state) for d in observed], [(control, 'control'), (case, 'case')])
        self.assertEqual(observed[1].at, AT + timedelta(minutes=33, seconds=20))
        self.assertEqual(observed[1].anchor['message_id'], 3)
        # The exact v1 rule is already reserved at the first negative check.
        old, _ = after.decide(dict(REVERSAL, version=1), Budget(400))
        self.assertFalse(any(d.fixture == case and d.state == 'case' for d in old))

    def test_budget_cut_does_not_allow_later_winner(self):
        world = BANK.World(3)
        game = fixture('case')
        quote(world, game, AT, 1, 'under')
        quote(world, game, AT, 2, 'over')
        budget = Budget(2)
        outcome = replay_v2.Timeline(audit(world, AT + timedelta(hours=1))).run(REVERSAL, budget)
        self.assertTrue(budget.exhausted)
        self.assertEqual(outcome.decisions, [])
        self.assertEqual(outcome.withheld['search_incomplete'], 1)


class ProspectiveBlocks(unittest.TestCase):
    def setup_replay(self, *, with_results):
        world = BANK.World(5)
        case, control = fixture('case'), fixture('control')
        ko = AT + timedelta(hours=5)
        quote(world, case, AT, 5, 'under', price='2.00')
        quote(world, case, AT, 25, 'over')
        quote(world, control, AT, 100, 'over')
        if with_results:
            world.settle(case, ko, 'over', '2.5', True)
            world.settle(control, ko, 'over', '2.5', False)
        cut = AT + timedelta(hours=8)
        replay = audit(world, cut)
        cohort = replay_v2.Timeline(replay).run(REVERSAL, Budget(500))
        return replay, cohort, blocks_v2.overlap(cohort.decisions, Budget(100))

    def test_overlap_has_no_fifteen_minute_limit_and_results_do_not_select_weights(self):
        before, cohort, design = self.setup_replay(with_results=False)
        after, added, frozen = self.setup_replay(with_results=True)
        left, right = [blocks_v2.profile(*x) for x in ((before, cohort, design), (after, added, frozen))]
        self.assertEqual(left['overlap_sha256'], right['overlap_sha256'])
        self.assertEqual(left['reservation_sha256'], right['reservation_sha256'])
        self.assertEqual(left['blocks'], 1)
        self.assertLess(left['mean_day_missing_ft_bounds'][0], 0)
        self.assertGreater(left['mean_day_missing_ft_bounds'][1], 0)
        self.assertIsNone(left['mean_day_difference'])
        self.assertGreater(right['mean_day_difference'], 0)
        self.assertIsNone(right['nominal_sign_p'])

    def test_duplicate_fixtures_and_partial_weights_are_not_admissible(self):
        _, cohort, _ = self.setup_replay(with_results=True)
        result = blocks_v2.overlap(cohort.decisions, Budget(0))
        self.assertTrue(result.exhausted)
        self.assertEqual(result.blocks, [])
        with self.assertRaises(ValueError):
            blocks_v2.overlap(cohort.decisions * 2, Budget(100))

    def test_uncertainty_and_multiple_testing(self):
        self.assertEqual(blocks_v2.median_interval([0.] * 3, [1.] * 3), [None, None])
        self.assertEqual(blocks_v2.median_interval([-.2] * 30, [.3] * 30), [-.2, .3])
        self.assertAlmostEqual(blocks_v2.sign_tail(10, 10), 2**-10)
        self.assertEqual(engine.study.holm([.01, .03, None]), [.03, .06, 1.])


class RegistryCompatibility(unittest.TestCase):
    def test_independent_publication_version_and_immutable_result_archive(self):
        as_of = BANK.START + timedelta(days=7)
        world = BANK.World(1)
        replay = audit(world, as_of)
        now = BANK.START - timedelta(days=2)
        seen = now + timedelta(hours=1)
        with TemporaryDirectory() as folder:
            registry, output = Path(folder) / 'proposals', Path(folder) / 'evidence'
            for definition, language, protocol, code in (
                    (dict(REVERSAL, version=1), dsl, engine.PROTOCOL, engine.INTERPRETER_SHA256),
                    (REVERSAL, dsl_v2, engine_v2.PROTOCOL, engine_v2.INTERPRETER_SHA256)):
                ledger.append(registry, discovery_as_of=now-timedelta(hours=1), now=now,
                              manifest=replay.audit['input_sha256'], snapshot_sha256=replay.snapshot_sha256,
                              records=[dict(id=language.identifier(definition), rule=language.canonical(definition),
                                            origin='test', status='insufficient', reason='test',
                                            track_prospectively=True)],
                              investigation={'protocol_sha256': dsl.digest(protocol), 'interpreter_sha256': code})
            batches = ledger.read(registry)

            def proof(_, domain, payload, *, as_of):
                return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, payload), seen)

            pending = engine_v2.evaluate(replay, batches)
            self.assertEqual(len(pending['investigations']), 1)
            self.assertEqual(pending['investigations'][0]['reason'], 'awaiting_independent_publication')
            witnessed = engine_v2.evaluate(replay, batches, verify=proof)
            self.assertEqual(witnessed['investigations'][0]['born_utc'], events.iso(seen))
            self.assertEqual(witnessed['investigations'][0]['prospective_fixtures'], 0)
            self.assertEqual(witnessed['investigations'][0]['status'], 'insufficient')
            legacy = engine.evaluate(replay, batches[:1], verify=proof)
            self.assertEqual(legacy['investigations'][0]['interpreter'], 'unchanged')
            head = engine_v2.archive_evidence(output, witnessed)
            self.assertEqual(head, engine_v2.archive_evidence(output, witnessed))
            self.assertEqual(len(list(output.glob('*.json'))), 1)
            self.assertEqual(ledger.read(registry), batches)


class DiscoveryAcceptance(unittest.TestCase):
    def test_reference_recovers_at_least_four_on_all_three_reference_seeds(self):
        for seed in BENCH.REFERENCE_SEEDS:
            replay, labels = BANK.build(seed)
            score = BENCH.retrieval(replay, labels, 2)
            self.assertGreaterEqual(len(score['recovered']), 4, score)
            self.assertLessEqual(score['selected'], miner_v2.MAX_CANDIDATES)
            self.assertLessEqual(score['work']['generator_operations'], miner_v2.MAX_EMISSIONS)
            self.assertFalse(score['structural_replay_truncated'])

    def test_outcomes_do_not_change_the_shortlist_and_search_budget_is_bounded(self):
        reference, _ = BANK.build(83, days=5)
        stripped = Replay(dict(reference.audit, fixture_results={}))
        found, stats = miner_v2.mine(replay_v2.Timeline(reference), emissions=12_000)
        absent, _ = miner_v2.mine(replay_v2.Timeline(stripped), emissions=12_000)
        self.assertEqual([candidate.id for candidate in found], [candidate.id for candidate in absent])
        self.assertLessEqual(stats['generator_operations'], 12_000)

    def test_false_promising_motif_does_not_survive_a_witnessed_future(self):
        assay = BENCH.power(83, 2)
        self.assertEqual(assay['false_retained'], 0, assay)
        self.assertTrue(all(r['status'] in ('experimental', 'rejected') for r in assay['rows']), assay)


if __name__ == '__main__':
    unittest.main()

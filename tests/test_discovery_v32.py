"""Adversarial tests of atomic decisions, completeness and protocol isolation."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import event_intelligence as events, scientific_receipts
from scangoal.discovery import __main__ as cli, dsl, quarantine, researcher
from scangoal.discovery.replay import Budget
from scangoal.discovery.v3 import protocol as previous
from scangoal.discovery.v32 import engine, language, mining, protocol, registry, riskset, source
from scangoal.discovery.v32.tape import Tape

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('v32_falsification_bank', ROOT / 'audits/temporal-discovery-v32/bank.py')
BANK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BANK)
T = BANK.T
MIN = timedelta(minutes=1)
SEC = timedelta(seconds=1)


class RiskSet(unittest.TestCase):
    def test_independent_falsification_bank(self):
        result = BANK.run()
        self.assertEqual(len(result['scenarios']), 20)
        self.assertEqual(result['pair_expectations'], {0: 15, 1: 5})

    def test_full_coverage_still_does_not_turn_unknown_price_features_into_absence(self):
        rows, spans, _ = BANK.example('unknown_feature')
        tape = Tape(rows, T, spans)
        cohort = riskset.RiskSet(tape).run(BANK.RULE, Budget(10_000))
        self.assertFalse(cohort.pairs)
        self.assertGreater(cohort.counts['antecedent_indeterminate'], 0)

    def test_negative_requires_coverage_but_late_positive_is_detected_with_no_proof(self):
        rows, spans, _ = BANK.example('proved_present_with_incomplete_coverage')
        tape = Tape(rows, T, spans)
        cohort = riskset.RiskSet(tape).run(BANK.RULE, Budget(10_000))
        self.assertEqual({r.anchor['teams'][0] for r in cohort.cases}, {'case home', 'control home'})
        self.assertFalse(cohort.pairs)

    def test_evaluates_all_simultaneous_cases_before_reserving_any_control(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        other_lead = BANK.row('second', T - 9 * MIN, kind='alert', provider='Polymarket', volume_usd=85_000)
        other = BANK.row('second', T)
        first = Tape([*rows, other, other_lead], T, caps)
        second = Tape([other_lead, other, *rows[::-1]], T, caps)
        a = riskset.RiskSet(first).run(BANK.RULE, Budget(10_000))
        b = riskset.RiskSet(second).run(BANK.RULE, Budget(10_000))
        self.assertEqual((len(a.cases), len(a.admissible), len(a.pairs)), (2, 2, 1))
        self.assertEqual(a.admissible, b.admissible)
        self.assertEqual(engine.pair_signature(a), engine.pair_signature(b))

    def test_restarts_and_reuses_design_with_no_leftover_reservations(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        tape = Tape(rows, T, caps)
        design = riskset.RiskSet(tape)
        first = design.run(BANK.RULE, Budget(10_000))
        self.assertEqual(engine.pair_signature(first), engine.pair_signature(design.run(BANK.RULE, Budget(10_000))))
        metered = design.run(BANK.RULE, Budget(1))
        self.assertTrue(metered.counts['budget_exhausted'])
        self.assertEqual((metered.cases, metered.pairs, metered.admissible), ([], [], []))

    def test_duplicate_coverage_and_rejection_evidence_are_idempotent(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        hole = BANK.row('control', T - MIN, received=T, kind='gap')
        original = Tape([*rows, hole], T, caps)
        repeated = Tape([hole, *rows, hole], T, [*caps, *caps])
        self.assertEqual(original.snapshot_sha256, repeated.snapshot_sha256)
        self.assertEqual(BANK.compare(original), BANK.compare(repeated))
        self.assertEqual(BANK.compare(repeated)['pairs_at_t'], 0)

    def test_future_results_and_received_after_cutoff_never_change_earlier_pairing(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        left = Tape(rows, T, caps)
        future = BANK.row('control', T + 10 * MIN, kind='goal', score=[3, 0], minute=80)
        right = Tape([future, *(dict(row, result_score=[7, 1], ft=[7, 1], payoff=1) for row in rows)], T, caps)
        self.assertEqual(left.snapshot_sha256, right.snapshot_sha256)
        self.assertEqual(BANK.compare(left), BANK.compare(right))
        self.assertRaises(dsl.Invalid, language.canonical,
                          dict(BANK.RULE, steps=[[dict(field='ft', op='eq', value=3)], []]))

    def test_later_live_score_change_blocks_the_older_quote(self):
        kickoff = T - 20 * MIN
        case_alert = BANK.row('case', T - 6 * MIN, kickoff=kickoff, kind='alert', provider='Polymarket',
                              volume_usd=60_000, minute=14, score=[0, 0], phase='live')
        case = BANK.row('case', T, kickoff=kickoff, phase='live', score=[0, 0], minute=20)
        control = BANK.row('control', T - 3 * MIN, kickoff=kickoff, phase='live', score=[0, 0], minute=17)
        change = BANK.row('control', T - MIN, received=T, kickoff=kickoff, kind='goal',
                          phase='live', score=[1, 0], minute=19)
        self.assertEqual(BANK.compare(Tape([case_alert, case, control], T, [BANK.cap(control)]))['pairs_at_t'], 1)
        self.assertEqual(BANK.compare(Tape([case_alert, case, control, change], T,
                                          [BANK.cap(control)]))['pairs_at_t'], 0)

    def test_count_and_persistence_predicates_need_proven_observation_of_the_interior(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        rule = language.canonical(dict(BANK.RULE, relations=[
            dict(field='observed_between', op='eq', value=0, **{'from': 0, 'to': 1})]))
        self.assertFalse(riskset.RiskSet(Tape(rows, T, caps)).run(rule, Budget(10_000)).cases)
        proven = Tape(rows, T, [*caps, BANK.cap(rows[1])])
        self.assertEqual(len(riskset.RiskSet(proven).run(rule, Budget(10_000)).pairs), 1)


def archived_row(mid, text, when, *, received=None, receipt=1, edited=None):
    row = dict(message_id=mid, text=text, date_utc=events.iso(when), collected_at_utc=events.iso(received or when),
               edit_date_utc=events.iso(edited), sha256=hashlib.sha256(text.encode()).hexdigest())
    if receipt is not None:
        row['receipt_version'] = receipt
    return row


def stake(teams, alias):
    return (f'💸 🟦 Stake money load — $20K\nLeague · 8 feb 2029 21:00\n{teams}\n'
            f'📊 Asian Total → Over 2.5 @ 2.05\n🧾 1 bet · biggest $20K\nhttps://scangoal.com/match/{alias}')


def poly(teams, alias):
    return (f'📈 🔷 Polymarket · 8 feb 2029 21:00\n{teams} · League\nOver 2.5 @2.00 · $90K\n'
            f'🧾 1 trade · biggest $8K\nhttps://scangoal.com/match/{alias}')


class Journal(unittest.TestCase):
    def test_storage_order_ids_duplicates_and_malformed_receipts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'messages.jsonl'
            rows = [
                archived_row(7, poly('Alpha — Beta', 'a'), T - 12 * MIN),
                archived_row(8, stake('Alpha — Beta', 'a'), T),
                archived_row(9, stake('Charlie — Delta', 'b'), T),
                archived_row(10, poly('Epsilon — Gamma', 'c'), T, receipt=True),
            ]

            def load(messages):
                path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in messages), encoding='utf-8')
                tape = source.journal(folder, T)
                controls = [BANK.cap(row) for fixture in tape.by_fixture.values() for row in fixture
                            if row['teams'][0] == 'charlie']
                return Tape([dict(row, match_id=fixture) for fixture, history in tape.by_fixture.items()
                             for row in history], T, controls)

            first = load(rows)
            copies = [dict(row, message_id=30 - row['message_id']) for row in rows]
            mixed = [*copies, copies[2], dict(copies[2], message_id=99)]
            Random(46217).shuffle(mixed)
            second = load(mixed)
            self.assertEqual(len(first.by_fixture), 2)
            self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)
            self.assertEqual(BANK.compare(first), BANK.compare(second))
            self.assertEqual(BANK.compare(first)['pairs_at_t'], 1)

    def test_edited_goals_appear_only_when_observed_and_never_as_full_time(self):
        with TemporaryDirectory() as folder:
            body = stake('Alpha — Beta', 'a')
            edit = body + "\n⚽ 12'   1 - 0\n🏁 FT 1:0\nΣ on this outcome overall: $40K"
            kickoff = T + 120 * MIN
            rows = [archived_row(3, body, T),
                    archived_row(3, edit, T, edited=kickoff + 110 * MIN, received=kickoff + 115 * MIN)]
            path = Path(folder) / 'messages.jsonl'
            path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
            early = source.journal(folder, kickoff + 114 * MIN)
            late = source.journal(folder, kickoff + 115 * MIN)
            self.assertFalse(any(e['event_kind'] == 'goal' for rows in early.by_fixture.values() for e in rows))
            goal, = [e for rows in late.by_fixture.values() for e in rows if e['event_kind'] == 'goal']
            self.assertEqual(events.seen(goal), kickoff + 115 * MIN)
            self.assertEqual(events.emitted(goal), kickoff + 110 * MIN)
            self.assertEqual((goal['score'], goal['minute']), ([1, 0], 12))
            self.assertNotIn('ft', goal)


class ScientificProtocol(unittest.TestCase):
    def test_protocol_and_interpreter_identity_and_archived_gate(self):
        pinned = '28f5e47f23e052e55d4a75481cdc23a0b9b1611e3e441933038ffaa01ed9c8ad'
        self.assertEqual(dsl.digest(protocol.PROTOCOL), pinned)
        self.assertEqual(protocol.PROTOCOL_SHA256, pinned)
        self.assertNotEqual(protocol.PROTOCOL_SHA256, previous.PROTOCOL_SHA256)
        self.assertIn(previous.PROTOCOL_SHA256, quarantine.BLOCKED)
        with TemporaryDirectory() as folder:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(['discover', '--version', '3', '--registry', folder, '--commit',
                          '--as-of', '2026-11-24T08:22:00Z'])
            with redirect_stdout(io.StringIO()) as output:
                cli.main(['evaluate', '--version', '3', '--registry', folder, '--as-of', '2026-11-24T08:22:00Z'])
            data = json.loads(output.getvalue())
            self.assertFalse(data['confirmatory'])
            self.assertEqual(data['confirmatory_claims'], 'blocked')

    def test_witnessed_plan_binds_rule_code_and_window_without_peeking(self):
        rows, caps, _ = BANK.example('atomic_receipt')
        hypotheses = [dict(id=registry.hypothesis_id(BANK.RULE), rule=language.canonical(BANK.RULE), origin='synthetic')]
        cutoff = T - timedelta(days=2)
        with TemporaryDirectory() as folder:
            registered = registry.append(folder, cutoff=cutoff, snapshot='a' * 64, interpreter=engine.INTERPRETER_SHA256,
                                         hypotheses=hypotheses, now=cutoff)
            self.assertIsNone(registry.append(folder, cutoff=cutoff, snapshot='a' * 64,
                                              interpreter=engine.INTERPRETER_SHA256, hypotheses=hypotheses,
                                              now=cutoff, expected_head=registered['sha256']))
            plans = registry.read(folder, expected_head=registered['sha256'])
        plan, = plans

        def verify(receipts, domain, content, *, as_of):
            self.assertEqual(domain, registry.DOMAIN)
            return scientific_receipts.Receipt('synthetic', registry.digest(content), cutoff + MIN)

        tape = Tape(rows, T, caps)
        result = engine.evaluate(tape, plans, outcomes=object(), verify=verify)
        self.assertFalse(result['confirmatory'])
        self.assertEqual(result['investigations'][0]['reason'], 'forward_window_or_settlement_open')
        legacy = dict(plan, protocol_sha256=previous.PROTOCOL_SHA256)
        self.assertEqual(engine.evaluate(tape, [legacy])['investigations'][0]['reason'], 'blocked_or_unsupported_protocol')
        with self.assertRaises(registry.RegistryError):
            registry.validate(legacy, 1, None, None)
        self.assertEqual(engine.evaluate(tape, [dict(plan, interpreter_sha256='b' * 64)])['investigations'][0]['reason'],
                         'interpreter_changed_since_registration')

        class Labels:
            def label(self, decision):
                return dict(status='settled', payoff=1.)

        closed = Tape(rows, T + timedelta(days=95), caps)
        result = engine.evaluate(closed, plans, outcomes=Labels(), verify=verify)
        row = result['investigations'][0]
        self.assertFalse(result['confirmatory'])
        self.assertEqual(row['evidence']['counts']['matched_pairs'], 1)
        self.assertEqual(row['status'], 'insufficient')

    def test_discovery_and_model_envelope_accept_new_enriched_sequences(self):
        rows = []
        for day in (0, 1, 2, 3):
            at = T + timedelta(days=day)
            kickoff = at - 20 * MIN
            name = f'live-{day}'
            rows.extend([
                BANK.row(name, at - 7 * MIN, kickoff=kickoff, phase='live', minute=13, score=[0, 0]),
                BANK.row(name, at - 5 * MIN, kickoff=kickoff, kind='goal', phase='live', minute=15, score=[1, 0]),
                BANK.row(name, at - 3 * MIN, kickoff=kickoff, kind='revision', biggest_ticket_usd=2650.,
                         volume_cumulative_usd=42_000.),
                BANK.row(name, at, kickoff=kickoff, phase='live', minute=20, score=[1, 0]),
            ])
        tape = Tape(rows, T + timedelta(days=3))
        trajectory = mining.Timeline(tape)
        prompt = mining.prompt(trajectory)
        self.assertIn('volume_cumulative_usd', prompt)
        self.assertIn('"event_kind":"goal"', prompt)
        self.assertLessEqual(len(prompt.encode()), researcher.MAX_PROMPT_BYTES)
        learned, _ = mining.enrich(trajectory, emissions=150_000)
        self.assertTrue(any(any(a['value'] == 'goal' for step in h.rule['steps'] for a in step)
                            for h in learned))
        rule = dict(version=32, within_seconds=600,
                    steps=[[dict(field='event_kind', op='eq', value='goal')],
                           [dict(field='event_kind', op='eq', value='revision'),
                            dict(field='volume_cumulative_usd', op='ge', value=40000)],
                           [dict(field='point_odds', op='gt', value=1.4), dict(field='goals', op='ge', value=1)]],
                    relations=[dict(field='gap_seconds', op='ge', value=240, **{'from': 0, 'to': 2})])
        reply = dsl.encoded(dict(proposals=[dict(rule=rule, alternative=None,
                                              mechanism='Mudança de marcador seguida de atividade acumulada.',
                                              refutation='Atraso diferente pode separar as trajetórias.')]))
        model, denied = researcher.proposals(reply, language=language, candidate=mining.Candidate)
        self.assertEqual((len(model), denied), (1, []))
        results = engine.discover(tape, reply=reply, work=10_000, emissions=20_000)
        accepted = next(r for r in results['investigations'] if r['id'] == registry.hypothesis_id(rule))
        self.assertEqual(accepted['counts']['cases'], 4)
        self.assertFalse(results['proposed_registration'])
        self.assertFalse(results['confirmatory'])


if __name__ == '__main__':
    unittest.main()

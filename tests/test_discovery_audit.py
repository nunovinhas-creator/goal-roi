"""Adversarial pre-merge audit of the temporal discovery engine (PR #83)."""

from datetime import timedelta
import importlib.util
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scangoal import chronology, event_audit
from scangoal.discovery import __main__ as cli, dsl, engine, ledger, researcher, study
from scangoal.discovery.replay import SETTLEMENT, Budget, Replay

from test_autonomous_discovery import (DISCOVERY, FLIP, PUBLISHED, SOURCE, WITNESSED, JournalFixture, alert, receipt,
                                       record, stamp)

BENCH = Path(__file__).resolve().parents[1] / 'audits' / 'temporal-discovery' / 'adversarial_bench.py'
START = (WITNESSED + timedelta(days=1)).replace(hour=0, minute=0, second=0)
CLOSED = START + study.WINDOW + SETTLEMENT + timedelta(hours=1)


def variant(seconds):
    return dict(FLIP, within_seconds=seconds)


def evidence(p, difference):
    return dict(matched=dict(sign_test_p=p, average_day_difference=difference))


def _publish(directory):
    return ledger.append(directory, discovery_as_of=DISCOVERY, manifest={'messages.jsonl': 'f' * 64},
                         snapshot_sha256='0' * 64, records=[record()],
                         investigation={'protocol_sha256': dsl.digest(engine.PROTOCOL)}, now=PUBLISHED)


class Memory(JournalFixture):
    def publish(self, *records, investigation=None, now=PUBLISHED):
        return ledger.append(self.registry, discovery_as_of=DISCOVERY, manifest={'messages.jsonl': 'f' * 64},
                             snapshot_sha256='0' * 64, records=records, now=now,
                             investigation=investigation or {'protocol_sha256': dsl.digest(engine.PROTOCOL),
                                                             'interpreter_sha256': engine.INTERPRETER_SHA256})

    def test_retroactive_edit_of_an_earlier_batch_is_detected_by_the_chain(self):
        self.publish(record())
        self.publish(record(variant(1500)), now=PUBLISHED + timedelta(minutes=5))
        first = ledger.read(self.registry)[0]
        old = self.registry / f'1-{ledger.digest(first)}.json'
        # A consistent rewrite of batch 1 (new content, new name) breaks batch 2's parent link.
        edited = dict(first, records=[dict(first['records'][0], reason='rewritten')])
        old.unlink()
        (self.registry / f'1-{ledger.digest(edited)}.json').write_bytes(dsl.encoded(edited) + b'\n')
        with self.assertRaises(ledger.LedgerError):
            ledger.read(self.registry)

    def test_deleting_the_latest_batch_requires_an_external_checkpoint(self):
        self.publish(record())
        head = self.publish(record(variant(1500)), now=PUBLISHED + timedelta(minutes=5))['sha256']
        (self.registry / f'2-{head}.json').unlink()
        # Documented limitation: truncation is invisible without a retained checkpoint.
        self.assertEqual(len(ledger.read(self.registry)), 1)
        with self.assertRaises(ledger.LedgerError):
            ledger.read(self.registry, expected_head=head)

    def test_failed_publication_leaves_the_previous_ledger_and_no_partial_batch(self):
        self.publish(record())
        with patch('scangoal.discovery.ledger.os.link', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.publish(record(variant(1500)), now=PUBLISHED + timedelta(minutes=5))
        self.assertEqual(len(ledger.read(self.registry)), 1)
        self.assertEqual([p.name.startswith('1-') for p in self.registry.iterdir()], [True])
        self.assertIsNotNone(self.publish(record(variant(1500)), now=PUBLISHED + timedelta(minutes=5)))

    def test_concurrent_processes_publish_one_batch(self):
        context = multiprocessing.get_context('fork')
        with context.Pool(4) as pool:
            outcomes = pool.map(_publish, [self.registry] * 4)
        self.assertEqual(sum(o is not None for o in outcomes), 1)
        self.assertEqual(len(ledger.read(self.registry)), 1)

    def test_rejections_and_insufficient_definitions_remain_in_memory(self):
        refusal = researcher.rejection(b'x', 'invalid_json')
        self.publish(record(state='insufficient', tracking=False), refusal)
        batch = ledger.read(self.registry)[0]
        self.assertEqual({r['status'] for r in batch['records']}, {'insufficient', 'rejected'})
        # A rejected model reply is not proposed again for this snapshot.
        self.assertIn(refusal['id'], ledger.known([batch], WITNESSED, '0' * 64))

    def test_closed_window_verdicts_reject_unsupported_and_null_hypotheses(self):
        rules = [variant(s) for s in (1500, 1600, 1700, 1800)]
        self.publish(*[record(r, state='insufficient') for r in rules])
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70)]
        replay = self.replay(rows, CLOSED)
        values = {r['within_seconds']: e for r, e in zip(rules, (evidence(.001, .2), evidence(.5, .1),
                                                                  evidence(None, None), evidence(.001, -.3)))}
        real = study.profile

        def fake(replay_, decisions, pairs, **kw):
            result = real(replay_, decisions, pairs, **kw)
            result['matched'].update(values[decisions_rule[0]]['matched'])
            return result
        decisions_rule = [None]
        original_decide = Replay.decide

        def spy(self_, rule, budget, **kw):
            decisions_rule[0] = rule['within_seconds']
            return original_decide(self_, rule, budget, **kw)
        with patch.object(Replay, 'decide', spy), patch('scangoal.discovery.study.profile', fake):
            report = engine.evaluate(replay, ledger.read(self.registry), verify=receipt)
        verdicts = {r['rule']['within_seconds']: (r['status'], r['reason']) for r in report['investigations']}
        self.assertEqual(verdicts, {1500: ('experimental', 'forward_difference_survived_holm'),
                                    1600: ('rejected', 'no_forward_difference_at_close'),
                                    1700: ('rejected', 'no_forward_support_at_close'),
                                    1800: ('rejected', 'adverse_forward_difference')})
        self.assertEqual(report['investigations'][0]['multiplicity']['family_size'], 4)

    def test_open_window_never_has_a_verdict(self):
        self.publish(record(state='insufficient'))
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70)]
        row = engine.evaluate(self.replay(rows, stamp(60)), ledger.read(self.registry), verify=receipt)['investigations'][0]
        self.assertEqual((row['status'], row['reason']), ('insufficient', 'forward_window_open'))

    def test_late_data_cannot_reinterpret_a_closed_window(self):
        self.publish(record(state='insufficient'))
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70),
                alert(3, 'control', 'Over 3.5', -97), alert(4, 'control', 'Over 2.5', -68),
                alert(2, 'case', 'Over 2.5', -70, read=113, edit=112, score='2:1'),
                alert(4, 'control', 'Over 2.5', -68, read=113, edit=112, score='1:0')]
        batches = ledger.read(self.registry)
        first = engine.evaluate(self.replay(rows, CLOSED), batches, verify=receipt)['investigations'][0]
        later_minutes = int((CLOSED - stamp(0)).total_seconds() // 60) + 600
        late = rows + [
            # FT correction after the settlement deadline and a new fixture after the window.
            alert(2, 'case', 'Over 2.5', -70, read=later_minutes, edit=later_minutes - 1, score='0:0'),
            alert(9, 'after', 'Under 2.5', later_minutes - 100), alert(10, 'after', 'Over 2.5', later_minutes - 70)]
        second = engine.evaluate(self.replay(late, CLOSED + timedelta(days=40)), batches, verify=receipt)['investigations'][0]
        for key in ('status', 'reason', 'evidence', 'prospective_fixtures'):
            self.assertEqual(first[key], second[key], key)
        self.assertEqual(first['status'], 'rejected')

    def test_unknown_protocol_is_reported_without_blocking_other_families(self):
        self.publish(record(), investigation={'protocol_sha256': 'a' * 64})
        self.publish(record(variant(1500)), now=PUBLISHED + timedelta(minutes=5))
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70)]
        report = engine.evaluate(self.replay(rows, stamp(60)), ledger.read(self.registry), verify=receipt)
        registrations = [r['registration'] for r in report['investigations']]
        self.assertEqual(registrations, ['unsupported_forward_protocol', 'independently_witnessed'])

    def test_interpreter_change_withholds_any_verdict(self):
        self.publish(record(state='insufficient'), investigation={'protocol_sha256': dsl.digest(engine.PROTOCOL),
                                                                  'interpreter_sha256': '0' * 64})
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70)]
        row = engine.evaluate(self.replay(rows, CLOSED), ledger.read(self.registry), verify=receipt)['investigations'][0]
        self.assertEqual(row['interpreter'], 'changed_since_registration')
        self.assertEqual((row['status'], row['reason']), ('insufficient', 'interpreter_changed_since_registration'))

    def test_duplicate_telegram_body_does_not_add_a_second_decision(self):
        rows = [alert(1, 'dup', 'Under 2.5', -100), alert(2, 'dup', 'Over 2.5', -70), alert(3, 'dup', 'Over 2.5', -70)]
        decisions, _ = self.replay(rows, stamp(-30)).decide(FLIP, Budget(100))
        self.assertEqual([d.fixture for d in decisions], ['dup'])

    def test_cli_refuses_a_registry_inside_the_working_tree(self):
        inside = cli.REPOSITORY / 'audits' / 'temporal-discovery' / 'ledger'
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            cli.main(['discover', '--as-of', '2026-11-24T08:22:00Z', '--registry', str(inside), '--commit'])
        self.assertFalse(inside.exists())


class ModelBoundary(unittest.TestCase):
    ITEM = dict(rule=FLIP, alternative=None, mechanism='The earlier quote may change the exposure context.',
                refutation='Look for losing cases and winning matched controls.')

    def test_claims_of_return_certainty_or_betting_actions_are_rejected(self):
        for text in ('This yields +12% ROI on every match, bet now', 'Highly profitable edge on totals.',
                     'Sure win when the price drops further.', 'Value bet: aposte no Over quando subir.',
                     'Padrão lucrativo e comprovado nos dados.', 'A risk-free pattern after a reversal.'):
            with self.subTest(text=text):
                accepted, refused = researcher.proposals(dsl.encoded({'proposals': [dict(self.ITEM, mechanism=text)]}))
                self.assertEqual((accepted, refused[0]['reason']), ([], 'invalid_research_explanation'))
        accepted, _ = researcher.proposals(dsl.encoded({'proposals': [dict(
            self.ITEM, mechanism='A Stake load after a Polymarket reversal may change the price context.')]}))
        self.assertEqual(len(accepted), 1)

    def test_duplicates_fences_and_end_marker(self):
        body = dsl.encoded({'proposals': [self.ITEM, self.ITEM]})
        accepted, refused = researcher.proposals(body)
        self.assertEqual((len(accepted), [r['reason'] for r in refused]), (1, ['duplicate_model_proposal']))
        single = dsl.encoded({'proposals': [self.ITEM]})
        self.assertEqual(len(researcher.proposals(single + b' [end of text]\n')[0]), 1)
        self.assertEqual(researcher.proposals(b'```json\n' + single + b'\n```')[1][0]['reason'], 'invalid_json')

    def test_local_generation_is_grammar_constrained(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'm.gguf'
            model.touch()
            with (patch('scangoal.discovery.researcher.shutil.which', return_value='/fixed/llama-completion'),
                  patch('scangoal.discovery.researcher.subprocess.run') as runner):
                researcher.run_local(model, 'text')
        argv = runner.call_args[0][0]
        schema = dsl.strict_json(argv[argv.index('--json-schema') + 1].encode())
        self.assertEqual(schema['properties']['proposals']['maxItems'], researcher.MAX_PROPOSALS)


class HistoricalAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Replay(event_audit.build(SOURCE, DISCOVERY, quote_only=True))

    def test_no_anchor_is_decided_after_its_result_was_available(self):
        for at, e in self.data.anchors:
            for result in self.data.audit['fixture_results'].get(e['fixture_id'], []):
                self.assertGreater(chronology.parse_utc(result['available_utc']), at)

    def test_model_reply_cannot_change_deterministic_discovery(self):
        alone, records = engine.discover(self.data)
        reply = dsl.encoded({'proposals': [ModelBoundary.ITEM]})
        mixed, mixed_records = engine.discover(self.data, reply=reply)
        self.assertEqual([r for r in mixed_records if r.get('origin') == 'deterministic'], records)
        self.assertEqual(mixed['model']['proposals'], 1)


class SyntheticBench(unittest.TestCase):
    def test_bench_is_deterministic_and_planted_context_rule_is_expressible(self):
        spec = importlib.util.spec_from_file_location('adversarial_bench', BENCH)
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        a, planted = bench.build(days=4, per_day=12)
        b, _ = bench.build(days=4, per_day=12)
        self.assertEqual(a.snapshot_sha256, b.snapshot_sha256)
        live = planted.get('live_context_x_price_2', set())
        cases, _, _ = bench.case_set(a, bench.ORACLES['live_context_x_price_2'])
        self.assertEqual(cases, live)


if __name__ == '__main__':
    unittest.main()

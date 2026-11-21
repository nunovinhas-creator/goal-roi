from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import brain, certification, chronology, llm, llm_eval, opportunity_results, report, store


T = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def stamp(minutes):
    return (T + timedelta(minutes=minutes)).isoformat()


class TemporalAudit(unittest.TestCase):
    def test_N01_fully_versioned_fields(self):
        state = {'results': {}}
        first = {'ft': [2, 1], 'provider': 'BSD', 'bsd_id': 1001, 'event_date': stamp(-240),
                 'fixture_id': 'a', 'league': 'First Cup', 'home': 'Alpha', 'away': 'Beta',
                 'link': 'https://example.invalid/fixtures/1001', 'provenance': {'record': 'first'}}
        opportunity_results.remember_provider_results(state, {'a': first}, T)
        previous = opportunity_results.provider_snapshot(state, 'bsd', T)
        self.assertEqual(previous['a']['league'], 'First Cup')
        later = dict(first, ft=[0, 2], provider='Alternate', bsd_id=2001, event_date=stamp(-215),
                     league='Second Cup', link='https://example.invalid/fixtures/2001', provenance={'record': 'second'})
        opportunity_results.remember_provider_results(state, {'a': later}, T + timedelta(minutes=40))
        self.assertEqual(opportunity_results.provider_snapshot(state, 'bsd', T), previous)
        after = opportunity_results.provider_snapshot(state, 'bsd', T + timedelta(minutes=40))['a']
        self.assertEqual({k: after[k] for k in later}, later)
        self.assertEqual((after['observed_at'], after['available_at']), (stamp(40), stamp(40)))

    def test_N04_a_recomputed_hash_chain_and_local_proof_cannot_activate(self):
        ledger = certification.activate({}, T - timedelta(hours=1))
        self.assertEqual(certification.verify(ledger), [])
        status = brain.build([], {}, 300, ledger, as_of=T, history_problems=[])['validation']
        self.assertFalse(status['ledger_valid'])
        self.assertEqual(status['certified'], [])

    def test_N05_all_consumers_at_the_same_cut(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': temp}):
            directory = Path(temp)

            def write(name, value):
                (directory / name).write_text(json.dumps(value), encoding='utf-8')

            message = {'message_id': 1, 'date_utc': stamp(-25), 'collected_at_utc': stamp(-24),
                       'edit_date_utc': None, 'text': '💷 Betfair · 20 nov 2026 15:30\nAlpha — Beta · First Cup\n'
                       '1 · Seleção @2.00 · $1K\nmarket share 60% · whole 1X2 market $8K\n'
                       'https://scangoal.com/match/a?lang=en'}
            hyp = {'family': 'Betfair', 'conditions': [], 'born_utc': stamp(-45), 'source': 'llm'}
            telemetry = {'last_run_utc': stamp(-3), 'runs': 1, 'day': T.date().isoformat()}
            write('hypotheses.json', {'old': hyp})
            write('llm_state.json', telemetry)
            write('notify_state.json', {'migrations': {'polymarket_over25_v1': {'at_utc': stamp(-60)}}})
            store.append_versions([message])

            def view():
                built = report.build(now=T)
                return built, llm.opportunity_digest(T), built['brain']['llm_evaluation']

            before = view()
            write('hypotheses.json', {'old': hyp, 'future': dict(hyp, born_utc=stamp(5))})
            write('llm_state.json', dict(telemetry, last_run_utc=stamp(6), runs=2, snapshots=[telemetry]))
            write('llm_notes.json', [{'id': 'old', 'note': 'Future note', 'at_utc': stamp(7)}])
            store.append_versions([dict(message, collected_at_utc=stamp(8), edit_date_utc=stamp(8),
                                        text=message['text'] + '\n🏁 FT 1:0')])
            self.assertEqual(view(), before)
            self.assertIsNotNone(before[1])


class ReceiptAudit(unittest.TestCase):
    def test_independent_receipt_binds_domain_content_and_cutoff(self):
        from scangoal import scientific_receipts

        source = Path(__file__).parent / 'fixtures' / 'scientific-receipts'
        data = json.loads((source / 'probe.json').read_bytes())
        sealed = chronology.parse_utc('2026-11-21T09:00:35Z')
        proof = scientific_receipts.verify(source, 'offline-probe', data, as_of=sealed)
        self.assertIsNotNone(proof)
        self.assertEqual((proof.identifier, proof.at), ('freetsa:0a520ce5', sealed))
        self.assertIsNone(scientific_receipts.verify(source, 'offline-probe', data, as_of=sealed - timedelta(seconds=1)))
        with TemporaryDirectory() as folder:
            # Reusing the genuine independently signed token under the digest of another payload must fail.
            modified = dict(data, exercise='changed')
            renamed = scientific_receipts.digest('offline-probe', modified) + '.tsr'
            (Path(folder) / renamed).write_bytes((source / (proof.digest + '.tsr')).read_bytes())
            self.assertIsNone(scientific_receipts.verify(folder, 'offline-probe', modified, as_of=sealed))

    def test_cutoff_alone_cannot_verify_llm_study(self):
        outcome = llm_eval.evaluate([], {}, as_of=T, born_eff={}, protocol_time=T, problems=())
        self.assertFalse(outcome['verified'])

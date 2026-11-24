"""Adversarial and historical tests for the offline temporal research engine."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scangoal import chronology, event_audit, scientific_receipts
from scangoal.discovery import dsl, engine, ledger, miner, researcher, study
from scangoal.discovery.replay import Budget, Replay

KICKOFF = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
DISCOVERY = chronology.parse_utc('2026-11-24T08:22:00Z')
PUBLISHED = chronology.parse_utc('2026-11-24T12:00:00Z')
WITNESSED = chronology.parse_utc('2026-11-24T12:05:00Z')
SOURCE = Path(__file__).resolve().parents[1] / 'data'


def stamp(minutes):
    return KICKOFF + timedelta(minutes=minutes)


def alert(mid, fixture, pick, sent, *, read=None, edit=None, score=None, odds='1.90',
          source='Polymarket', period='', ko='25 nov 2026 15:00', legacy=False):
    league, teams = 'Test League', f'{fixture} Home — {fixture} Away'
    if source == 'Stake':
        t = (f'💸 🟦 Stake money load — $20K\n{league} · {ko}\n{teams}\n'
             f'📊 {period or "Asian Total"} → {pick} @ {odds}\n🧾 1 bet · biggest $20K')
    else:
        t = (f'📈 🔷 Polymarket · {ko}\n{teams} · {league}\n{period}{pick} @{odds} · $18K\n'
             '🧾 1 trade · market share 62%')
    t += f'\nhttps://scangoal.com/match/{fixture}'
    if score:
        t += '\n🏁 FT ' + score
    row = dict(message_id=mid, text=t, date_utc=stamp(sent).isoformat(),
               collected_at_utc=stamp(sent if read is None else read).isoformat(),
               edit_date_utc=stamp(edit).isoformat() if edit is not None else None,
               sha256=hashlib.sha256(t.encode()).hexdigest())
    if not legacy:
        row['receipt_version'] = 1
    return row


def rule(*steps, relations=(), window=21600):
    return dict(version=1, within_seconds=window, steps=[list(s) for s in steps], relations=list(relations))


def atom(field, value, op='eq'):
    return miner.atom(field, value, op)


def linked(field, value, op='eq', left=0, right=1):
    return miner.edge(field, value, op, left, right)


FLIP = rule([atom('direction', 'under')], [atom('direction', 'over'), atom('prior_alerts', 1, 'ge')],
            relations=(linked('same_line', True), linked('same_period', True)))
REVERSAL = rule([atom('direction', 'under')], [atom('direction', 'over')])


def reference(name, number):
    return scientific_receipts.Receipt(name, number, WITNESSED)


def receipt(_directory, domain, content, *, as_of):
    return reference('synthetic-witness', scientific_receipts.digest(domain, content))


def record(definition=FLIP, state='experimental', tracking=True):
    return dict(id=dsl.identifier(definition), rule=dsl.canonical(definition), status=state,
                origin='deterministic', reason='test', track_prospectively=tracking)


class JournalFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.data = self.folder / 'data'
        self.data.mkdir()
        self.registry = self.folder / 'registry'

    def replay(self, rows, as_of):
        (self.data / 'messages.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return Replay(event_audit.build(self.data, as_of))

    def publish(self, *records, manifest=None, now=PUBLISHED, snapshot='0' * 64):
        return ledger.append(self.registry, discovery_as_of=DISCOVERY,
            manifest=manifest or {'messages.jsonl': 'f' * 64}, snapshot_sha256=snapshot,
            records=records, investigation={'protocol_sha256': dsl.digest(engine.PROTOCOL)}, now=now)


class Language(unittest.TestCase):
    def test_canonical_hash_equivalence_for_order_and_redundant_bounds(self):
        a = rule([atom('volume_usd', 10, 'ge'), atom('volume_usd', 10, 'ne')],
                 [atom('phase', 'pre'), atom('source', 'Stake'), atom('phase', 'pre')],
                 relations=(linked('same_source', False, 'ne'),))
        b = rule([atom('volume_usd', 10.0, 'gt')], [atom('source', 'Stake'), atom('phase', 'pre')],
                 relations=(linked('same_source', True),))
        self.assertEqual(dsl.identifier(a), dsl.identifier(b))
        self.assertNotEqual(dsl.identifier(a), dsl.identifier(dict(b, within_seconds=90)))

    def test_rejects_cycles_future_values_incompatible_types_and_unbounded_rules(self):
        tests = [
            rule([atom('ft', '1:0')], [atom('direction', 'over')]),
            rule([atom('minute', True, 'ge')], [atom('phase', 'pre')]),
            rule([atom('source', 'Stake', 'ge')], [atom('phase', 'pre')]),
            rule([atom('point_odds', float('inf'), 'gt')], [atom('phase', 'pre')]),
            rule([atom('prior_alerts', 1)], [atom('phase', 'pre')]),
            rule([atom('source', 'Stake')], [atom('phase', 'pre')],
                 relations=(linked('same_source', True, left=1, right=0),)),
            rule([atom('source', 'Stake')], [atom('phase', 'pre')], window=dsl.MAX_WINDOW + 1),
            rule(*([[atom('phase', 'pre')]] * (dsl.MAX_STEPS + 1))),
            rule([atom('phase', 'pre'), atom('phase', 'live')], [atom('phase', 'pre')]),
            rule([atom('minute', 2, 'ge'), atom('minute', 2, 'lt')], [atom('phase', 'live')]),
        ]
        for invalid in tests:
            with self.subTest(invalid=invalid), self.assertRaises(dsl.Invalid):
                dsl.canonical(invalid)

    def test_probability_contrast_uses_kickoff_day_and_fixed_family(self):
        self.assertAlmostEqual(study.sign_p([1] * 8), 2 / 256)
        self.assertEqual(study.sign_p([-1, 1, 0]), 1)
        self.assertEqual(study.holm([.01, None, .08]), [.03, 1, .16])


class ChronologicalResearch(JournalFixture):
    def test_late_read_does_not_reclassify_reserved_anchor(self):
        rows = [
            alert(20, 'late', 'Under 2.5', -110, read=-50),
            alert(21, 'late', 'Over 2.5', -75),
            alert(22, 'late', 'Over 2.5', -45, odds='2.05'),
        ]
        early = self.replay(rows, stamp(-70))
        late = self.replay(rows, stamp(-30))
        def choices(r):
            return [(d.anchor['message_id'], d.state) for d in r.decide(REVERSAL, Budget(100))[0]]
        self.assertEqual(choices(early), [(21, 'control')])
        self.assertEqual(choices(late), choices(early))

    def test_negative_receipt_gap_is_visible_only_at_anchor_and_emissions_must_be_strict(self):
        rows = [alert(1, 'case', 'Under 2.5', -110, read=-75),
                alert(2, 'case', 'Over 2.5', -90, read=-73),
                alert(3, 'case', 'Over 2.5', -70, odds='2.05')]
        r = self.replay(rows[::-1], stamp(-30))
        seq = rule([atom('direction', 'under')], [atom('direction', 'over')],
                   relations=(linked('gap_seconds', 1500, 'le'),))
        decisions, _ = r.decide(seq, Budget(100))
        d = decisions[0]
        self.assertEqual(d.at, stamp(-73))
        self.assertEqual([e['message_id'] for e in d.witness], [1, 2])
        events = [alert(4, 'tie', 'Under 2.5', -105), alert(5, 'tie', 'Over 2.5', -105)]
        self.assertEqual(self.replay(events, stamp(-60)).decide(REVERSAL, Budget(100))[0][0].state, 'control')

    def test_price_velocity_refuses_mismatched_scope_and_ambiguous_same_second(self):
        base = [alert(1, 'market', 'Over 2.5', -90, odds='2.10', source='Stake', period='Total Goals'),
                alert(2, 'market', 'Over 2.5', -60, odds='1.90', source='Stake')]
        criteria = rule([atom('direction', 'over')], [atom('prior_alerts', 1, 'ge')],
                        relations=(linked('odds_velocity_bp_hour', -500, 'lt'),))
        changed = self.replay(base, stamp(-40))
        self.assertEqual(changed.decide(criteria, Budget(100))[0][0].state, 'unknown')
        no_cross = [alert(1, 'market', 'Over 2.5', -90, odds='2.10', source='Stake'), base[1]]
        coherent = self.replay(no_cross, stamp(-40))
        self.assertEqual(coherent.decide(criteria, Budget(100))[0][0].state, 'case')
        conflict = [*no_cross, alert(3, 'market', 'Over 2.5', -90, odds='2.31', read=-89, source='Stake')]
        self.assertEqual(self.replay(conflict, stamp(-40)).decide(criteria, Budget(100))[0][0].state, 'unknown')

    def test_sequence_of_eight_distinct_alerts_and_per_anchor_budget(self):
        same = [alert(i + 1, 'chain', 'Over 2.5', -120 + 2*i, odds=f'{1.91 + i*.02:.2f}')
                for i in range(8)]
        r = self.replay(same, stamp(-30))
        definition = rule(*([[atom('direction', 'over')]] * 7),
                          [atom('direction', 'over'), atom('prior_alerts', 7, 'ge')])
        outcomes, _ = r.decide(definition, Budget(200))
        self.assertEqual([e['message_id'] for e in outcomes[0].witness], list(range(1, 9)))
        # Search is incomplete, so it cannot supply a negative comparator.
        budget = Budget(11)
        paths, _ = r.decide(definition, budget)
        self.assertEqual((paths[0].state, budget.used, budget.exhausted), ('search_budget', 11, True))

    def test_result_versions_are_held_out_and_settlement_deadline_does_not_move(self):
        alerts = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -63)]
        ft = alert(2, 'case', 'Over 2.5', -63, read=112, edit=111, score='2:1')
        changed = alert(2, 'case', 'Over 2.5', -63, read=5000, edit=4999, score='0:0')
        first = self.replay(alerts + [ft, changed], stamp(60))
        last = self.replay(alerts + [ft, changed], stamp(5100))
        a, _ = first.decide(FLIP, Budget(100))
        b, _ = last.decide(FLIP, Budget(100))
        self.assertEqual([x.describe() for x in a], [x.describe() for x in b])
        self.assertEqual(first.label(a[0])['status'], 'pending_ft')
        self.assertEqual(last.label(b[0])['ft'], (2, 1))

    def test_old_fixture_and_legacy_receipt_cannot_enter_forward(self):
        old = alert(1, 'old', 'Under 2.5', -1680)
        rows = [old, alert(2, 'old', 'Over 2.5', -73),
                alert(3, 'legacy', 'Under 2.5', -100), alert(4, 'legacy', 'Over 2.5', -75, legacy=True),
                alert(5, 'new', 'Under 2.5', -101), alert(6, 'new', 'Over 2.5', -70)]
        r = self.replay(rows, stamp(-30))
        results, _ = r.decide(FLIP, Budget(100), start=KICKOFF.replace(hour=0), end=KICKOFF + timedelta(days=90),
                              discovery_cutoff=DISCOVERY)
        self.assertEqual([d.fixture for d in results], ['new'])

    def test_forward_pair_reserves_both_fixtures_before_results(self):
        rows = [alert(1, 'case', 'Under 2.5', -100), alert(2, 'case', 'Over 2.5', -70),
                alert(3, 'control', 'Over 3.5', -97), alert(4, 'control', 'Over 2.5', -68),
                alert(5, 'control', 'Under 2.5', -67), alert(6, 'control', 'Over 2.5', -60, odds='1.92'),
                alert(7, 'late-control', 'Over 3.5', -95), alert(8, 'late-control', 'Over 2.5', -48)]
        before = self.replay(rows, stamp(-40))
        self.publish(record(state='insufficient'))
        batches = ledger.read(self.registry)
        zero = engine.evaluate(before, batches)
        self.assertEqual(zero['investigations'][0]['prospective_fixtures'], 0)
        active = engine.evaluate(before, batches, verify=receipt)['investigations'][0]
        self.assertEqual(active['registration'], 'independently_witnessed')
        self.assertEqual(active['prospective_fixtures'], 3)
        self.assertEqual(active['evidence']['matched']['pairs'], 1)
        self.assertEqual(active['evidence']['matched']['priced_pairs'], 0)
        settled = [alert(2, 'case', 'Over 2.5', -70, read=113, edit=112, score='2:1'),
                   alert(4, 'control', 'Over 2.5', -68, read=113, edit=112, score='1:0')]
        updated = engine.evaluate(self.replay(rows + settled, stamp(125)), batches, verify=receipt)['investigations'][0]
        example = updated['evidence']['matched']['examples'][0]
        self.assertEqual(example['control']['events'][0]['message_id'], 4)
        self.assertEqual(updated['evidence']['matched']['priced_pairs'], 1)
        for key in ('reservation_sha256', 'pairing_sha256'):
            self.assertEqual(updated['evidence'][key], active['evidence'][key])
        self.assertEqual(updated['evidence']['matched']['average_pair_difference'], 1.9)
        self.assertIsNone(updated['multiplicity']['adjusted_p'])
        expected = ledger.digest(batches[0])
        self.assertEqual(ledger.digest(ledger.read(self.registry)[0]), expected)

    def test_projection_does_not_materialize_quadratic_pairs_for_a_busy_fixture(self):
        rows = [alert(i+1, 'busy', 'Over 2.5', -470+i, odds=f'{1.12 + i*.005:.3f}') for i in range(320)]
        (self.data / 'messages.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        with patch('scangoal.event_audit.relation', side_effect=AssertionError('do not expand event pairs')):
            view = event_audit.build(self.data, stamp(-30), quote_only=True)
        self.assertEqual(len(view['episodes']), 320)
        self.assertTrue(all(row['kind'] == 'quote' for row in view['episodes']))

    def test_asian_push_and_unpriced_range_are_retained(self):
        rows = [alert(1, 'push', 'Under 3', -100, source='Stake'),
                alert(2, 'push', 'Over 3', -70, source='Stake', odds='1.90–2.00'),
                alert(2, 'push', 'Over 3', -70, source='Stake', odds='1.90–2.00', edit=110, read=111, score='2:1')]
        r = self.replay(rows, stamp(120))
        selected, comparisons = r.decide(FLIP, Budget(100))
        info = study.profile(r, selected, comparisons)
        self.assertEqual(info['arms']['case']['pushes'], 1)
        self.assertEqual(info['arms']['case']['price_available'], 0)
        self.assertEqual(info['arms']['case']['priced_settled'], 0)


class WriteAndModelBoundaries(JournalFixture):
    def test_concurrent_dedup_content_hash_and_checkpoint(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.publish(record()), range(4)))
        self.assertEqual(sum(result is not None for result in results), 1)
        original = ledger.read(self.registry)
        first_hash = ledger.digest(original[0])
        self.publish(record(dict(FLIP, within_seconds=1500)), now=PUBLISHED + timedelta(minutes=10))
        rows = ledger.read(self.registry, expected_head=first_hash)
        self.assertEqual(len(rows), 2)
        self.assertEqual(ledger.digest(rows[0]), first_hash)
        head = ledger.digest(rows[1])
        path = self.registry / f'2-{head}.json'
        original_bytes = path.read_bytes()
        path.write_bytes(original_bytes.replace(b'"test"', b'"modified"'))
        with self.assertRaises(ledger.LedgerError):
            ledger.read(self.registry)
        path.unlink()
        with self.assertRaises(ledger.LedgerError):
            ledger.read(self.registry, expected_head=head)

    def test_as_of_and_receipt_are_fail_closed(self):
        self.publish(record())
        batch = ledger.read(self.registry)[0]
        self.assertEqual(ledger.visible([batch], PUBLISHED - timedelta(seconds=1)), [])
        too_early = lambda *a, **k: scientific_receipts.Receipt('early', ledger.digest(batch), DISCOVERY)
        wrong = lambda *a, **k: reference('wrong-digest', '0' * 64)
        self.assertIsNone(ledger.attest(batch, self.folder, WITNESSED, verify=too_early))
        self.assertIsNone(ledger.attest(batch, self.folder, WITNESSED, verify=wrong))
        self.assertIsNone(ledger.attest(batch, self.folder, PUBLISHED, verify=receipt))
        self.assertIsNotNone(ledger.attest(batch, self.folder, WITNESSED, verify=receipt))

    def test_real_timestamp_verifier_accepts_only_the_exact_local_batch(self):
        # Create an ephemeral TSA chain. The repository trust store stays untouched.
        def run(*args):
            subprocess.run(['openssl', *args], cwd=self.folder, check=True, capture_output=True, timeout=10)

        run('req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Test Root',
            '-keyout', 'root.key', '-out', 'root.pem', '-days', '1',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Test Clock',
            '-keyout', 'tsa.key', '-out', 'tsa.csr')
        (self.folder / 'extensions').write_text('basicConstraints=critical,CA:FALSE\n'
            'extendedKeyUsage=critical,timeStamping\nkeyUsage=critical,digitalSignature\n')
        run('x509', '-req', '-in', 'tsa.csr', '-CA', 'root.pem', '-CAkey', 'root.key',
            '-CAcreateserial', '-out', 'tsa.pem', '-days', '1', '-extfile', 'extensions')
        (self.folder / 'serial').write_text('01\n')
        (self.folder / 'tsa.cnf').write_text('''[tsa]
dir=.
serial=$dir/serial
signer_cert=$dir/tsa.pem
signer_key=$dir/tsa.key
certs=$dir/root.pem
signer_digest=sha256
default_policy=1.2.3.4.5
digests=sha256
ordering=no
tsa_name=no
ess_cert_id_chain=no
''')
        local = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
        ledger.append(self.registry, discovery_as_of=local - timedelta(minutes=2),
                      manifest={'messages.jsonl': 'f' * 64}, snapshot_sha256='0' * 64,
                      records=[record()], investigation={'protocol_sha256': dsl.digest(engine.PROTOCOL)}, now=local)
        batch = ledger.read(self.registry)[0]
        imprint = ledger.digest(batch)
        run('ts', '-query', '-digest', imprint, '-sha256', '-cert', '-out', 'query.tsq')
        run('ts', '-reply', '-queryfile', 'query.tsq', '-config', 'tsa.cnf', '-section', 'tsa',
            '-out', imprint + '.tsr')
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        ca = self.folder / 'root.pem'
        sha = x509.load_pem_x509_certificate(ca.read_bytes()).fingerprint(hashes.SHA256()).hex()
        with patch.multiple(scientific_receipts, ROOT=ca, ROOT_SHA256=sha):
            proof = ledger.attest(batch, self.folder, datetime.now(timezone.utc))
            self.assertEqual(proof.digest, imprint)
            self.assertIsNone(ledger.attest(dict(batch, previous='f' * 64), self.folder, datetime.now(timezone.utc)))

    def test_model_response_is_bounded_strict_and_cannot_certify(self):
        item = dict(rule=FLIP, alternative=miner.contrast(FLIP),
                    mechanism='The earlier quote may change the exposure context.',
                    refutation='Look for losing cases and winning matched controls.')
        results, errors = researcher.proposals(dsl.encoded({'proposals': [item]}))
        self.assertEqual(results[0].id, dsl.identifier(FLIP))
        self.assertFalse(errors)
        for raw in (b'{"proposals":[],"proposals":[]}', b'`{"proposals":[]}`',
                    b' ' * (researcher.MAX_REPLY_BYTES + 1),
                    dsl.encoded({'proposals': [dict(item, status='validated')]}),
                    dsl.encoded({'proposals': [dict(item, mechanism='lucro comprovado')]}),
                    dsl.encoded({'proposals': [item] * (researcher.MAX_PROPOSALS + 1)})):
            valid, rejections = researcher.proposals(raw)
            self.assertFalse(valid)
            self.assertEqual(rejections[0]['status'], 'rejected')
            self.assertTrue(rejections[0]['id'].startswith('rejected:'))

    def test_local_inference_has_no_cloud_fallback_and_uses_fixed_subprocess_budget(self):
        gguf = self.folder / 'candidate.gguf'
        gguf.touch()
        with patch('scangoal.discovery.researcher.shutil.which', return_value=None):
            self.assertEqual(researcher.run_local(gguf, 'text'), (None, 'local_model_or_runtime_unavailable'))
        with (patch('scangoal.discovery.researcher.shutil.which', return_value='/fixed/llama-completion'),
              patch('scangoal.discovery.researcher.subprocess.run',
                    side_effect=subprocess.TimeoutExpired('llama-completion', 45)) as runner):
            self.assertEqual(researcher.run_local(gguf, 'text'), (None, 'local_model_timeout'))
            args, opts = runner.call_args
            self.assertIn(str(researcher.MAX_TOKENS), args[0])
            self.assertEqual(opts['timeout'], researcher.TIMEOUT_SECONDS)
            self.assertNotIn('shell', opts)
            self.assertEqual(runner.call_count, 1)


class HistoricalResearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = Replay(event_audit.build(SOURCE, DISCOVERY))
        cls.data = Replay(event_audit.build(SOURCE, DISCOVERY, quote_only=True))

    def test_quote_projection_preserves_pr_a_clock_without_expanding_pairs(self):
        self.assertEqual(self.data.snapshot_sha256, self.full.snapshot_sha256)
        self.assertTrue(any(r['kind'] == 'sequence' for r in self.full.audit['episodes']))
        self.assertTrue(all(r['kind'] == 'quote' for r in self.data.audit['episodes']))

    def test_deterministic_discovery_spans_temporal_shapes_and_has_work_ceiling(self):
        first, records = engine.discover(self.data)
        second, _ = engine.discover(self.data)
        self.assertEqual(first, second)
        self.assertTrue(records)
        self.assertIn(5, {len(record['rule']['steps']) for record in records})
        self.assertTrue(any(record['rule']['relations'] for record in records))
        self.assertTrue(any(record.get('alternative_probe') for record in first['investigations']))
        self.assertLessEqual(first['work']['pattern_observations'], miner.MAX_EMISSIONS)
        self.assertLessEqual(first['work']['search_expansions'], miner.MAX_REPLAY_WORK)
        self.assertEqual(first['prospective_fixtures'], 0)
        small, records = engine.discover(self.data, work=2, emissions=40)
        self.assertTrue(small['work']['generator_truncated'])
        self.assertLessEqual(small['work']['search_expansions'], 2)
        self.assertFalse(any(record.get('track_prospectively') for record in records))

    def test_as_of_invariants_use_the_same_receipts_and_prefix_on_real_records(self):
        before = Replay(event_audit.build(SOURCE, chronology.parse_utc('2026-11-23T19:00:00Z')))
        first, _ = before.decide(FLIP, Budget(50_000))
        final, _ = self.data.decide(FLIP, Budget(50_000))
        self.assertTrue(first)
        self.assertEqual([d.describe() for d in first], [d.describe() for d in final if d.at <= before.as_of])


if __name__ == '__main__':
    unittest.main()

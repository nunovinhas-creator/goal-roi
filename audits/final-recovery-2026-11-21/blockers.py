"""Independent acceptance probes. Run from the root of the code under test."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.getcwd())
from scangoal import brain, details_hl, opportunity_results, report, scientific_receipts, store

T = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def at(minutes):
    return T + timedelta(minutes=minutes)


def stamp(minutes):
    return at(minutes).isoformat()


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Audit', '-c',
                           'user.email=audit@example.invalid', *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def publication_child():
    from io import BytesIO
    import urllib.request
    from scangoal.publication import PublicationError, Publisher

    ledger = Path(sys.argv[2])
    operation = sys.argv[3]

    def durable(request, **kwargs):
        from urllib.parse import parse_qs, urlsplit
        current = json.loads(ledger.read_text()) if ledger.exists() else {}
        if request.get_method() == 'GET':
            key = parse_qs(urlsplit(request.full_url).query)['key'][0]
            result = current.get(key, {'revision': 0, 'value': None})
        else:
            assert request.get_method() == 'PUT'
            item = json.loads(request.data)
            key = item['key']
            prior = current.get(key, {}).get('revision', 0)
            assert prior == item['revision']
            result = {'revision': prior + 1}
            current[key] = dict(result, value=item['value'])
            temporary = ledger.with_suffix('.tmp')
            temporary.write_text(json.dumps(current))
            temporary.replace(ledger)
        return BytesIO(json.dumps(result).encode())

    def command(args, **kwargs):
        if operation == 'publish' and 'push' in args:
            return subprocess.CompletedProcess(args, 1, '', 'transport unavailable')
        return subprocess.run(args, **kwargs)

    with patch.object(urllib.request, 'urlopen', durable):
        try:
            getattr(Publisher(runner=command), operation)()
        except PublicationError:
            print('push unavailable before artifact upload', flush=True)
            return 3
    return 0


class Acceptance(unittest.TestCase):
    def test_P01_total_runner_loss_before_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            remote, lost, clean = [temp / name for name in ('remote.git', 'runner-lost', 'runner-clean')]
            git(temp, 'init', '--bare', '--initial-branch=main', str(remote))
            git(temp, 'clone', str(remote), str(lost))
            (lost / 'data').mkdir()
            original = '{"message_id":101,"text":"old","sha256":"old"}'
            pending = '{"message_id":102,"text":"version observed then deleted at origin","sha256":"new"}'
            source = lost / 'data/messages.jsonl'
            source.write_text(original + '\n')
            git(lost, 'add', 'data')
            git(lost, 'commit', '-m', 'seed')
            git(lost, 'push', 'origin', 'main')
            source.write_text(original + '\n' + pending + '\n')
            root = os.getcwd()
            env = dict(os.environ, PYTHONPATH=root, GOAL_ROI_DURABLE_REQUIRED='1',
                       GOAL_ROI_DURABLE_URL='https://continuity.example.invalid/journal',
                       GOAL_ROI_DURABLE_TOKEN='synthetic-only')

            def execute(where, operation):
                return subprocess.run([sys.executable, __file__, '--publication-child',
                                       str(temp / 'durable.json'), operation], cwd=where,
                                      env=dict(env, GOAL_ROI_PUBLICATION_DIR=str(where / 'spool')),
                                      capture_output=True, text=True, timeout=30)

            first = execute(lost, 'publish')
            self.assertEqual(first.returncode, 3, first.stderr)
            self.assertTrue((lost / 'spool/pending.bundle').exists())
            self.assertEqual(git(remote, 'show', 'main:data/messages.jsonl'), original)
            shutil.rmtree(lost)
            self.assertFalse(lost.exists())
            git(temp, 'clone', str(remote), str(clean))
            resumed = execute(clean, 'resume')
            actual = git(remote, 'show', 'main:data/messages.jsonl')
            print(f'P01 resume={resumed.returncode} published_rows={len(actual.splitlines())}')
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(actual.splitlines(), [original, pending])
            self.assertEqual(execute(clean, 'resume').returncode, 0)
            self.assertEqual(git(remote, 'rev-list', '--count', 'main'), '2')

    def test_T01_correction_never_changes_previous_report(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, SCANGOAL_DATA_DIR=folder):
            directory = Path(folder)
            text = ('💷 Betfair · 20 nov 2026 15:00\nAlpha — Beta · First Cup\n'
                    'X · Seleção @2.00 · $1K\nmarket share 60% · whole 1X2 market $8K\n'
                    'https://scangoal.com/match/fixture-a?lang=en')
            store.append_versions([{'message_id': 1, 'date_utc': stamp(-25), 'collected_at_utc': stamp(-24),
                                    'edit_date_utc': None, 'text': text}])
            binding = {'id': 123, 'fixture_id': 'fixture-a', 'event_date': stamp(0),
                       'home': 'Alpha', 'away': 'Beta', 'league': 'First Cup',
                       'link': 'https://highlightly.example.invalid/fixtures/123'}
            (directory / 'results_hl.json').write_text(json.dumps({'not_regular_time': {'fixture-a': binding}}))

            def fixture(corrected):
                goals = [{'type': 'Goal', 'time': '110', 'team': {'name': 'Alpha'}}]
                if not corrected:
                    goals += [{'type': 'Goal', 'time': '15', 'team': {'name': 'Alpha'}},
                              {'type': 'Goal', 'time': '25', 'team': {'name': 'Beta'}}]
                return {'homeTeam': {'name': 'Alpha'}, 'awayTeam': {'name': 'Beta'}, 'events': goals,
                        'state': {'description': 'Finished after extra time',
                                  'score': {'current': '1 - 0' if corrected else '2 - 1'}}}

            # Force the same provider ID to be consulted twice within the fixture's short timeline.
            with patch.object(details_hl, 'wanted', return_value=[('fixture-a', 123)]), \
                 patch.object(details_hl, 'MIN_INTERVAL_MIN', 0):
                details_hl.run(fetch=lambda *_: (fixture(False), 100), now=at(130), token='test', priority=())
                before = report.build(as_of=at(145))
                self.assertEqual((before['settled_bets'], before['recent_bets'][0]['ft']), (1, [1, 1]))
                details_hl.run(fetch=lambda *_: (fixture(True), 100), now=at(170), token='test', priority=())
            later = report.build(as_of=at(180))
            replay = report.build(as_of=at(145))
            print('T01 settled/FT before, replay, later:', [(r['settled_bets'], r['recent_bets'][0]['ft'])
                                                           for r in (before, replay, later)])
            self.assertEqual((later['recent_bets'][0]['match_id'], later['recent_bets'][0]['ft'],
                              later['recent_bets'][0]['w']), ('fixture-a', [0, 0], 1.0))
            self.assertEqual(replay, before)

    def test_R02_each_commitment_binds_its_own_score_and_return(self):
        base = {'match_id': 'fixture-a', 'teams': ['Alpha', 'Beta'], 'league': 'First Cup',
                'key': 'bet-1', 'type': 'BETFAIR', 'market': '1X2', 'period': 'regulation', 'selection': '1',
                'odds': 2.0, 'phase': 'pre', 'date_utc': stamp(-25), 'signal_seen_utc': stamp(-24),
                'kickoff_utc': stamp(0), 'w': None}
        original = opportunity_results.collected('telegram_message', '1', [1, 0], stamp(120), published=stamp(120), exact=True)
        correction = opportunity_results.collected('telegram_message', '1', [0, 1], stamp(150), published=stamp(150), exact=True)

        def emit(evidence, cut):
            row = opportunity_results.settle_research_bet(dict(base, _ft_observations=tuple(evidence)), cut)
            receipts = []

            def verify(directory, domain, content, *, as_of):
                receipts.append((domain, copy.deepcopy(content), scientific_receipts.digest(domain, content)))
                return None

            with patch.object(scientific_receipts, 'verify', side_effect=verify):
                output = brain.confirmation([row], at(-60), .05, as_of=cut, hypothesis='test')
            self.assertEqual(output['admitted'], 1)
            self.assertTrue(output['proof_problems'])
            self.assertIsNone(output['certified_at'])
            return [event for event in receipts if event[0] in ('certification-outcome', 'certification-settlement')]

        first, revised = emit([original], at(125)), emit([original, correction], at(160))
        print('R02 verification objects:', json.dumps({'first': first, 'revised': revised}, ensure_ascii=False))
        self.assertTrue(first)
        self.assertIn(first[0], revised, 'the exact original payload and digest must still verify')
        self.assertEqual(emit([original, correction], at(125)), first)
        corrected = next((event for event in revised if event[1].get('ft') == [0, 1]), None)
        self.assertIsNotNone(corrected)
        self.assertAlmostEqual(first[0][1]['profit'], .96)
        self.assertAlmostEqual(corrected[1]['profit'], -1)
        self.assertEqual(corrected[1]['available_utc'], stamp(150))
        self.assertIn(corrected[1].get('supersedes'), {first[0][2], first[0][1].get('id', 'missing')})
        self.assertIsNotNone(corrected[1].get('supersedes'))


if __name__ == '__main__':
    if '--publication-child' in sys.argv:
        sys.exit(publication_child())
    unittest.main(verbosity=2)

import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from scangoal import chronology
from scangoal.decision_audit_provenance import registration, signal_versions


def stamp(minute):
    return f'2026-11-23T14:{minute:02}:00Z'


def at(minute):
    return chronology.parse_utc(stamp(minute))


class SignalProvenance(unittest.TestCase):
    def test_exact_version_includes_buttons_and_ignores_later_observations(self):
        text = 'Original signal'
        first = {'message_id': 1, 'date_utc': stamp(1), 'collected_at_utc': stamp(2),
                 'edit_date_utc': None, 'text': text, 'buttons': [['Match', 'https://example.test/123']],
                 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'receipt_version': 1}
        later = dict(first, edit_date_utc=stamp(8), collected_at_utc=stamp(9), text='Updated signal')
        # A version published after the cut is ineligible even if its recorded collection is earlier.
        future_edit = dict(later, message_id=2, collected_at_utc=stamp(3))
        unseen = dict(first, message_id=3, collected_at_utc=stamp(9))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.jsonl'
            path.write_text(json.dumps(first) + '\n', encoding='utf-8')
            prefix = signal_versions(path, at(5))
            with path.open('a', encoding='utf-8') as journal:
                for row in (later, future_edit, unseen):
                    journal.write(json.dumps(row) + '\n')
            self.assertEqual(prefix, signal_versions(path, at(5)))
            self.assertEqual(signal_versions(path, at(12))[1], prefix[1])
            corrected_buttons = dict(first, buttons=[['Match', 'https://example.test/456']])
            path.write_text(json.dumps(corrected_buttons) + '\n', encoding='utf-8')
            alternate = signal_versions(path, at(5))[1]
        version = prefix[1]
        self.assertNotIn('text', version)
        self.assertNotIn('buttons', version)
        self.assertEqual(version['journal_line'], 1)
        self.assertEqual(version['text_sha256'], alternate['text_sha256'])
        self.assertNotEqual(version['record_sha256'], alternate['record_sha256'])
        self.assertTrue(version['text_sha256_matches_journal'])
        self.assertEqual(version['receipt_quality'], 'completed_page')


class RegistrationProvenance(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.git('init', '-q')
        (self.root / 'data').mkdir()
        self.path = self.root / 'data' / 'hypotheses.json'
        self.entry = {'born_utc': stamp(1), 'family': 'Open game', 'conditions': [['Minute', 'Second half']]}

    def git(self, *args, time=None):
        variables = dict(os.environ, GIT_AUTHOR_NAME='Audit test', GIT_AUTHOR_EMAIL='test@example.test',
                         GIT_COMMITTER_NAME='Audit test', GIT_COMMITTER_EMAIL='test@example.test')
        if time is not None:
            variables.update(GIT_AUTHOR_DATE=time, GIT_COMMITTER_DATE=time)
        return subprocess.run(['git', '-C', str(self.root), '-c', 'commit.gpgsign=false', *args], check=True,
                              capture_output=True, text=True, env=variables).stdout.strip()

    def commit(self, entries, minute):
        self.path.write_text(json.dumps(entries), encoding='utf-8')
        self.git('add', self.path)
        self.git('commit', '-qm', 'Registry snapshot', time=stamp(minute))
        return self.git('rev-parse', 'HEAD')

    def test_declaration_is_not_proof_and_altered_definition_uses_later_commit(self):
        self.commit({}, 0)
        first = self.commit({'candidate': self.entry}, 5)
        missing = registration(self.root, 'candidate', at(4))
        self.assertEqual(missing.flags, ('registration_not_observed_by_cutoff',))
        original = registration(self.root, 'candidate', at(6))
        self.assertEqual(original.declared, at(1))
        self.assertEqual(original.proven_at, at(5))
        self.assertEqual(original.effective, at(5))
        self.assertEqual(original.proof['commit'], first)
        self.assertEqual(original.proof['blob_oid'], self.git('rev-parse', f'{first}:data/hypotheses.json'))
        self.assertIn('registration_claim_precedes_git', original.flags)
        changed = dict(self.entry, conditions=[['Minute', 'First half']])
        second = self.commit({'candidate': changed}, 12)
        self.assertEqual(registration(self.root, 'candidate', at(6)), original)
        rewritten = registration(self.root, 'candidate', at(13))
        self.assertEqual(rewritten.effective, at(12))
        self.assertEqual(rewritten.entry, changed)
        self.assertEqual(rewritten.proof['commit'], second)
        self.assertIn('registration_definition_changed', rewritten.flags)
        self.assertEqual(registration(self.root, 'candidate', at(13), ref=first), original)
        self.commit({}, 14)
        self.assertEqual(registration(self.root, 'candidate', at(15)).flags, ('registration_removed',))

    def test_backdated_child_cannot_enter_visible_prefix(self):
        self.commit({'candidate': self.entry}, 5)
        before = registration(self.root, 'candidate', at(8))
        self.commit({'candidate': self.entry, 'future': self.entry}, 12)
        self.commit({'candidate': {}, 'future': self.entry}, 6)
        self.assertEqual(registration(self.root, 'candidate', at(8)), before)
        self.assertIsNone(registration(self.root, 'future', at(8)).effective)
        self.assertEqual(registration(self.root, 'candidate', at(20)), before)

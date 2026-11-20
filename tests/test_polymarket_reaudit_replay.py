"""Offline regression for the changed message versions found by the independent replay."""

from collections import Counter
import json
from pathlib import Path
import unittest

from scangoal.parser import parse


class PolymarketReauditReplay(unittest.TestCase):
    def test_changed_versions_keep_their_market_or_diagnostic_classification(self):
        # The examples preserve the quote and header. Team and competition names were replaced.
        path = Path(__file__).with_name('polymarket_reaudit.jsonl')
        with path.open(encoding='utf-8') as handle:
            fixtures = [json.loads(line) for line in handle]
        self.assertEqual(Counter(row['kind'] for row in fixtures),
                         {'explicit_scope': 8, 'unproven_scope': 25, 'live_diagnostic': 4})
        self.assertEqual(sum(row['original_difference'] for row in fixtures), 12)
        self.assertEqual(sum(row['first'] for row in fixtures if row['kind'] == 'unproven_scope'), 13)
        self.assertEqual(len({row['sha256_prefix'] for row in fixtures}), 37)
        for row in fixtures:
            with self.subTest(version=row['sha256_prefix']):
                quote = parse(row['message'])
                self.assertEqual({key: quote.get(key) for key in row['expected']}, row['expected'])

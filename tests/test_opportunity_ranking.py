"""Phase 3 opportunity ranking: no retrospective leak, no ranking with short samples."""
import unittest

from scangoal import opportunity_ranking

PROTOCOL = {
    "id": "polymarket_pre_over25_share_lt80_total_lt10000_v1",
    "declared_before_forward_samples_at_utc": "2026-11-19T15:16:22Z",
    "born_merge_sha": "b7c7ba6720e37ec7a38c28c49e2223101fc45f9a",
}
ACTIVATION = "2026-11-19T15:19:15.977169+00:00"
NOW = "2026-11-20T12:00:00+00:00"


def signal(mid="match-1", **changes):
    return {
        "type": "POLYMARKET", "phase": "pre",
        "market": "Asian Total", "selection": "Over 2.5",
        "match_id": mid, "message_id": mid,
        "teams": ["A", "B"], "league": "Example League",
        "bet": True, "share": 79, "total": 9900, "odds": 1.85,
        "date_utc": "2026-11-19T15:35:00+00:00",
        "collected_at_utc": "2026-11-19T15:36:00+00:00",
        "kickoff_utc": "2026-11-19T18:00:00+00:00",
        "had_ft_at_first_sight": False,
        "edited_at_first_sight": False, "burst": False, **changes,
    }


def result(ft=None, time="2026-11-19T20:05:00+00:00", *, conflict=False):
    return {"ft": ft if ft is not None else [2, 1], "known_utc": time, "conflict": conflict}


def run(signals, results=None, activation=ACTIVATION, now=NOW, protocol=PROTOCOL):
    return opportunity_ranking.build(
        signals, results if results is not None else {},
        protocol, activation_utc=activation, now=now,
    )


class OpportunityRankingTests(unittest.TestCase):
    def test_zero_games_and_unactivated_never_rank(self):
        without = run([signal()], {"match-1": result()}, activation=None)
        self.assertEqual(without["ranking"], [])
        self.assertEqual(without["status"], "not_activated")
        out = run([])
        self.assertEqual(out["ranking"][0]["evidence_status"], "sem_dados")
        self.assertIsNone(out["ranking"][0]["rank_exploratory"])
        self.assertIsNone(out["ranking"][0]["descriptive_two_se_score"])
        self.assertIsNone(out["baseline"]["roi_odds_minus_2pct"])

    def test_no_inference_or_alert_even_on_all_wins(self):
        records = [signal("g-%02d" % i) for i in range(30)]
        ft = {s["match_id"]: result() for s in records}
        out = run(records, ft)
        c = out["ranking"][0]
        self.assertEqual(c["forward_games"], 30)
        self.assertEqual((c["wins"], c["losses"], c["settled"]), (30, 0, 30))
        self.assertEqual(c["rank_exploratory"], 1)
        self.assertEqual(c["evidence_status"], "exploratorio_em_revisao")
        self.assertEqual(c["roi_odds_minus_2pct"], 0.813)
        self.assertEqual(c["descriptive_two_se_score"], 0.813)
        self.assertFalse(c["certified"])
        self.assertEqual(out["statistical_claim"], "none")
        self.assertEqual(out["certification"], "not_activated")
        self.assertFalse(out["automatic_alerts_changed"])

    def test_baseline_has_same_market_but_no_volume_restrictions(self):
        rows = [signal(), signal("higher-share", share=80),
                signal("higher-amount", total=10000),
                signal("wrong-market", selection="Under 2.5")]
        outcomes = {
            "match-1": result(),
            "higher-share": result(ft=[1, 0]),
            "higher-amount": result(ft=[1, 1]),
            "wrong-market": result(ft=[2, 1]),
        }
        out = run(rows, outcomes)
        candidate = out["ranking"][0]
        self.assertEqual((candidate["forward_games"], candidate["wins"]), (1, 1))
        self.assertEqual((out["baseline"]["forward_games"], out["baseline"]["settled"]), (3, 3))
        self.assertEqual((out["baseline"]["wins"], out["baseline"]["losses"]), (1, 2))
        self.assertEqual(candidate["roi_odds_minus_2pct"], 0.813)
        self.assertEqual(out["baseline"]["roi_odds_minus_2pct"], -0.3957)
        self.assertEqual(candidate["roi_minus_baseline_descriptive"], 1.2087)
        self.assertIsNone(candidate["rank_exploratory"])
        self.assertIn("overlaps", out["comparability"])

    def test_only_first_eligible_quote_in_each_match(self):
        early = signal()
        late = signal(odds=3.7, date_utc="2026-11-19T15:40:00+00:00",
                      collected_at_utc="2026-11-19T15:41:00+00:00")
        out = run([late, early, late, early], {"match-1": result()})
        self.assertEqual(out["ranking"][0]["forward_games"], 1)
        self.assertEqual(out["baseline"]["forward_games"], 1)
        self.assertEqual(out["ranking"][0]["roi_advertised"], 0.85)

    def test_historical_signal_and_after_kickoff_rejected_from_both(self):
        old = signal(date_utc="2026-11-19T15:10:00+00:00",
                     collected_at_utc="2026-11-19T15:11:00+00:00")
        too_late = signal("late", kickoff_utc="2026-11-19T15:45:00+00:00")
        out = run([old, too_late], {"match-1": result(), "late": result()})
        self.assertEqual(out["ranking"][0]["forward_games"], 0)
        self.assertEqual(out["baseline"]["forward_games"], 0)

    def test_contradictions_and_missing_results_not_hidden(self):
        rows = [signal(), signal("pending"), signal("contradiction")]
        results = {
            "match-1": result(ft=[2, 1]),
            "contradiction": result(conflict=True),
        }
        out = run(rows, results, now="2026-11-19T22:00:00+00:00")
        c = out["ranking"][0]
        self.assertEqual((c["settled"], c["pending"], c["quarantined"]), (1, 1, 1))
        self.assertEqual(c["forward_games"], 3)
        self.assertEqual(out["baseline"]["quarantined"], 1)
        after = run(rows, results, now="2026-11-23T12:00:00+00:00")
        c = after["ranking"][0]
        self.assertEqual(c["timeouts_counted_as_losses"], 1)
        self.assertEqual((c["wins"], c["losses"], c["quarantined"]), (1, 1, 1))

    def test_crosscheck_rejects_protocol_change_and_invalid_clocks(self):
        modified = dict(PROTOCOL, born_merge_sha="bogus")
        with self.assertRaises(ValueError):
            run([signal()], {}, protocol=modified)
        with self.assertRaises(ValueError):
            run([signal()], {}, now="2026-11-19T15:01:00+00:00")

    def test_exploratory_score_penalizes_variance(self):
        rows = [signal("game-%02d" % i) for i in range(30)]
        scores = {r["match_id"]: result(ft=[2, 1] if i % 2 == 0 else [1, 0])
                  for i, r in enumerate(rows)}
        out = run(rows, scores)
        c = out["ranking"][0]
        self.assertEqual((c["wins"], c["losses"], c["settled"]), (15, 15, 30))
        self.assertLess(c["descriptive_two_se_score"], c["roi_odds_minus_2pct"])
        self.assertFalse(c["certified"])

    def test_missing_score_older_than_three_days_is_a_loss(self):
        out = run([signal()], {}, now="2026-11-23T10:00:00+00:00")
        row = out["ranking"][0]
        self.assertEqual((row["settled"], row["losses"],
                          row["timeouts_counted_as_losses"]), (1, 1, 1))
        self.assertEqual(row["roi_odds_minus_2pct"], -1.0)


if __name__ == "__main__":
    unittest.main()

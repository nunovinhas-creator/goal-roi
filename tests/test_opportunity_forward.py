"""Offline adversarial tests for the predeclared PR #55 forward-only ROI ledger."""
import copy
import unittest

from scangoal import opportunity_forward as lab

PROTOCOL = {
    "id": "polymarket_pre_over25_share_lt80_total_lt10000_v1",
    "declared_before_forward_samples_at_utc": "2026-11-19T15:16:22Z",
    "born_merge_sha": "b7c7ba6720e37ec7a38c28c49e2223101fc45f9a",
}
ACTIVATION = "2026-11-19T15:20:00+00:00"
NOW = "2026-11-20T12:00:00+00:00"


def signal(mid="game-1", **changes):
    return {
        "type": "POLYMARKET",
        "phase": "pre",
        "market": "Asian Total",
        "selection": "Over 2.5",
        "match_id": mid,
        "teams": ["Alpha", "Beta"],
        "bet": True,
        "date_utc": "2026-11-19T15:35:00+00:00",
        "collected_at_utc": "2026-11-19T15:36:00+00:00",
        "kickoff_utc": "2026-11-19T18:00:00+00:00",
        "share": 79,
        "total": 9900,
        "odds": 1.85,
        "had_ft_at_first_sight": False,
        "edited_at_first_sight": False,
        "burst": False,
        **changes,
    }


def result(ft=(2, 1), known="2026-11-19T20:05:00+00:00", conflict=False):
    return {"ft": list(ft), "known_utc": known, "conflict": conflict}


def run(rows, results=None, *, now=NOW, activation=ACTIVATION):
    return lab.evaluate(rows, results or {}, PROTOCOL,
                        activation_utc=activation, now=now)


class ProspectiveOpportunityTests(unittest.TestCase):
    def test_only_new_original_future_eligible_signals(self):
        valid = signal()
        for change in (
            {"date_utc": "2026-11-19T15:15:00+00:00"},
            {"collected_at_utc": "2026-11-19T15:19:00+00:00"},
            {"type": "STAKE_LOAD"},
            {"phase": "live"},
            {"market": "1st Half - Asian Total"},
            {"selection": "Over 3.5"},
            {"selection": "Under 2.5"},
            {"share": 80},
            {"total": 10000},
            {"odds": 1.09},
            {"share": True},
            {"total": float("inf")},
            {"odds": float("nan")},
            {"had_ft_at_first_sight": True},
            {"edited_at_first_sight": True},
            {"burst": True},
            {"bet": False},
            {"match_id": None},
            {"kickoff_utc": "2026-11-19T15:45:00+00:00"},
        ):
            with self.subTest(change=change):
                self.assertEqual(run([signal(**change)], {"game-1": result()})["forward_games"], 0)
        self.assertEqual(run([valid], {"game-1": result()})["forward_games"], 1)

    def test_one_vote_per_game_uses_first_eligible_quote(self):
        earlier = signal(odds=1.85)
        later = signal(odds=4.5, date_utc="2026-11-19T15:40:00+00:00",
                       collected_at_utc="2026-11-19T15:41:00+00:00")
        out = run([later, earlier, earlier], {"game-1": result()})
        self.assertEqual(out["forward_games"], 1)
        self.assertEqual(out["settled"], 1)
        self.assertEqual(out["wins"], 1)
        self.assertEqual(out["roi_advertised"], 0.85)
        self.assertEqual(out["roi_odds_minus_2pct"], 0.813)

    def test_loss_and_reduced_odds_and_no_scientific_claims(self):
        a, b = signal(), signal("game-2", odds=2.0)
        o = run([a, b], {"game-1": result(), "game-2": result((1, 1))})
        self.assertEqual(o["settled"], 2)
        self.assertEqual((o["wins"], o["losses"]), (1, 1))
        self.assertEqual(o["roi_advertised"], -0.075)
        self.assertEqual(o["roi_odds_minus_2pct"], -0.0935)
        self.assertEqual(o["scientific_claim"], "none")
        self.assertEqual(o["validation"], "not_activated")
        self.assertFalse(o["alerts_changed"])

    def test_unknown_result_never_becomes_a_win(self):
        out = run([signal()], {}, now="2026-11-19T19:00:00+00:00")
        self.assertEqual((out["pending"], out["settled"]), (1, 0))
        self.assertIsNone(out["roi_advertised"])
        late = run([signal()], {}, now="2026-11-23T12:00:00+00:00")
        self.assertEqual((late["timeouts_counted_as_losses"], late["losses"]), (1, 1))
        self.assertEqual(late["roi_advertised"], -1.0)

    def test_known_before_kickoff_does_not_rewrite_history(self):
        o = run([signal()], {"game-1": result(known="2026-11-19T17:00:00+00:00")})
        self.assertEqual(o["wins"], 0)
        self.assertEqual(o["pending"], 1)

    def test_conflicting_scores_are_quarantined_not_promoted(self):
        o = run([signal()], {"game-1": result(conflict=True)})
        self.assertEqual((o["forward_games"], o["quarantined"], o["settled"]), (1, 1, 0))
        self.assertIsNone(o["roi_advertised"])

    def test_activated_state_required_and_cutoff_is_later_of_two(self):
        o = run([signal()], {"game-1": result()}, activation=None)
        self.assertEqual(o["status"], "not_activated")
        self.assertEqual(o["forward_games"], 0)
        o = run([signal()], {"game-1": result()},
                activation="2026-11-19T15:40:00+00:00")
        self.assertEqual(o["forward_games"], 0)

    def test_unparseable_or_untrusted_clock_rejected(self):
        p = copy.deepcopy(PROTOCOL)
        p["declared_before_forward_samples_at_utc"] = "yesterday"
        with self.assertRaises(ValueError):
            lab.evaluate([signal()], {}, p, activation_utc=ACTIVATION, now=NOW)
        with self.assertRaises(ValueError):
            run([signal()], now="2020-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

"""Offline adversarial tests for the predeclared PR #55 forward-only ROI ledger."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

from scripts.opportunity_forward_audit import sourced_results

from scangoal import chronology, opportunity_ranking, opportunity_results, report
from scangoal import opportunity_forward as lab

PROTOCOL = json.loads((Path(__file__).resolve().parents[1] /
                       "audits/opportunity-lab/polymarket-over25-v1.json").read_text())
ACTIVATION = "2026-11-19T15:20:00+00:00"
NOW = "2026-11-20T12:00:00+00:00"


def signal(mid="game-1", **changes):
    return {
        "type": "POLYMARKET",
        "phase": "pre",
        "market": "Asian Total", "period": "regulation", "market_scope": "match_goals",
        "selection": "Over 2.5",
        "match_id": mid,
        "teams": [mid + " home", mid + " away"],
        "league": "Example League",
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
        a, b = signal(), signal("game-2", odds=2.0, teams=["Gamma", "Delta"])
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



class ResultSourceTests(unittest.TestCase):
    MATCH = "game-1"
    KO = "2026-11-19T18:00:00+00:00"

    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(folder)
        self.enterContext(patch.dict(os.environ, {"SCANGOAL_DATA_DIR": folder}))

    def write_provider(self, name, score, stamp, **extra):
        observed = chronology.parse_utc(stamp)
        available = (observed + timedelta(minutes=5)).isoformat() if observed else None
        row = {"ft": score, "period": "regulation", "observed_at": stamp, "available_at": available, **extra}
        (self.directory / ("results_" + name + ".json")).write_text(
            json.dumps({"ft_versions": {self.MATCH: [row]}}), encoding="utf-8",
        )

    def telegram(self, score, stamp):
        return opportunity_results.collected("telegram", 42, score, stamp, published=stamp)

    def assess(self, results, *, now=NOW, messages=None):
        rows = [signal()] if messages is None else messages
        audit = run(rows, results, now=now)
        ranking = opportunity_ranking.build(rows, results, PROTOCOL, activation_utc=ACTIVATION, now=now)
        for key in ("settled", "wins", "losses", "pending", "quarantined", "roi_odds_minus_2pct"):
            self.assertEqual(audit[key], ranking["ranking"][0][key], key)
        return audit, ranking

    def test_source_and_observation_margin_are_preserved(self):
        seen = "2026-11-19T20:00:00.125+00:00"
        self.write_provider("hl", [2, 1], seen)
        evidence = sourced_results({self.MATCH: [self.telegram([2, 1], seen)]}, self.directory)
        self.assertEqual({o.source for o in evidence[self.MATCH]}, {"telegram", "highlightly"})
        for row in evidence[self.MATCH]:
            self.assertEqual(row.score, (2, 1))
            self.assertEqual(row.period, "regulation")
            self.assertEqual(row.observed_at, chronology.parse_utc(seen))
            self.assertEqual(row.available_at, chronology.parse_utc("2026-11-19T20:05:00.125+00:00"))
        self.assertEqual(self.assess(evidence, now="2026-11-19T20:05:00.124Z")[0]["pending"], 1)
        self.assertEqual(self.assess(evidence, now="2026-11-19T20:05:00.125Z")[0]["wins"], 1)

    def test_missing_proven_timestamp_cannot_borrow_one_from_invalid_score(self):
        self.write_provider("hl", [-1, 1], "2026-11-19T20:00:00Z")
        evidence = sourced_results({self.MATCH: [self.telegram([2, 1], None)]}, self.directory)
        self.assertIsNone(evidence[self.MATCH][0].available_at)
        self.assertEqual(self.assess(evidence)[0]["pending"], 1)

    def test_invalid_scores_and_non_regulation_results_are_not_evidence(self):
        for score in ([-1, 4], [True, 2], [2.0, 1], ["2", 1], [2, 1, 0], None):
            with self.subTest(score=score):
                self.write_provider("hl", score, "2026-11-19T20:00:00Z")
                self.assertEqual(self.assess(sourced_results({}, self.directory))[0]["pending"], 1)
        self.write_provider("hl", [2, 1], "2026-11-19T20:00:00Z", period="extra_time")
        self.assertEqual(self.assess(sourced_results({}, self.directory))[0]["pending"], 1)

    def test_ft_at_kickoff_or_before_possible_full_time_is_rejected(self):
        for observed in (self.KO, "2026-11-19T19:24:59Z"):
            with self.subTest(observed=observed):
                self.write_provider("hl", [2, 1], observed)
                self.assertEqual(self.assess(sourced_results({}, self.directory))[0]["pending"], 1)
                self.assertEqual(self.assess({self.MATCH: result(known=observed)})[0]["pending"], 1)

    def test_later_contradiction_does_not_change_a_previous_cutoff(self):
        observed = {self.MATCH: [self.telegram([2, 1], "2026-11-19T20:00:00Z")]}
        earlier = self.assess(sourced_results(observed, self.directory), now="2026-11-19T20:30:00Z")
        self.assertEqual(earlier[0]["wins"], 1)
        self.write_provider("bsd", [0, 1], "2026-11-19T21:00:00Z")
        evidence = sourced_results(observed, self.directory)
        self.assertEqual(earlier, self.assess(evidence, now="2026-11-19T20:30:00Z"))
        self.assertEqual(self.assess(evidence, now="2026-11-19T21:05:00Z")[0]["quarantined"], 1)

    def test_bot_correction_preserves_previous_cutoff_and_legacy_report(self):
        text = "\n".join((
            "📈 🔷 Polymarket · 19 nov 2026 21:00", "A — B · Example",
            "Over 2.5 @1.85 · $9.9K", "🧾 2 trades · biggest $4.4K · market share 79%",
            "https://scangoal.com/match/game-1?lang=en",
        ))
        path = self.directory / "messages.jsonl"
        original = {"message_id": 42, "date_utc": "2026-11-19T15:35:00Z",
                    "collected_at_utc": "2026-11-19T15:36:00Z", "text": text}
        for index, (marker, stamp) in enumerate(((None, None), ("1:1", "2026-11-19T20:00:00Z"),
                                                ("2:1", "2026-11-19T21:00:00Z"))):
            row = original if marker is None else dict(
                original, text=text + "\n🏁 FT " + marker,
                edit_date_utc=stamp, collected_at_utc=stamp,
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            if index == 1:
                observed = {}
                messages, *_ = report.load(result_observations=observed)
                before = self.assess(sourced_results(observed, self.directory),
                                     now="2026-11-19T20:30:00Z", messages=messages)
                self.assertEqual(before[0]["losses"], 1)
        observed = {}
        messages, ft, *rest = report.load(result_observations=observed)
        self.assertEqual((messages, ft, *rest), report.load())
        self.assertEqual(ft, {self.MATCH: [2, 1]})
        self.assertEqual([o.score for o in observed[self.MATCH]], [(1, 1), (2, 1)])
        evidence = sourced_results(observed, self.directory)
        self.assertEqual(before, self.assess(evidence, now="2026-11-19T20:30:00Z", messages=messages))
        self.assertEqual(self.assess(evidence, now="2026-11-19T21:05:00Z", messages=messages)[0]["wins"], 1)

    def test_late_read_of_older_edit_does_not_rollback_newer_marker(self):
        latest = self.telegram([2, 1], "2026-11-19T21:00:00Z")
        late_old = opportunity_results.collected(
            "telegram", 42, [1, 1], "2026-11-19T21:30:00Z", published="2026-11-19T20:00:00Z",
        )
        evidence = {self.MATCH: (latest, late_old)}
        self.assertEqual(self.assess(evidence)[0]["wins"], 1)

    def test_two_conflicting_markers_for_same_version_are_quarantined(self):
        stamp = "2026-11-19T21:00:00Z"
        rows = (self.telegram([2, 1], stamp), self.telegram([1, 1], stamp))
        self.assertEqual(self.assess({self.MATCH: rows})[0]["quarantined"], 1)
        self.assertEqual(self.assess({self.MATCH: rows[::-1]})[0]["quarantined"], 1)

    def test_external_correction_preserves_published_history_for_all_consumers(self):
        text = "\n".join((
            "📈 🔷 Polymarket · 19 nov 2026 21:00", "A — B · Example",
            "Over 2.5 @1.85 · $9.9K", "🧾 2 trades · market share 79%",
            "https://scangoal.com/match/game-1?lang=en",
        ))
        row = {"message_id": 42, "date_utc": "2026-11-19T15:35:00Z",
               "collected_at_utc": "2026-11-19T15:36:00Z", "text": text}
        (self.directory / "messages.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        path = self.directory / "results_hl.json"
        info = {"bsd_id": 17, "home": "A", "away": "B", "event_date": "2026-11-19T18:00+00:00"}
        state = {"results": {}}
        cutoff = chronology.parse_utc("2026-11-19T21:00:00Z")
        opportunity_results.remember_provider_results(state, {self.MATCH: dict(info, ft=[2, 1])},
                                                     chronology.parse_utc("2026-11-19T20:00:00Z"))
        path.write_text(json.dumps(state), encoding="utf-8")
        before = self.assess(sourced_results({}, self.directory), now=cutoff)
        from scangoal.results_bsd import capture_result_versions, save_result_state
        capture_result_versions(state, {}, [
            {"id": 17, "status": "finished", "home_score": 1, "away_score": 1, "home_team": "A",
             "away_team": "B", "event_date": "2026-11-19T18:00:00Z"},
        ], chronology.parse_utc("2026-11-19T22:00:00Z"))
        save_result_state(path, state)
        self.assertEqual(state["results"][self.MATCH]["ft"], [1, 1])
        self.assertEqual(state["first_results"][self.MATCH]["ft"], [2, 1])
        self.assertEqual([r["ft"] for r in state["ft_versions"][self.MATCH]], [[2, 1], [1, 1]])
        updated = sourced_results({}, self.directory)
        self.assertEqual(before, self.assess(updated, now=cutoff))
        self.assertEqual(before[0]["wins"], 1)
        self.assertEqual(self.assess(updated, now="2026-11-19T22:01:00Z")[0]["losses"], 1)
        report.build(now=cutoff)
        self.assertEqual(report.LAST["research_signals"][0]["ft"], [2, 1])
        report.build(now=chronology.parse_utc("2026-11-19T22:01:00Z"))
        self.assertEqual(report.LAST["research_signals"][0]["ft"], [1, 1])
        self.assertEqual(report.LAST["signals"][0]["ft"], [1, 1])

    def test_current_only_external_result_cannot_backfill_history(self):
        (self.directory / "results_hl.json").write_text(json.dumps(
            {"results": {self.MATCH: {"ft": [1, 1], "matched_at_utc": "2026-11-19T20:00:00Z"}}}
        ), encoding="utf-8")
        self.assertEqual(self.assess(sourced_results({}, self.directory), now="2026-11-19T21:00:00Z")[0]["pending"], 1)

    def test_legacy_conflict_requires_cutoff_evidence(self):
        for known in (None, "2026-11-21T20:00:00Z"):
            with self.subTest(known=known):
                data = {self.MATCH: result(known=known, conflict=True)}
                self.assertEqual(self.assess(data)[0]["pending"], 1)


if __name__ == "__main__":
    unittest.main()

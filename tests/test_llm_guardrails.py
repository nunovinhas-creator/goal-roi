"""Offline regressions for LLM response validation and quota accounting."""

import json
import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import llm, report
from test_chronological_validity import T0, sig


ENV = {"CLOUDFLARE_ACCOUNT_ID": "a" * 32, "MASTER_BRAIN_LLM_API_KEY": "fixture",
       "GOAL_ROI_LLM_FREE_CONFIRMED": "true"}
DAY = T0 + timedelta(hours=10)
FAMILY = "Jogo aberto"
VOCAB = {FAMILY: {"Liga": {"Example": 8, "Other": 8},
                  "Minuto": {"31 a 60": 8}, "Faixa de odd": {"odd 2,00 a 3,00": 8}}}


class ResponseValidation(unittest.TestCase):
    def test_malformed_items_are_counted_and_do_not_drop_the_valid_item(self):
        payload = {"hypotheses": [
            {"family": [], "conditions": [["Liga", "Example"]]},
            {"family": FAMILY, "conditions": [["Liga", "Example", "extra"]]},
            {"family": FAMILY, "conditions": [["Liga", "Example"]], "reason": "Lucro comprovado"},
            {"family": FAMILY, "conditions": [["Minuto", "31 a 60"]], "reason": ["wrong type"]},
        ]}
        reasons = {}
        accepted, count = llm.accept(json.dumps(payload), VOCAB, {}, reasons=reasons)
        self.assertEqual((len(accepted), count, reasons), (1, 3, {"malformed": 3}))
        self.assertEqual(accepted[0][1]["reason"], "")
        self.assertEqual(llm.accept('{"hypotheses": {}}', VOCAB, {}, reasons=reasons), ([], 1))
        self.assertEqual(reasons["malformed_response"], 1)

    def test_refinement_must_keep_a_visible_parent_and_add_exactly_one_condition(self):
        parent = FAMILY + " | Liga = Example"
        conditions = [["Liga", "Example"], ["Minuto", "31 a 60"]]
        registry = {parent: {"family": FAMILY, "conditions": conditions[:1]}}
        proposal = {"family": FAMILY, "conditions": conditions, "parent_id": parent}
        malformed = [
            dict(proposal, parent_id="unlisted"),
            dict(proposal, conditions=[["Liga", "Other"], conditions[1]]),
            dict(proposal, conditions=[*conditions, ["Faixa de odd", "odd 2,00 a 3,00"]]),
        ]
        reasons = {}
        accepted, count = llm.accept(json.dumps({"hypotheses": [proposal, *malformed]}), VOCAB,
                                     registry, mode="refinar", parents={parent}, reasons=reasons)
        self.assertEqual((len(accepted), count), (1, 3))
        self.assertEqual(accepted[0][1]["parent_id"], parent)
        self.assertEqual(reasons, {"invalid_parent": 3})

    def test_model_text_cannot_supply_numbers_or_certification_in_notes(self):
        items = [{"id": "h", "note": "Lucro de 99%"}, {"id": "h", "note": "Estratégia validada"},
                 {"id": ["h"], "note": "inválida"}, {"id": "h", "note": "Amostra curta"}]
        reasons = {}
        notes = llm.accept_notes(json.dumps({"notes": items}), {"h"}, reasons)
        self.assertEqual(notes, [{"id": "h", "note": "Amostra curta"}])
        self.assertEqual(reasons, {"invalid_note": 2, "malformed": 1})


class BudgetAccounting(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / "llm_state.json"
        self.enterContext(patch.dict(os.environ, {"SCANGOAL_DATA_DIR": folder}))
        def build(*, as_of, capture):
            capture['research_signals'] = [sig('fixture', None, 2)]
            return {"brain": {"top": []}}
        self.enterContext(patch.object(report, "build", side_effect=build))
        self.enterContext(patch.object(llm, "MODELS", list(llm.PRICES)))

    def read(self):
        return json.loads(self.path.read_text())

    def test_fallback_reserves_before_each_attempt_and_charges_invalid_response(self):
        attempts = []

        def call(_account, _key, model, _prompt):
            state = self.read()
            self.assertEqual(state["calls_today"], len(attempts) + 1)
            self.assertEqual(state["last_outcome"], "attempt_reserved")
            self.assertGreater(state["neurons_today"], 0)
            attempts.append((model, state["neurons_today"]))
            if len(attempts) == 1:
                raise llm.LLMError("HTTP 500")
            return "invalid JSON", {"prompt_tokens": 200, "completion_tokens": 10}

        state = llm.run(call, DAY, ENV)
        self.assertEqual(state["calls_today"], 2)
        self.assertEqual(state["last_rejection_reasons"], {"malformed_response": 1})
        self.assertEqual(state["last_outcome"], "invalid_response")
        first_reservation = attempts[0][1]
        self.assertGreater(state["neurons_today"], first_reservation)
        self.assertLess(state["neurons_today"], attempts[1][1])

    def test_crash_keeps_reservation_and_restart_respects_it(self):
        def crash(*_):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            llm.run(crash, DAY, ENV)
        state = self.read()
        self.assertEqual(state["calls_today"], 1)
        self.assertGreater(state["neurons_today"], 0)
        with patch.object(llm, "DAILY_NEURONS", state["neurons_today"] + 1):
            def unexpected(*_):
                self.fail("Attempted a call without sufficient funds")
            llm.run(unexpected, DAY + timedelta(hours=2), ENV)
        self.assertEqual(self.read()["calls_today"], 1)
        self.assertEqual(self.read()["neurons_today"], state["neurons_today"])

    def test_limit_and_utc_day_rollover(self):
        self.path.write_text(json.dumps({"day": DAY.date().isoformat(), "neurons_today": 7999,
                                         "calls_today": 2, "last_run_utc": None}))
        calls = []

        def call(*args):
            calls.append(args[2])
            return '{"hypotheses":[]}', {"prompt_tokens": 200, "completion_tokens": 10}

        llm.run(call, DAY, ENV)
        self.assertEqual(calls, [])
        state = llm.run(call, DAY + timedelta(days=1), ENV)
        self.assertEqual(len(calls), 1)
        self.assertEqual((state["calls_today"], state["day"]), (1, "2026-11-21"))
        self.assertLess(state["neurons_today"], 8000)

    def test_provider_quota_rejection_stops_fallback(self):
        attempts = []

        def call(*args):
            attempts.append(args[2])
            raise llm.LLMError("HTTP 429")

        state = llm.run(call, DAY, ENV)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(state["calls_today"], 1)
        self.assertGreater(state["neurons_today"], 0)

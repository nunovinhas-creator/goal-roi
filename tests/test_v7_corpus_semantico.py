"""Corpus semântico V7 (audits/v7/semantics): zero admissões, regras com fonte oficial e totais iguais à sonda."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from audits.v7.semantics import corpus as C

ROOT = Path(__file__).resolve().parents[1]
SEM = ROOT / "audits/v7/semantics"
CUT = "e3a0760813f8b2ba4f4953a84dde0a697a117a10"
KEYS = {"ref", "familia", "fase", "mercado", "rotulo", "equipas", "classe", "regras", "decisao", "falta", "admitido"}
DECISIONS = {"condicional_revisao_humana", "quarentena_permanente", "bloqueado_sem_fonte_oficial"}


def rows():
    return [json.loads(line) for line in (SEM / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]


class Corpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = rows()
        cls.rules = json.loads((SEM / "regras.json").read_text(encoding="utf-8"))

    def test_zero_admissions(self):
        self.assertTrue(self.rows)
        self.assertFalse(any(r["admitido"] for r in self.rows))
        self.assertLessEqual({r["decisao"] for r in self.rows}, DECISIONS)

    def test_no_raw_text_or_ids(self):
        for r in self.rows:
            self.assertEqual(set(r), KEYS)
            self.assertRegex(r["ref"], r"^[0-9a-f]{16}$")

    def test_every_cited_rule_exists_and_has_official_source(self):
        for r in self.rows:
            for rid in r["regras"]:
                rule = self.rules["regras"][rid]
                self.assertEqual(self.rules["fontes"][rule["fonte"]]["estado"], "oficial", rid)

    def test_conditional_only_with_official_rules(self):
        for r in self.rows:
            if r["decisao"] == "condicional_revisao_humana":
                self.assertTrue(r["regras"], r["classe"])
                self.assertEqual(r["familia"], "Kalshi")

    def test_sources_without_official_rules_are_never_conditional(self):
        for r in self.rows:
            if r["familia"] in ("Polymarket", "Stake"):
                self.assertNotEqual(r["decisao"], "condicional_revisao_humana")
                self.assertEqual(r["regras"], [])

    def test_totals_match_the_audited_probe(self):
        probe = json.loads((ROOT / "audits/v7/parser/sonda-semantica.json").read_text(encoding="utf-8"))["classes"]
        census = json.loads((ROOT / "audits/v7/parser/auditoria-parser.json").read_text(encoding="utf-8"))
        count = lambda fam, market=None: sum(r["familia"] == fam and (market is None or r["mercado"] == market) for r in self.rows)
        self.assertEqual(count("Kalshi", "selecao_sem_vinculo_literal"), sum(probe["kalshi_sem_vinculo"].values()))
        self.assertEqual(count("Kalshi", "contrato_negado"), sum(probe["kalshi_negado"].values()))
        self.assertEqual(count("Polymarket"), 144)
        self.assertEqual(count("Stake"), sum(census["markets_without_legacy_ft_settlement"]["Stake"].values()))

    def test_permanent_quarantine_classes(self):
        for r in self.rows:
            if r["classe"] in ("codigo_12_dupla_hipotese", "nome_sem_lado", "spread_contraditorio", "relogio_ausente"):
                self.assertEqual(r["decisao"], "quarentena_permanente", r["classe"])


class Classifier(unittest.TestCase):
    def test_stake_classes(self):
        cases = {"1x2 (2up)": "regra_promocional_2up", "1st Half - Asian Total": "parte_do_jogo", "Total Corners": "estatistica_nao_golo",
                 "2nd Team to Score": "sequencia_golos", "Arsenal Total Goals": "total_equipa", "Correct Score": "resultado_exato",
                 "Asian Handicap": "mercado_base_nao_liquidado", "1x2 & Total": "combinado", "Which Team Will Win The Final": "apuramento_ou_torneio"}
        for market, klass in cases.items():
            self.assertEqual(C.stake_class(market)[0], klass, market)

    def test_kalshi_mapping_is_total(self):
        produced = {"alias_vencedor", "alias_mais_handicap", "alias_mais_handicap_sinal_unicode", "alias_mais_handicap_sem_lado",
                    "alias_mais_handicap_sinal_unicode_sem_lado", "nome_sem_lado", "codigo_12_dupla_hipotese", "nao_equipa_alias",
                    "nao_equipa_literal", "nao_equipa_sem_lado", "nao_resultado_exato", "relogio_ausente"}
        self.assertEqual(set(C.KALSHI), produced)

    def test_kalshi_labels(self):
        teams = ["Bayern Munchen", "Arsenal"]
        self.assertEqual(C.sp.kalshi_semantics("Bayern Munich −1.5", teams)[0], "alias_mais_handicap_sinal_unicode")
        self.assertEqual(C.sp.kalshi_semantics("12", teams)[0], "codigo_12_dupla_hipotese")
        self.assertEqual(C.sp.negated("not Bayern Munich", teams)[0], "nao_equipa_alias")
        self.assertEqual(C.sp.negated("not 2:0", teams)[0], "nao_resultado_exato")


@unittest.skipUnless(subprocess.run(["git", "cat-file", "-e", CUT + ":data/messages.jsonl"], cwd=ROOT, capture_output=True).returncode == 0,
                     "corte e3a0760 indisponível neste clone")
class Regeneration(unittest.TestCase):
    def test_byte_identical_from_frozen_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "m.jsonl"
            archive.write_bytes(subprocess.run(["git", "show", CUT + ":data/messages.jsonl"], cwd=ROOT, capture_output=True, check=True).stdout)
            out = Path(tmp) / "c.jsonl"
            subprocess.run(["python3", "audits/v7/semantics/corpus.py", "--archive", str(archive), "--out", str(out)], cwd=ROOT, check=True,
                           capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(out.read_bytes(), (SEM / "corpus.jsonl").read_bytes())


@unittest.skipUnless(os.environ.get("V7_KALSHI_PDF_DIR") and shutil.which("pdftotext"), "PDFs oficiais não indicados (V7_KALSHI_PDF_DIR)")
class OfficialQuotes(unittest.TestCase):
    def test_hashes_and_quotes(self):
        from audits.v7.semantics import fetch_sources
        res = fetch_sources.check(os.environ["V7_KALSHI_PDF_DIR"], offline=True)
        self.assertTrue(all(v in ("igual", "fonte_oficial_nao_obtida") for v in res["fontes"].values()), res["fontes"])
        self.assertTrue(all(res["citacoes"].values()), res["citacoes"])


if __name__ == "__main__":
    unittest.main()

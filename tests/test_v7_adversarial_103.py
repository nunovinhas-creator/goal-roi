"""Auditoria adversarial independente do publicador de snapshots da PR #103 (audits/v7/snapshots/publisher.py).

Não altera o protótipo. Cada invariante que o protótipo devia garantir e não garante está marcada com
@unittest.expectedFailure: o teste documenta o defeito hoje e passa a "unexpected success" quando for corrigido
(nesse momento retira-se o decorador). Os restantes testes fixam invariantes que hoje se verificam.

    python3 -m pytest -q tests/test_v7_adversarial_103.py
"""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from audits.v7.snapshots import publisher as P
from tests.test_v7_snapshot_publisher import Fixture

# Lista de caminhos publicáveis da PR #98 (scangoal/publication.py em 33b469e4): o protótipo terá de caber nela.
PR98_SNAPSHOT_FILE = re.compile(r'docs/v6/snapshots/dashboard-(manifest|summary|signals|performance|science|operations|learning)\.json')


def interrupted_build(fx, after_renames):
    """Publica e interrompe (OSError) no rename número `after_renames` + 1, como uma perda de energia a meio."""
    original, calls = os.replace, {"n": 0}

    def interrupt(src, dst):
        calls["n"] += 1
        if calls["n"] > after_renames:
            raise OSError("interrupção simulada")
        return original(src, dst)

    with patch.object(P.os, "replace", side_effect=interrupt):
        try:
            fx.build()
        except OSError:
            return True
    return False


class AdversarialPublisher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)
        self.fx.build()                                   # rev 1 em a/
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)
        self.fx.build()                                   # rev 2 em b/ ; ponteiro: current=b, previous=a
        self.pointer = P.read_pointer(self.fx.out)
        self.fx.revise("2026-11-26T10:20:00+00:00", 3)

    def tearDown(self):
        self.tmp.cleanup()

    # A1 — a publicação reescreve a ranhura que o ponteiro ainda anuncia como "previous".
    @unittest.expectedFailure
    def test_a1_previous_advertised_by_pointer_stays_intact_after_interruption(self):
        self.assertTrue(interrupted_build(self.fx, 3))
        pointer = P.read_pointer(self.fx.out)
        self.assertEqual(pointer, self.pointer)                       # o ponteiro não mudou...
        P.verify_revision(pointer["previous"], self.fx.out)           # ...mas "previous" já não é íntegra

    # A2 — com "current" corrompida, a ranhura escolhida é a da única revisão íntegra: uma interrupção deixa zero.
    @unittest.expectedFailure
    def test_a2_some_verified_revision_survives_corruption_plus_interruption(self):
        (self.fx.out / "b" / "dashboard-science.json").write_text("{}")
        self.assertEqual(P.read_verified(self.fx.out)["slot"], "a")  # antes: recuo funciona
        self.assertTrue(interrupted_build(self.fx, 2))
        self.assertIsNotNone(P.read_verified(self.fx.out))           # depois: nenhuma revisão íntegra

    # A3 — leitor que obteve o ponteiro antes de duas publicações (A-B-A) não consegue ler nada.
    @unittest.expectedFailure
    def test_a3_reader_holding_pointer_across_two_publications_still_verifies(self):
        held = P.read_pointer(self.fx.out)
        self.fx.build()                                                # rev 3 em a/
        self.fx.revise("2026-11-26T10:30:00+00:00", 4)
        self.fx.build()                                                # rev 4 em b/
        ok = []
        for key in ("current", "previous"):
            try:
                P.verify_revision(held[key], self.fx.out)
                ok.append(key)
            except P.SnapshotError:
                pass
        self.assertTrue(ok, "nem current nem previous do ponteiro retido são legíveis")

    # A3b — com uma só publicação intermédia, current continua legível (limite exato do modelo de 2 ranhuras).
    def test_a3b_reader_survives_one_intermediate_publication(self):
        held = P.read_pointer(self.fx.out)
        self.fx.build()
        P.verify_revision(held["current"], self.fx.out)
        with self.assertRaises(P.SnapshotError):
            P.verify_revision(held["previous"], self.fx.out)          # previous morre já na primeira

    # A4 — o ponteiro tem de ser uma única linha: é isso que faz o rebase de publication.py entrar em conflito
    # (e abortar) quando dois escritores publicam a partir da mesma base. Com JSON indentado, hunks distintos
    # podiam fundir-se e misturar ranhuras sem conflito.
    def test_a4_pointer_is_single_line_so_concurrent_rebases_conflict(self):
        text = (self.fx.out / P.POINTER).read_text(encoding="utf-8")
        self.assertNotIn("\n", text.rstrip("\n"))

    # A5 — o layout do protótipo não cabe na lista de caminhos publicáveis da PR #98, e deixa .lock na pasta servida.
    @unittest.expectedFailure
    def test_a5_output_layout_is_publishable_by_pr98_allowlist(self):
        names = sorted(str(p.relative_to(self.fx.out)) for p in self.fx.out.rglob("*") if p.is_file())
        bad = [n for n in names if not PR98_SNAPSHOT_FILE.fullmatch("docs/v6/snapshots/" + n)]
        self.assertEqual(bad, [])

    # A6 — o recuo para "previous" devolve dados de outras entradas sem o declarar ao leitor.
    def test_a6_previous_fallback_is_distinguishable_from_current(self):
        (self.fx.out / "b" / "dashboard-learning.json").write_text("{}")
        rev = P.read_verified(self.fx.out)
        self.assertNotEqual(rev["inputs"], self.pointer["current"]["inputs"],
                            "o chamador tem de comparar inputs/report_generated_at_utc para assinalar atraso")


if __name__ == "__main__":
    unittest.main()

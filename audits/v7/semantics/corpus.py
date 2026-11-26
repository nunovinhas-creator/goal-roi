"""Corpus semântico etiquetado das rejeições Kalshi, Polymarket e Stake (V7, só diagnóstico, zero admissões).

    PYTHONPATH=. python3 audits/v7/semantics/corpus.py --archive <messages.jsonl> [--out audits/v7/semantics/corpus.jsonl]

Para cada primeira mensagem rejeitada, escreve uma linha com: referência opaca (SHA-256 do message_id, 16 hex),
família, fase, mercado, rótulo, equipas, classe, regras oficiais aplicáveis (regras.json), decisão e
"admitido": false. Não escreve texto bruto nem IDs. Não altera parser, diário, protocolo nem registo prospetivo.

Decisões possíveis (nenhuma admite):
- condicional_revisao_humana: há regra oficial; a equivalência com o liquidante depende de factos que a mensagem
  não traz (âmbito do contrato, formato da competição, alias revisto).
- quarentena_permanente: a mensagem é ambígua por construção; nenhuma regra externa a resolve.
- bloqueado_sem_fonte_oficial: não há regra oficial obtida para a família ou o contrato.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from scangoal import parser, store
from audits.v7.parser import parser_audit as pa
from audits.v7.parser import semantic_probe as sp

HERE = Path(__file__).resolve().parent
GW = ["K-GW-SCOPE", "K-GW-DRAW", "K-GW-REG", "K-GW-REG-DRAW", "K-GW-FULL"]
SP = ["K-SP-PAYOUT", "K-SP-DEFAULT", "K-SP-TIE", "K-ABANDON-90"]

# Classe -> (regras, decisão, condição que falta). Única fonte do mapeamento; os testes verificam que é total.
KALSHI = {
    "alias_vencedor": (GW, "condicional_revisao_humana", "âmbito do contrato (regulamentar vs. full match) ou competição sem prolongamento; alias revisto"),
    "alias_mais_handicap": (SP, "condicional_revisao_humana", "período do spread ou competição sem prolongamento; alias revisto; sinal '+' não tem contrato"),
    "alias_mais_handicap_sinal_unicode": (SP, "condicional_revisao_humana", "como alias_mais_handicap; U+2212 equivale a '-' só por convenção do bot, a confirmar"),
    "alias_mais_handicap_sem_lado": (SP, "quarentena_permanente", "equipa não identificável"),
    "alias_mais_handicap_sinal_unicode_sem_lado": (SP, "quarentena_permanente", "equipa não identificável"),
    "nome_sem_lado": (GW, "quarentena_permanente", "equipa não identificável"),
    "codigo_12_dupla_hipotese": ([], "quarentena_permanente", "nenhum contrato Kalshi com o rótulo '12' nos termos obtidos"),
    "nao_equipa_alias": (GW, "condicional_revisao_humana", "âmbito do contrato; 'não A' = empate ou vitória B no mesmo âmbito (K-GW-DRAW); alias revisto"),
    "nao_equipa_literal": (GW, "condicional_revisao_humana", "âmbito do contrato"),
    "nao_equipa_sem_lado": (GW, "quarentena_permanente", "equipa não identificável"),
    "nao_resultado_exato": ([], "bloqueado_sem_fonte_oficial", "termos Kalshi de resultado exato não obtidos; sem liquidante"),
    "relogio_ausente": ([], "quarentena_permanente", "cabeçalho LIVE sem minuto nem marcador: fase não verificável"),
}
POLY_PERMANENT = {"spread_contraditorio", "nome_sem_lado", "codigo_12_dupla_hipotese", "relogio_ausente", "nao_equipa_sem_lado",
                  "spread_lado_nao_resolvido", "total_equipa_sem_lado"}

STAKE_RULES = [
    (re.compile(r"\(2up\)"), "regra_promocional_2up", "pagamento antecipado com 2 golos de vantagem: regra própria da Stake"),
    (re.compile(r"Corner|Booking|Card", re.I), "estatistica_nao_golo", "cantos/cartões: o resultado final não decide"),
    (re.compile(r"^\d+(st|nd|rd|th) (Goal|Team to Score)|Team to Score|1st Goal"), "sequencia_golos", "ordem dos golos"),
    (re.compile(r"^\d+ Minutes"), "janela_minutos", "golos por janela de minutos"),
    (re.compile(r"^(1st|2nd) Half - |Both Halves|1st/2nd Half|^Half Time|^Halftime"), "parte_do_jogo", "resultado por parte"),
    (re.compile(r"Win The Final|Winner|Outright|to Qualify|Advance", re.I), "apuramento_ou_torneio", "não decidido pelo resultado de um jogo"),
    (re.compile(r"Correct Score"), "resultado_exato", "liquidável pelo resultado final, sem liquidante no parser"),
    (re.compile(r"Multigoals"), "intervalo_golos", "liquidável pelo resultado final, sem liquidante no parser"),
    (re.compile(r" & "), "combinado", "mercado combinado"),
    (re.compile(r"Both Teams to Score"), "ambas_marcam", "liquidável pelo resultado final, sem liquidante no parser"),
    (re.compile(r"Exact Goals"), "golos_exatos", "liquidável pelo resultado final, sem liquidante no parser"),
    (re.compile(r".+ Total( Goals)?$"), "total_equipa", "liquidável pelo resultado final, sem liquidante no parser"),
    (re.compile(r"^(1x2|Asian Handicap|Total|Asian Total|Match Result|Total Goals|Handicap|Draw No Bet|Double Chance)$"), "mercado_base_nao_liquidado", "mercado base que o liquidante recusou (vocabulário do rótulo ou nome de equipa a investigar no parser)"),
]


def ref(message_id):
    return hashlib.sha256(str(message_id).encode()).hexdigest()[:16]


def stake_class(market):
    for pattern, name, why in STAKE_RULES:
        if pattern.search(market or ""):
            return name, why
    return "outro", "mercado sem classe"


def entry(mid, family, phase, market, label, teams, klass, rules, decision, missing):
    return {"ref": ref(mid), "familia": family, "fase": phase, "mercado": market, "rotulo": label, "equipas": teams,
            "classe": klass, "regras": rules, "decisao": decision, "falta": missing, "admitido": False}


def build(archive):
    seen, rows = set(), []
    for _, row in store.message_rows(Path(archive)):
        if row["message_id"] in seen:
            continue
        seen.add(row["message_id"])
        mid, lines = row["message_id"], row["text"].strip().splitlines()
        parsed = parser.parse(row["text"])
        family, category, fields = pa.diagnostic(lines, parsed)
        if family == "Kalshi" and fields and fields.get("market") in ("selecao_sem_vinculo_literal", "contrato_negado"):
            teams, label = fields["teams"], fields["label"]
            klass = (sp.kalshi_semantics(label, teams) if fields["market"] == "selecao_sem_vinculo_literal" else sp.negated(label, teams))[0]
            rules, decision, missing = KALSHI[klass]
            rows.append(entry(mid, "Kalshi", fields.get("phase"), fields["market"], label, teams, klass, rules, decision, missing))
        elif category == "kalshi_relogio_ausente":
            rows.append(entry(mid, "Kalshi", None, None, None, None, "relogio_ausente", *KALSHI["relogio_ausente"]))
        elif category.startswith("polymarket_"):
            teams = pa.teams_of(lines[1]) if len(lines) > 1 else None
            selection = lines[2].split(" @", 1)[0] if len(lines) > 2 else ""
            if category == "polymarket_contrato_negado":
                klass = sp.negated(selection, teams or [])[0]
            elif category == "polymarket_outro_mercado_ou_identidade":
                klass = sp.polymarket_semantics(selection, teams or [])
            else:
                klass, selection = "relogio_ausente", None
            decision = "quarentena_permanente" if klass in POLY_PERMANENT else "bloqueado_sem_fonte_oficial"
            rows.append(entry(mid, "Polymarket", None, category, selection, teams, klass, [], decision,
                              "regras oficiais Polymarket não obtidas (POLYMARKET em regras.json)"))
        elif family == "Stake" and category == "interpretada" and parsed and parsed.get("market"):
            if parser.settle(parsed.get("market"), parsed.get("selection"), [2, 1], parsed.get("teams")) is None:
                klass, why = stake_class(parsed["market"])
                rows.append(entry(mid, "Stake", parsed.get("phase"), parsed["market"], parsed.get("selection"), parsed.get("teams"),
                                  klass, [], "bloqueado_sem_fonte_oficial", why + "; regras oficiais Stake não obtidas"))
    rows.sort(key=lambda r: (r["familia"], r["classe"], r["ref"]))
    return rows


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", required=True, type=Path)
    cli.add_argument("--out", type=Path, default=HERE / "corpus.jsonl")
    a = cli.parse_args()
    rows = build(a.archive)
    a.out.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    summary = {"archive_sha256": hashlib.sha256(a.archive.read_bytes()).hexdigest(), "linhas": len(rows), "admitidos": 0}
    for r in rows:
        summary.setdefault(r["familia"], {}).setdefault(r["decisao"], 0)
        summary[r["familia"]][r["decisao"]] += 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

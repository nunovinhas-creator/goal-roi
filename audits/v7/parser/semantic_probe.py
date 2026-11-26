"""Sonda semântica offline das rejeições Kalshi e Polymarket (V7, só diagnóstico).

Não altera o parser, o diário, o protocolo nem o registo prospetivo. Cada rótulo é
classificado numa hipótese semântica candidata com a evidência que a suporta. Nenhuma
hipótese é admitida: todas exigem confirmação humana das regras do mercado.

Saída: JSON agregado sem texto bruto, IDs de mensagens ou URLs. Os pares de nomes de
equipas (públicos) são incluídos para permitir a revisão humana dos aliases.
"""
import argparse
from collections import Counter, defaultdict
import json
import re
import unicodedata
from pathlib import Path

from scangoal import parser, store

try:
    from audits.v7.parser import parser_audit as pa
except ImportError:  # execução com PYTHONPATH apontado para a pasta
    import parser_audit as pa

NUMBER = r"[0-9]+(?:\.[0-9]+)?"
MINUS = "−-"
STOP = {"fc", "cf", "sc", "ac", "as", "fk", "cd", "rc", "afc", "club", "de", "the", "1901", "1.", "of", "bv", "09"}
SPREAD = re.compile(rf"Handicap → Spread: (.+?) \(([+−-]{NUMBER})\) \((.+?)\)")
ADVANCE = re.compile(r"soccer_team_to_advance → .+: Team to Advance \((.+)\)")
PLAYER = re.compile(r"soccer_\w+ → .+")
TEAM_TOTAL = re.compile(rf"(.+) Total (?:Over|Under) {NUMBER}")


def fold(text):
    """Minúsculas, sem acentos e com transliterações comuns (ø→o, ü→u)."""
    text = text.replace("ø", "o").replace("Ø", "O").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text.casefold()).split()


def tokens(text):
    return [t for t in fold(text) if t not in STOP]


def initials(text):
    return "".join(t[0] for t in fold(text) if t not in STOP)


def evidence(label, team):
    """Tipo de evidência textual de que `label` designa `team`. None se não houver."""
    a, b = tokens(label), tokens(team)
    if not a or not b:
        return None
    if a == b:
        return "igual_apos_normalizacao"
    if "".join(a) == initials(team) or "".join(a) == initials(team)[:len("".join(a))] and len("".join(a)) >= 3:
        return "sigla"
    if a[0] == b[0] or a[0][:4] == b[0][:4] and len(a[0]) >= 4:
        return "prefixo_comum"
    if set(a) & set(b):
        return "token_comum"
    return None


def resolve_team(label, teams):
    """Devolve (lado, evidência) se exatamente um lado tem evidência; senão (None, motivo)."""
    hits = [(i, e) for i, t in enumerate(teams) if (e := evidence(label, t))]
    if len(hits) == 1:
        return hits[0]
    return None, "ambos_os_lados" if hits else "sem_evidencia_textual"


def kalshi_semantics(label, teams):
    """Hipótese semântica candidata para um rótulo Kalshi sem vínculo literal."""
    if label == "12":
        return "codigo_12_dupla_hipotese", None
    m = re.fullmatch(rf"(.+) ([+{MINUS}]{NUMBER})", label)
    if m:
        side, ev = resolve_team(m[1], teams)
        kind = "alias_mais_handicap"
        if "−" in m[2]:
            kind += "_sinal_unicode"
        return kind if side is not None else kind + "_sem_lado", ev
    side, ev = resolve_team(label, teams)
    return ("alias_vencedor" if side is not None else "nome_sem_lado"), ev


def negated(label, teams):
    inner = label[4:]
    if re.fullmatch(pa.SCORE, inner):
        return "nao_resultado_exato", None
    side, ev = resolve_team(inner, teams)
    if side is None:
        return "nao_equipa_sem_lado", ev
    literal = pa.key_text(inner) in {pa.key_text(t) for t in teams}
    return ("nao_equipa_literal" if literal else "nao_equipa_alias"), ev


def polymarket_semantics(selection, teams):
    if (m := SPREAD.fullmatch(selection)):
        named, _, outcome = m.groups()
        side_named, _ = resolve_team(named, teams)
        side_outcome, _ = resolve_team(outcome, teams)
        if side_named is None or side_outcome is None:
            return "spread_lado_nao_resolvido"
        return "spread_coerente" if side_named == side_outcome else "spread_contraditorio"
    if (m := TEAM_TOTAL.fullmatch(selection)):
        side, _ = resolve_team(m[1], teams)
        return "total_equipa" if side is not None else "total_equipa_sem_lado"
    if ADVANCE.fullmatch(selection):
        return "apuramento"
    if PLAYER.fullmatch(selection):
        return "mercado_jogador"
    if selection == "12":
        return "codigo_12_dupla_hipotese"
    side, _ = resolve_team(selection, teams)
    return "alias_vencedor" if side is not None else "nome_sem_lado"


def probe(repo):
    root = Path(repo).resolve()
    seen = set()
    out = defaultdict(Counter)
    aliases = Counter()
    for _, row in store.message_rows(root / "data/messages.jsonl"):
        if row["message_id"] in seen:
            continue
        seen.add(row["message_id"])
        lines = row["text"].strip().splitlines()
        parsed = parser.parse(row["text"])
        family, category, fields = pa.diagnostic(lines, parsed)
        if family == "Kalshi" and fields:
            teams, label, market = fields["teams"], fields["label"], fields["market"]
            if market == "selecao_sem_vinculo_literal":
                kind, ev = kalshi_semantics(label, teams)
                out["kalshi_sem_vinculo"][kind] += 1
                if ev:
                    out["kalshi_sem_vinculo_evidencia"][ev] += 1
                if kind.startswith("alias"):
                    name = re.sub(rf" [+{MINUS}]{NUMBER}$", "", label)
                    side, _ = resolve_team(name, teams)
                    aliases[("Kalshi", name, teams[side])] += 1
            elif market == "contrato_negado":
                kind, _ = negated(label, teams)
                out["kalshi_negado"][kind] += 1
                out["kalshi_negado_fase"][fields["phase"]] += 1
        elif category == "kalshi_relogio_ausente":
            out["kalshi_relogio_ausente"]["cabecalho_LIVE_sem_minuto_nem_marcador"] += 1
        elif category.startswith("polymarket_"):
            teams = pa.teams_of(lines[1]) if len(lines) > 1 else None
            selection = lines[2].split(" @", 1)[0] if len(lines) > 2 else ""
            if category == "polymarket_contrato_negado":
                kind, _ = negated(selection, teams or [])
                out["polymarket_negado"][kind] += 1
            elif category == "polymarket_outro_mercado_ou_identidade":
                kind = polymarket_semantics(selection, teams or [])
                out["polymarket_outro"][kind] += 1
                if kind == "alias_vencedor":
                    side, _ = resolve_team(selection, teams)
                    aliases[("Polymarket", selection, teams[side])] += 1
            else:
                out["polymarket_cabecalho"]["LIVE_sem_minuto_nem_marcador"] += 1
    return {
        "archive_sha256": __import__("hashlib").sha256((root / "data/messages.jsonl").read_bytes()).hexdigest(),
        "classes": {k: dict(sorted(v.items())) for k, v in sorted(out.items())},
        "aliases_para_revisao_humana": [
            {"familia": f, "rotulo": n, "equipa_no_cabecalho": t, "primeiras_mensagens": c}
            for (f, n, t), c in sorted(aliases.items(), key=lambda x: (-x[1], x[0]))],
        "admissoes": 0,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", default=".", type=Path)
    args = cli.parse_args()
    print(json.dumps(probe(args.repo), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

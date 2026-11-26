"""Reproduce the V7 parser census without writing collection or scientific state."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from pathlib import Path

from scangoal import match_identity, parser, report, store

NUMBER = r"[0-9]+(?:\.[0-9]+)?"
MONEY = rf"({NUMBER})([KM]?)"
SCORE = r"([0-9]{1,2}):([0-9]{1,2})"
KALSHI = re.compile(r"(?:📊){1,2} 🟩 Kalshi(?: ×[1-9][0-9]*)? · (.+)", re.I)
KF_QUOTE = re.compile(rf"(.+?) @({NUMBER})(?:[–-]({NUMBER}))? · \x24{MONEY}")
BF_HEAD = re.compile(r"(?:💷){1,2} 🟨 Betfair(?: ×[1-9][0-9]*)? · LIVE", re.I)
BF_QUOTE = re.compile(rf"([12X]) · (.+?) @({NUMBER}) · \x24{MONEY}")
BF_VOLUME = re.compile(rf"📊 market share ([0-9]{{1,3}})% · 1X2 market at {SCORE} \x24{MONEY} · live")
RESULT = re.compile(r"(.+?)\s+([0-9]{1,2})")
CONTROL = ("Hi! This is ScanGoal", "Questions and support", "📋 My strategies", "📈 My Signals")


def key_text(text):
    return " ".join(text.casefold().split())


def teams_of(line):
    teams = [name.strip() for name in line.rsplit(" · ", 1)[0].split("—")]
    if len(teams) == 2 and all(teams) and key_text(teams[0]) != key_text(teams[1]):
        return teams
    return None


def money(number, suffix):
    return Decimal(number) * {"": 1, "K": 1000, "M": 1_000_000}[suffix]


def kalshi_market(label, teams):
    if label.casefold().startswith("not "):
        return "contrato_negado"
    if re.match(r"1H\b", label, re.I):
        return "primeira_parte"
    if re.search(r"\bteam total\b", label, re.I):
        return "total_equipa"
    if re.search(r"\bto advance\b", label, re.I):
        return "apuramento"
    if re.fullmatch(rf"(?:Over|Under) {NUMBER}", label, re.I):
        return "total_jogo"
    if re.fullmatch(rf"AH[12]\([+-]{NUMBER}\)", label, re.I):
        return "handicap_lado"
    if re.fullmatch(r"BTTS: (?:Yes|No)", label, re.I):
        return "ambas_marcam"
    if label in {"1", "X", "2"} or key_text(label) in {key_text(t) for t in teams}:
        return "vencedor_codigo_ou_nome_literal"
    if any(re.fullmatch(rf"{re.escape(team)} [+−-]{NUMBER}", label, re.I) for team in teams):
        return "handicap_equipa_literal"
    if re.fullmatch(SCORE, label):
        return "resultado_exato"
    return "selecao_sem_vinculo_literal"


def parse_kalshi(lines):
    if len(lines) < 3 or not (header := KALSHI.fullmatch(lines[0])):
        return "cabecalho", {}
    clock = header[1]
    live = parser.LIVE_SCORE.fullmatch(clock)
    if clock.casefold() == "live":
        return "relogio_ausente", {}
    phase = "live" if live else "pre"
    kickoff = None
    if live and int(live[1]) > 130:
        return "minuto", {}
    if not live:
        try:
            kickoff = parser.kickoff_utc(clock) if parser.KICKOFF.fullmatch(clock) else None
        except ValueError:
            pass
        if kickoff is None:
            return "calendario", {}
    teams, quote = teams_of(lines[1]), KF_QUOTE.fullmatch(lines[2])
    if not teams:
        return "equipa", {}
    if not quote:
        return "cotacao", {}
    label, lo, hi, volume, unit = quote.groups()
    lo, hi = Decimal(lo), Decimal(hi or lo)
    if lo <= 1 or hi < lo or money(volume, unit) <= 0:
        return "cotacao", {}
    return "estrutura", {
        "phase": phase, "quote": "intervalo" if quote[3] else "pontual",
        "market": kalshi_market(label, teams), "kickoff": kickoff,
        "teams": teams, "label": label, "odds_range": [str(lo), str(hi)],
        "signal_volume_usd": str(money(volume, unit)),
        "minute": int(live[1]) if live else None,
        "score": [int(live[2]), int(live[3])] if live else None,
        "bet": False,
    }


def parse_betfair_live(lines):
    if len(lines) < 4 or not BF_HEAD.fullmatch(lines[0]):
        return "cabecalho", {}
    if not (teams := teams_of(lines[1])):
        return "equipa", {}
    quote, volume = BF_QUOTE.fullmatch(lines[2]), BF_VOLUME.fullmatch(lines[3])
    if not quote or not volume:
        return "cotacao_ou_volume", {}
    side, label, odds, amount, unit = quote.groups()
    share, home, away, total, total_unit = volume.groups()
    if Decimal(odds) <= 1 or money(amount, unit) <= 0 or money(total, total_unit) <= 0 or int(share) > 100:
        return "cotacao_ou_volume", {}
    expected = "draw" if side == "X" else key_text(teams[int(side) - 1])
    match = key_text(label) in ({"draw", "x"} if side == "X" else {expected})
    return "estrutura", {
        "score": [int(home), int(away)], "scope": "mercado_no_marcador",
        "volume_m": "M" in (unit, total_unit), "team_literal": match,
        "teams": teams, "side": side, "odds": str(Decimal(odds)),
        "selection_volume_usd": str(money(amount, unit)),
        "volume_at_score_usd": str(money(total, total_unit)), "share_pct": int(share),
        "bet": False,
    }


def provider(lines, parsed):
    head = lines[0] if lines else ""
    for family in ("Kalshi", "Betfair", "Polymarket"):
        if family.casefold() in head.casefold():
            return family
    if parsed.get("type") == "STAKE_LOAD" or head.startswith("💸"):
        return "Stake"
    return str(parsed.get("type") or "outro")


def diagnostic(lines, parsed):
    head = lines[0] if lines else ""
    family = provider(lines, parsed)
    if family == "Kalshi":
        reason, fields = parse_kalshi(lines)
        return family, "kalshi_" + reason, fields
    if family == "Betfair" and parsed.get("error"):
        reason, fields = parse_betfair_live(lines)
        return family, "betfair_" + reason, fields
    if parsed.get("error"):
        return family, "erro_parser", {}
    if parsed.get("type") == "POLYMARKET" and not parsed["bet"]:
        if not parser.polymarket_header(head):
            return family, "polymarket_sem_relogio_ou_cabecalho", {}
        selection = lines[2].split(" @", 1)[0] if len(lines) > 2 else ""
        if selection.casefold().startswith("not "):
            return family, "polymarket_contrato_negado", {}
        teams = teams_of(lines[1]) if len(lines) > 1 else None
        market = kalshi_market(selection, teams or [])
        return family, "polymarket_outro_mercado_ou_identidade", {"market": market}
    if parsed.get("type") is not None:
        return family, "interpretada", {}
    if head.startswith("🏁 Signal result"):
        scores = [RESULT.fullmatch(line.strip()) for line in lines[1:3]]
        return family, "nota_resultado_signal", {"placar": len(scores) == 2 and all(scores)}
    if head.startswith("🧑‍💼 NEW COACH"):
        return family, "contexto_treinador", {}
    if head.startswith("📉 Odds drop"):
        return family, "movimento_odds", {}
    if head.startswith(CONTROL):
        return family, "servico_marketing", {}
    return family, "desconhecida", {}


def safe_time(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return result if result.utcoffset() is not None else None


def valid_urls(row):
    links = {m[1] for m in parser.URL.finditer(row["text"])}
    for button in row.get("buttons") or ():
        if isinstance(button, (list, tuple)) and len(button) == 2 and isinstance(button[1], str):
            links.update(m[1] for m in parser.URL.finditer(button[1]))
    return links


def summarize(repo, inventory):
    root = Path(repo).resolve()
    if store.data_dir().resolve() != root / "data":
        raise ValueError("executar no clone fixado com SCANGOAL_DATA_DIR desativado")
    path = root / "data/messages.jsonl"
    raw = path.read_bytes()
    rows = list(store.message_rows(path))
    first, previous, different_ids = set(), {}, defaultdict(set)
    by_type, by_day, issues, versions = Counter(), defaultdict(Counter), defaultdict(Counter), Counter()
    missing, c_market, layouts = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    identity, kickoffs, result_notes = defaultdict(set), defaultdict(set), []
    layouts_by_day = defaultdict(Counter)
    audit, transition, examples, fixture_ids = Counter(), defaultdict(set), {}, set()
    for ordinal, row in rows:
        mid, text = row["message_id"], row["text"]
        is_first = mid not in first
        first.add(mid)
        parsed = parser.parse(text)
        lines = text.strip().splitlines()
        family, category, fields = diagnostic(lines, parsed)
        digest = hashlib.sha256(text.encode()).hexdigest()
        audit["hash_text_discrepancies"] += digest != row["sha256"]
        different_ids[digest].add(mid)
        version = (digest, json.dumps(row.get("buttons"), ensure_ascii=False, sort_keys=True), row.get("edit_date_utc"))
        audit["adjacent_versions_identical"] += previous.get(mid) == version
        previous[mid] = version
        urls = valid_urls(row)
        published, observed, edited = (safe_time(row.get(key)) for key in ("date_utc", "collected_at_utc", "edit_date_utc"))
        versions[category] += 1
        day = published.date().isoformat() if published else "inválida"
        if is_first:
            by_type[category] += 1
            by_day[day][category] += 1
            transition[f"{family}/{category}"].add(day)
            examples.setdefault(category, ordinal)
            issues[family]["first_messages"] += 1
            issues[family]["legacy_unparsed"] += parsed.get("type") is None or bool(parsed.get("error"))
            issues[family]["silencioso_polymarket"] += category.startswith("polymarket_")
            issues[family]["bet"] += bool(parsed.get("bet"))
            issues[family]["edited_at_first_sight"] += bool(row.get("edit_date_utc"))
            issues[family]["ambiguous_explicit_ids"] += len(urls) > 1
            issues[family]["invalid_timestamps"] += (published is None or observed is None or
                                                     bool(row.get("edit_date_utc") and edited is None))
            issues[family]["observed_before_publication"] += bool(published and observed and observed < (edited or published))
            audit["first_messages_with_ft_marker"] += bool(parsed.get("ft"))
            if len(urls) == 1 and parsed.get("bet"):
                fixture_ids.update(urls)
            if parsed.get("bet"):
                problems = missing[family]
                for key in ("teams", "selection", "market", "league"):
                    problems[key] += not parsed.get(key)
                problems["explicit_id"] += len(urls) != 1
                problems["kickoff_pre"] += parsed.get("phase") == "pre" and not parsed.get("kickoff_utc")
                if parser.settle(parsed.get("market"), parsed.get("selection"), [2, 1], parsed.get("teams")) is None:
                    c_market[family][parsed.get("market") or "sem mercado"] += 1
            if family in {"Kalshi", "Betfair", "Polymarket", "Stake"}:
                marked = "emoji_duplo" if lines and lines[0].startswith(("💷💷", "📊📊")) else "cabecalho_simples"
                layouts_by_day[day][f"{family}/{category}/{marked}"] += 1
            if family == "Kalshi" and fields:
                for dimension in ("phase", "quote", "market"):
                    layouts[family][f"{dimension}:{fields[dimension]}"] += 1
            elif family == "Betfair" and fields:
                layouts[family]["volume_M"] += fields["volume_m"]
                layouts[family]["side_text_not_exact"] += not fields["team_literal"]
            elif category == "polymarket_outro_mercado_ou_identidade":
                layouts[family]["reject:" + fields["market"]] += 1
            if category == "nota_resultado_signal":
                audit["signal_result_has_score_syntax"] += bool(fields["placar"])
                audit["signal_result_single_explicit_id"] += len(urls) == 1
                result_notes.append(urls)
        if len(urls) == 1 and parsed.get("teams"):
            one = next(iter(urls))
            kickoff, home, away, _ = match_identity.components(parsed)
            if home and away:
                identity[one].add((home, away))
            if kickoff:
                kickoffs[one].add(kickoff)
        inventory.writerow((ordinal, int(is_first), day, family, category, parsed.get("type") or "",
                            int(bool(parsed.get("error"))), int(bool(parsed.get("bet"))),
                            fields.get("market", ""), len(urls), int(digest == row["sha256"])))

    audit["signal_result_unique_id_with_known_signal"] = sum(len(v) == 1 and bool(v & fixture_ids) for v in result_notes)
    audit["text_hashes_reused_by_different_ids"] = sum(len(ids) > 1 for ids in different_ids.values())
    audit["excess_ids_sharing_exact_text"] = sum(len(ids) - 1 for ids in different_ids.values())
    audit["explicit_ids_with_different_identity_components"] = sum(len(ids) > 1 for ids in identity.values())
    audit["explicit_ids_with_multiple_kickoffs"] = sum(len(items) > 1 for items in kickoffs.values())
    messages, btft, _, notes, report_audit = report.load()
    published = json.loads((root / "data/report.json").read_bytes())
    baseline_count = sum(m.get("type") is None or bool(m.get("error")) for m in messages)
    assert baseline_count == sum(c["legacy_unparsed"] for c in issues.values())
    assert baseline_count == published["unparsed"] and len(first) == published["messages"]
    audit.update(raw_lines=len(raw.splitlines()), valid_versions=len(rows), unique_messages=len(first),
                 unparsed=baseline_count, published_bets=published["unique_bets"],
                 published_settled=published["settled_bets"], published_ft_but_unsettled_market=published["unsettled_market"],
                 fixtures_with_bot_ft=len(btft), linked_standalone_notes=len(notes),
                 standalone_notes_with_parsed_ft=sum(x["type"] == "FULL_TIME_NOTE" and bool(x.get("ft")) for x in messages))
    return {"archive_sha256": hashlib.sha256(raw).hexdigest(), "published_cutoff": published["generated_at_utc"],
            "totals": audit, "issues": issues, "first": by_type, "versions": versions, "by_day_utc": by_day,
            "date_ranges": {key: [min(days), max(days), len(days)] for key, days in transition.items()},
            "markets_without_legacy_ft_settlement": c_market, "bet_fields_missing": missing,
            "candidate_layouts": layouts, "layouts_by_day_utc": layouts_by_day, "examples_archive_line": examples, "report_audit": report_audit,
            "source_state_audit": {k: v for k,v in published["audit"].items() if isinstance(v, int)}}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--inventory", required=True, type=Path)
    args = cli.parse_args()
    with args.inventory.open("w", encoding="utf-8", newline="") as destination:
        inventory = csv.writer(destination)
        inventory.writerow(("line", "first", "date_utc", "family", "classification", "parser_type",
                            "parser_error", "legacy_bet", "candidate_market", "url_id_count", "sha256_valid"))
        out = summarize(Path.cwd(), inventory)
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

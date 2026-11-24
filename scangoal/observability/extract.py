"""Conservative extraction of fields present in a single saved text version."""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from scangoal import match_identity, parser

from .models import ScoreEvent

DIGITS = r'[0-9]{1,4}'
GOAL = re.compile(rf"⚽\s*({DIGITS})(?:\+({DIGITS}))?['′]\s+({DIGITS})\s*[-–]\s*({DIGITS})")
FINAL = re.compile(rf'🏁 FT\s+({DIGITS}):({DIGITS})(?:\s+·.*)?')
TICKET = re.compile(rf'🧾\s+([0-9]{{1,12}})\s+(trade|bet)s?(?:\s+·\s+biggest\s+([^·]+))?(?:\s+·\s+market share\s+[^·]+)?', re.I)
CUMULATIVE = re.compile(r'(?:Σ on this outcome overall|Total on (.+) this match):\s*(.+)', re.I)
PRICE_TOKEN = r'[0-9]+(?:\.[0-9]+)?'
EXCHANGE_PRICE = re.compile(rf'([^@]+)@\s*({PRICE_TOKEN})(?:\s*[-–—]\s*({PRICE_TOKEN}))?\s*·\s*(\$.+)')
STAKE_PRICE = re.compile(rf'📊 [^@]+@\s*({PRICE_TOKEN})(?:\s*[-–—]\s*({PRICE_TOKEN}))?')
USD = re.compile(r'(?:\$\s*|USD\s+)(.+)', re.I)
MILLIONS = re.compile(r'[0-9]+(?:[.,][0-9]{1,2})?M', re.I)


def usd_amount(token: str) -> Decimal | None:
    match = USD.fullmatch(token.strip())
    if not match:
        return None
    amount = match[1].strip()
    if MILLIONS.fullmatch(amount):
        return Decimal(amount[:-1].replace(',', '.')) * 1_000_000
    number = parser.polymarket_amount(amount)
    return Decimal(str(number)) if number is not None else None


@dataclass(frozen=True)
class Extracted:
    scores: tuple[ScoreEvent, ...]
    biggest: Decimal | None
    n_bets: int | None
    count_unit: Literal['bets', 'trades'] | None
    cumulative: Decimal | None
    issues: tuple[str, ...]


def score_events(lines: list[str]) -> tuple[tuple[ScoreEvent, ...], tuple[str, ...]]:
    """Accept incomplete histories, but reject contradictory score paths."""
    goals = []
    finals = set()
    issues = set()
    for line in lines:
        if line.startswith('⚽'):
            match = GOAL.fullmatch(line)
            if not match:
                issues.add('invalid_goal_line')
                continue
            minute, added, home, away = (int(value or 0) for value in match.groups())
            if not 1 <= minute <= 120 or added > 30:
                issues.add('invalid_goal_clock')
                continue
            goal = ScoreEvent('goal', (home, away), minute, added)
            if goals and goal == goals[-1]:
                continue
            goals.append(goal)
        elif line.startswith('🏁 FT'):
            if match := FINAL.fullmatch(line):
                finals.add((int(match[1]), int(match[2])))
            else:
                issues.add('invalid_final_score')
    last_score = (0, 0)
    last_clock = (0, 0)
    for goal in goals:
        clock = goal.minute, goal.stoppage
        if (clock < last_clock or sum(goal.score) <= sum(last_score)
                or any(new < old for new, old in zip(goal.score, last_score))):
            issues.add('incoherent_goal_timeline')
        elif sum(goal.score) - sum(last_score) > 1:
            issues.add('partial_goal_timeline')
        last_score, last_clock = goal.score, clock
    if len(finals) > 1:
        issues.add('conflicting_final_scores')
    final = next(iter(finals)) if len(finals) == 1 else None
    if final is not None and goals:
        if any(end < old for end, old in zip(final, last_score)):
            issues.add('goals_conflict_with_final')
        elif final != last_score:
            issues.add('partial_goal_timeline')

    invalid = issues - {'partial_goal_timeline'}
    result = () if invalid else tuple(goals)
    if final is not None and 'invalid_final_score' not in issues:
        result += (ScoreEvent('final', final),)
    return result, tuple(sorted(issues))


def context(text: str, selection: str | None, *, native_selection: str | None = None) -> Extracted:
    lines = [line.strip() for line in text.splitlines()]
    scores, score_issues = score_events(lines)
    issues = set(score_issues)
    selections = {match_identity.normalized(value) for value in (selection, native_selection) if value}
    tickets = set()
    counts = set()
    totals = set()
    for line in lines:
        if line.startswith('🧾'):
            if match := TICKET.fullmatch(line):
                counts.add((int(match[1]), 'bets' if match[2].lower() == 'bet' else 'trades'))
                if match[3] is not None:
                    amount = usd_amount(match[3])
                    if amount is None:
                        issues.add('invalid_biggest_ticket_usd')
                    tickets.add(amount)
            elif 'biggest' in line.casefold():
                issues.add('invalid_ticket_line')
        elif match := CUMULATIVE.fullmatch(line):
            if match[1] is not None and match_identity.normalized(match[1]) not in selections:
                issues.add('cumulative_selection_unproven')
                continue
            amount = usd_amount(match[2])
            if amount is None:
                issues.add('invalid_cumulative_usd')
            totals.add(amount)
    for values, field in ((tickets, 'biggest_ticket'), (counts, 'bet_count'), (totals, 'cumulative_volume')):
        if len(values) > 1:
            issues.add(f'conflicting_{field}')
    biggest = next(iter(tickets)) if len(tickets) == 1 and 'invalid_ticket_line' not in issues else None
    count, unit = next(iter(counts)) if len(counts) == 1 and 'invalid_ticket_line' not in issues else (None, None)
    cumulative = next(iter(totals)) if len(totals) == 1 and 'cumulative_selection_unproven' not in issues else None
    return Extracted(scores, biggest, count, unit, cumulative, tuple(sorted(issues)))


def exchange_quote(text: str, source: str) -> re.Match | None:
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None
    line = lines[2].strip()
    if source == 'Betfair' and not re.match(r'[12X] · ', line):
        return None
    return EXCHANGE_PRICE.fullmatch(line)


def price_bounds(text: str, source: str, odds: list[str] | None) -> tuple[Decimal | None, Decimal | None]:
    """Verify the entire advertised token, including both ends of a range."""
    if source in ('Polymarket', 'Betfair'):
        match = exchange_quote(text, source)
        tokens = match.group(2, 3) if match else None
    elif source == 'Stake':
        quotes = [line.strip() for line in text.splitlines() if line.startswith('📊 ') and ' → ' in line]
        match = STAKE_PRICE.fullmatch(quotes[0]) if len(quotes) == 1 else None
        tokens = match.groups() if match else None
    else:
        tokens = odds
    if tokens is not None:
        low, high = Decimal(tokens[0]), Decimal(tokens[1] or tokens[0])
        if 1 < low <= high:
            return low, high
    return None, None

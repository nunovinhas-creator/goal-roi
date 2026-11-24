"""Supplementary one-decision-per-fixture analysis of a temporal audit JSON file."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re

from scangoal import chronology, parser
from scangoal.event_audit import VERDICT, stats
from scangoal.event_intelligence import HANDICAP, emitted, kickoff, seen
from scangoal.match_identity import normalized

INVERSION_PATTERNS = ('under_to_over', 'over_to_under')
MOVEMENT_PATTERNS = ('down', 'stable', 'up')
ODDS_BOUNDS = (Decimal('1.5'), Decimal('2'), Decimal('2.5'), Decimal('3.5'))
LOOKBACK_SECONDS = 72 * 3600


def band(value, boundaries):
    if value is None:
        return None
    return next((f'<{bound}' for bound in boundaries if value < bound), f'>={boundaries[-1]}')


def time_of(row):
    return chronology.parse_utc(row['decision_utc'])


def bucket(value, limits):
    return 'unknown' if value is None else band(value, limits)


def canonical_selection(event):
    """Match selection roles across games without equating different positions."""
    if event['direction']:
        return event['direction']
    selection, market = event['selection'], event['market_key']
    if market == 'winner':
        return parser.selection_1x2(selection, event['teams'])
    if market == 'asian_handicap' and selection:
        code = re.fullmatch(r'AH([12])\([+-][0-9.]+\)', selection, re.I)
        if code:
            return code[1]
        if HANDICAP.fullmatch(selection):
            return parser.selection_1x2(selection.rsplit(' (', 1)[0], event['teams'])
    if market == 'both_teams_to_score' and selection:
        return normalized(selection.casefold().removeprefix('btts: '))
    return None


class Replay:
    def __init__(self, audit):
        self.audit = audit
        self.events = {e['message_id']: e for e in audit['events']}
        self.outcomes = audit['outcomes']
        self.history = defaultdict(list)
        for e in audit['events']:
            if e['fixture_id'] is not None and not e.get('exclusion'):
                self.history[e['fixture_id']].append(e)

    def anchor(self, row):
        return self.events[row['message_ids'][-1]]

    def order(self, row):
        first, anchor = (self.events[row['message_ids'][i]] for i in (0, -1))
        return time_of(row), emitted(anchor), anchor['message_id'], emitted(first), first['message_id']

    def first(self, rows, key):
        retained = {}
        for row in sorted((r for r in rows if r['decision_utc'] is not None), key=self.order):
            retained.setdefault(key(row), row)
        return list(retained.values())

    def counted(self, rows):
        result = stats(rows, self.outcomes)
        details = Counter()
        for row in rows:
            label = self.outcomes[row['episode_id']]
            if label['ft'] is None:
                details['without_ft'] += 1
            if row['decision_utc'] is None:
                continue
            decision_label = label['last_payoff']
            if decision_label == -0.5:
                details['half_losses'] += 1
            if decision_label == 0.5:
                details['half_wins'] += 1
            if label['ft'] is None:
                details['decisions_without_ft'] += 1
            prices = self.anchor(row)['odds']
            if not prices or prices[0] != prices[1]:
                details['decisions_without_point_price'] += 1
            elif Decimal(prices[0]) < Decimal(str(parser.MIN_ODDS)):
                details['decisions_below_minimum_price'] += 1
        return dict(result, coverage=dict(details))

    def measure(self, rows):
        anchors = self.first(rows, lambda row: row['message_ids'][-1])
        fixtures = self.first(rows, lambda row: row['fixture_id'])
        counts = Counter(r['fixture_id'] for r in rows if r['decision_utc'] is not None)
        return dict(all=self.counted(rows), per_anchor=self.counted(anchors), per_fixture=self.counted(fixtures),
                    densest_five_decisions=sum(n for _, n in counts.most_common(5)),
                    max_decisions_per_fixture=max(counts.values(), default=0),
                    retained=[r['episode_id'] for r in fixtures])

    def no_preceding_opposite(self, row):
        anchor = self.anchor(row)
        return not any(
            e['direction'] and e['direction'] != anchor['direction']
            and e['market_key'] == anchor['market_key'] and e['period'] == anchor['period']
            and emitted(e) <= emitted(anchor) and seen(e) <= time_of(row)
            for e in self.history[row['fixture_id']]
        )

    def opportunity(self, row, *, movement):
        e = self.anchor(row)
        side = canonical_selection(e)
        price = e['odds']
        if side is None or price is None:
            return None
        if e['phase'] == 'pre' and kickoff(e) is not None:
            context = bucket((kickoff(e) - time_of(row)).total_seconds() / 60, (30, 120, 360))
        elif e['phase'] == 'live' and e['minute'] is not None and e['score'] is not None:
            context = (bucket(e['minute'], (16, 31, 46, 61, 76)), tuple(e['score']))
        else:
            return None
        key = (e['source'], e['signal_type'], normalized(e['market']), e['market_key'], e['period'],
               e['line'], side, e['phase'], context, price[0] == price[1])
        if not movement:
            return (*key, band(Decimal(price[0]), ODDS_BOUNDS))
        prior = row['features']['prior_movement']
        start = self.events[prior['from_message_id']]
        original = prior['from_odds']
        return (*key, start['phase'], original[0] == original[1], band(Decimal(original[0]), ODDS_BOUNDS),
                bucket(e['volume_usd'], (25000, 50000)))

    def movement_support(self, cases, controls):
        cases = self.first(cases, lambda row: row['fixture_id'])
        case_games = {row['fixture_id'] for row in cases}
        controls = [row for row in controls
                    if row['decision_utc'] is not None and row['fixture_id'] not in case_games]
        fields = {
            'same_source': lambda row: (self.anchor(row)['source'],),
            'same_native_market_and_period': lambda row: (
                self.anchor(row)['source'], normalized(self.anchor(row)['market']),
                self.anchor(row)['market_key'], self.anchor(row)['period']),
            'also_selection_role_and_line': lambda row: (
                self.anchor(row)['source'], normalized(self.anchor(row)['market']),
                self.anchor(row)['market_key'], self.anchor(row)['period'],
                canonical_selection(self.anchor(row)), self.anchor(row)['line']),
        }
        return {
            name: sum(any(field(case) == field(control) for control in controls) for case in cases)
            for name, field in fields.items()
        }

    def compare(self, cases, controls, *, movement=False):
        """Greedily match to the latest prior unused game with the same recorded opportunity."""
        treatments = self.first(cases, lambda r: r['fixture_id'])
        treatment_games = {r['fixture_id'] for r in treatments}
        index = defaultdict(list)
        for row in controls:
            if row['decision_utc'] is None or row['fixture_id'] in treatment_games:
                continue
            key = self.opportunity(row, movement=movement)
            if key is not None:
                index[key].append(row)

        matched, used = [], set()
        for case in treatments:
            choices = [r for r in index.get(self.opportunity(case, movement=movement), ())
                       if r['fixture_id'] not in used
                       and 0 <= (time_of(case) - time_of(r)).total_seconds() <= LOOKBACK_SECONDS]
            if choices:
                control = max(choices, key=self.order)
                used.add(control['fixture_id'])
                matched.append((case, control))

        paired_profit, paired_hits = [], []
        for case, control in matched:
            a, b = (self.outcomes[row['episode_id']] for row in (case, control))
            pa, pb = a['theoretical_profit_units'], b['theoretical_profit_units']
            if pa is not None and pb is not None:
                paired_profit.append(pa - pb)
            wa, wb = a['last_payoff'], b['last_payoff']
            if wa and wb:
                paired_hits.append(int(wa > 0) - int(wb > 0))
        return dict(case_games=len(treatments), matched=len(matched), unmatched=len(treatments) - len(matched),
                    cases=self.counted([a for a, _ in matched]), controls=self.counted([b for _, b in matched]),
                    common_priced_n=len(paired_profit),
                    common_priced_mean_difference=round(sum(paired_profit) / len(paired_profit), 6)
                    if paired_profit else None,
                    common_win_loss_n=len(paired_hits),
                    common_hit_difference=round(sum(paired_hits) / len(paired_hits), 6) if paired_hits else None,
                    matched_ids=[[a['episode_id'], b['episode_id']] for a, b in matched])

    def inversions(self, rows, quotes):
        exact_scope = [r for r in rows if r['features']['same_market'] and r['features']['same_period']]
        within_scope = {r['episode_id'] for r in exact_scope}
        controls = {side: [r for r in quotes if r['decision_utc'] is not None
                           and self.anchor(r)['direction'] == side and self.no_preceding_opposite(r)]
                    for side in ('over', 'under')}
        result = {}
        for direction in INVERSION_PATTERNS:
            for same in (True, False):
                subset = [r for r in exact_scope if r['pattern'] == direction and r['features']['same_line'] == same]
                baseline = controls[direction.split('_')[-1]]

                def analyze(group):
                    return dict(self.measure(group), comparable=self.compare(group, baseline))

                facets = {
                    'phase': lambda r: '→'.join(self.events[mid]['phase'] or 'unknown' for mid in r['message_ids']),
                    'source_pair': lambda r: '→'.join(self.events[mid]['source'] for mid in r['message_ids']),
                    'cross_source': lambda r: 'different' if r['features']['cross_source'] else 'same',
                    'gap_minutes': lambda r: band(r['features']['emission_gap_seconds'] / 60, (15, 60)),
                }
                key = f"{direction}:{'same_line' if same else 'different_line'}"
                result[key] = dict(analyze(subset), breakdown={
                    name: {str(value): analyze(group) for value, group in group_by(subset, field).items()}
                    for name, field in facets.items()})
        return dict(rules=result, other_markets_or_periods=self.measure([r for r in rows if r['episode_id'] not in within_scope]))

    def volume(self, rows):
        def initial_price(row):
            values = row['features']['prior_movement'].get('from_odds')
            return bucket(Decimal(values[0]) if values else None, ODDS_BOUNDS)

        fields = {
            'source': lambda r: self.anchor(r)['source'],
            'native_market': lambda r: normalized(self.anchor(r)['market']),
            'selection_role': lambda r: canonical_selection(self.anchor(r)),
            'line': lambda r: self.anchor(r)['line'],
            'period': lambda r: self.anchor(r)['period'],
            'phase': lambda r: self.anchor(r)['phase'],
            'initial_quote_low_band': initial_price,
        }
        by_move = group_by(rows, lambda r: r['pattern'])
        rules = {move: dict(self.measure(group), breakdown={
            name: {str(value): self.measure(subset) for value, subset in group_by(group, field).items()}
            for name, field in fields.items()}) for move, group in by_move.items()}
        observed = [r for r in rows if r['pattern'] in MOVEMENT_PATTERNS]
        first = self.first(observed, lambda r: r['fixture_id'])
        games_by_move = {name: {r['fixture_id'] for r in by_move.get(name, []) if r['decision_utc'] is not None}
                         for name in MOVEMENT_PATTERNS}
        pairs = (('down', 'stable'), ('up', 'stable'), ('down', 'up'))
        return dict(
            volume_min_usd=self.audit['volume_min_usd'], rules=rules,
            first_proven_movement_across_arms={name: self.counted([r for r in first if r['pattern'] == name])
                                               for name in MOVEMENT_PATTERNS},
            overlap={f'{a}:{b}': len(games_by_move[a] & games_by_move[b]) for a, b in pairs},
            comparable={f'{a}:{b}': dict(
                self.compare(by_move.get(a, []), by_move.get(b, []), movement=True),
                support_before_timing_and_reservation=self.movement_support(by_move.get(a, []), by_move.get(b, [])))
                for a, b in pairs},
        )

    def run(self):
        episodes = self.audit['episodes']
        sequences = [r for r in episodes if r['kind'] == 'sequence']
        inversions = [r for r in sequences if r['pattern'] in INVERSION_PATTERNS]
        quotes = [r for r in episodes if r['kind'] == 'quote']
        return dict(as_of_utc=self.audit['as_of_utc'], input_sha256=self.audit['input_sha256'],
                    sequences=self.measure(sequences), all_inversions=self.measure(inversions),
                    inversions=self.inversions(inversions, quotes),
                    volume=self.volume([r for r in quotes if r['features']['high_volume']]), verdict=VERDICT)


def group_by(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--audit', required=True, type=Path)
    args = cli.parse_args()
    audit = json.loads(args.audit.read_bytes())
    print(json.dumps(Replay(audit).run(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

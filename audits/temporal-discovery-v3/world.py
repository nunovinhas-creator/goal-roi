"""Independent synthetic event worlds for the v3 risk-set design.

Written for v3, separately from the v1/v2 banks. Every fixture gets a regular
path of Stake `Over 2.5` quotes; some fixtures get a large-volume Polymarket
antecedent on the same line. The rule under test is the oracle sequence
`Polymarket(volume >= 50k) -> Stake over, same line`. Scenarios control the
true incremental effect, confounding by price, competition composition,
temporal dependence and missing FT. FT respects any recorded LIVE score
because this world is pre-match only.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from math import exp, log
import random

from scangoal import event_intelligence as events
from scangoal.discovery.replay import Replay

START = datetime(2028, 3, 6, tzinfo=timezone.utc)  # Monday; disjoint from v1/v2 banks
RULE = dict(version=2, within_seconds=6 * 3600,
            steps=[[dict(field='source', op='eq', value='Polymarket'), dict(field='volume_usd', op='ge', value=50000)],
                   [dict(field='direction', op='eq', value='over'), dict(field='source', op='eq', value='Stake')]],
            relations=[dict(field='same_line', op='eq', value=True, **{'from': 0, 'to': 1})])


@dataclass(frozen=True)
class Scenario:
    name: str
    days: int = 90
    per_day: int = 24
    share: float = .30
    delta: float = 0.
    odds_low: float = 1.75
    odds_high: float = 2.35
    margin: float = .05
    price_confounding: float = 0.     # antecedent more frequent at low odds
    favourite_bias: float = 0.        # win probability above implied at low odds
    league_gap: float = 0.            # league A wins more often, at the same price
    composition_drift: float = 0.     # antecedent share in league A grows over time
    week_shock: float = 0.            # common shock by week x league
    effect_week_sd: float = 0.        # exposure effect varies by week (dependence)
    ft_missing: float = 0.
    selective_missing: float = 0.     # extra FT loss on exposed losers
    seed: int = 0


def scenarios():
    base = Scenario('null')
    return {s.name: s for s in (
        base,
        replace(base, name='positive_10pp', delta=.10),
        replace(base, name='positive_20pp', delta=.20),
        replace(base, name='negative_10pp', delta=-.10),
        replace(base, name='null_price_confounding', price_confounding=3., favourite_bias=.20),
        replace(base, name='null_composition_drift', league_gap=.12, composition_drift=1.),
        replace(base, name='null_week_shocks', week_shock=.10),
        replace(base, name='null_effect_varies_by_week', effect_week_sd=.20),
        replace(base, name='null_selective_missing_ft', selective_missing=.25),
        replace(base, name='positive_10pp_missing_ft_5pct', delta=.10, ft_missing=.05),
    )}


class World:
    def __init__(self, scenario):
        self.s = scenario
        self.rng = random.Random(f'v3-world/{scenario.name}/{scenario.seed}')
        self.mid = 0
        self.events, self.episodes, self.results = [], [], {}
        self.exposed, self.truth = set(), {}

    def emit(self, fx, ko, at, *, source, direction='over', line='2.5', odds=None, volume=None, league, delay=45):
        self.mid += 1
        seen = at + timedelta(seconds=delay)
        e = dict(message_id=self.mid, journal_line=self.mid,
                 text_sha256=hashlib.sha256(b'v3t%d' % self.mid).hexdigest(),
                 record_sha256=hashlib.sha256(b'v3r%d' % self.mid).hexdigest(),
                 emitted_utc=events.iso(at), edit_utc=None, first_read_utc=events.iso(seen),
                 available_utc=events.iso(seen), receipt='completed_page', match_id=fx, league=league,
                 teams=[fx + ' Home', fx + ' Away'], source=source,
                 signal_type='POLYMARKET' if source == 'Polymarket' else 'STAKE_LOAD', kickoff_utc=events.iso(ko),
                 phase='pre', market='Asian Total', market_key='match_goals', period='regulation',
                 selection=f'{direction.capitalize()} {line}', direction=direction, line=line,
                 odds=[odds, odds] if odds else None, volume_usd=volume, share_pct=None, signal_kind='original',
                 multiplier=1, minute=None, score=None, context={}, fixture_id=fx)
        self.events.append(e)
        if odds:
            self.episodes.append(dict(episode_id=f'quote:{self.mid}', kind='quote', pattern='synthetic',
                                      fixture_id=fx, message_ids=[self.mid], observed_utc=events.iso(seen),
                                      decision_utc=events.iso(seen), exclusion_reasons=[], features={}))

    def build(self):
        s, r = self.s, self.rng
        weeks = {}
        lo, hi = log(s.odds_low), log(s.odds_high)
        for day in range(s.days):
            week = day // 7
            for league in ('League A', 'League B'):
                weeks.setdefault((week, league), r.gauss(0, s.week_shock) if s.week_shock else 0.)
            weeks.setdefault((week, 'effect'), r.gauss(0, s.effect_week_sd) if s.effect_week_sd else 0.)
            for slot in range(s.per_day):
                fx = f'v3-{s.seed}-{day:03d}-{slot:02d}'
                league = 'League A' if slot % 2 == 0 else 'League B'
                ko = START + timedelta(days=day, hours=12 + slot % 8, minutes=20 * (slot // 8))
                base = exp(r.uniform(lo, hi))
                position = (hi - log(base)) / (hi - lo)  # 1 at the shortest price
                share = s.share * (1 + s.price_confounding * (position - .5))
                if league == 'League A' and s.composition_drift:
                    share = s.share * (1 + s.composition_drift * (2 * day / s.days - 1))
                exposed = r.random() < min(.95, max(0., share))
                lead = r.randint(150, 330)
                if exposed:
                    self.emit(fx, ko, ko - timedelta(minutes=lead), source='Polymarket', odds=f'{base * 1.01:.2f}',
                              volume=float(r.choice([60000, 80000, 120000])), league=league)
                    self.exposed.add(fx)
                elif r.random() < .3:
                    self.emit(fx, ko, ko - timedelta(minutes=lead), source='Polymarket', odds=f'{base * 1.01:.2f}',
                              volume=float(r.choice([5000, 10000, 20000])), league=league)
                for minutes in range(300, 0, -30):
                    jitter = r.randint(-4, 4)
                    price = base * (1 + r.uniform(-.015, .015))
                    self.emit(fx, ko, ko - timedelta(minutes=minutes + jitter), source='Stake',
                              odds=f'{price:.2f}', volume=8000., league=league)
                p = (1 - s.margin) / base + s.favourite_bias * (position - .5)
                p += weeks[week, league]
                if league == 'League A':
                    p += s.league_gap / 2
                else:
                    p -= s.league_gap / 2
                unexposed = min(.97, max(.03, p))
                if exposed:
                    p += s.delta + weeks[week, 'effect']
                p = min(.97, max(.03, p))
                won = r.random() < p
                # `increment` is the antecedent's causal effect on this fixture's win probability.
                self.truth[fx] = dict(p=p, increment=p - unexposed, won=won, exposed=exposed, base_odds=base,
                                      league=league)
                missing = s.ft_missing + (s.selective_missing if exposed and not won else 0.)
                if r.random() < missing:
                    continue
                goals = r.choice((3, 4, 5)) if won else r.choice((0, 1, 2))
                home = r.randint(0, goals)
                stamp = ko + timedelta(hours=2)
                self.results[fx] = [dict(source='synthetic', record_id=fx, fixture_id=fx, link=None,
                                         score=(home, goals - home), available_utc=events.iso(stamp),
                                         observed_utc=events.iso(stamp), published_utc=events.iso(stamp),
                                         kickoff_utc=events.iso(ko))]
        end = START + timedelta(days=s.days + 5)
        audit = dict(as_of_utc=events.iso(end), input_sha256={'messages.jsonl': '0' * 64}, events=self.events,
                     episodes=self.episodes, fixture_results=self.results)
        return Replay(audit)


def build(scenario):
    world = World(scenario)
    return world.build(), world

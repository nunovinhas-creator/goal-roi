"""Independent shifted fixture bank, loaded only by the acceptance benchmark.

The engine receives the resulting audit. Labels never enter the miner,
interpreter, registry or ranking.
"""

from datetime import timedelta

NAMES = ('period_handover', 'draw_after_deficit', 'cross_market_relay', 'repeated_context')


def build(bank, seed, *, days=25, per_day=22):
    w = bank.World(seed)
    rng = w.rng
    start = bank.START + timedelta(days=240)
    for day in range(days):
        for slot in range(per_day):
            fixture = f'held-{day}-{slot}'
            ko = start + timedelta(days=day, hours=9 + slot % 13, minutes=7 * slot % 60)
            w.noise(fixture, ko, rng.randint(2, 7))
            if rng.random() >= .55:
                continue
            name = rng.choice(NAMES)
            w.planted.setdefault(name, set()).add(fixture)
            main = ko - timedelta(minutes=rng.randint(103, 184))

            def send(offset, source, signal, *, phase='pre', side='under', line='1.5', price=True,
                     minute=None, score=None, period='regulation', market='Asian Total'):
                at = (main if phase == 'pre' else ko) + timedelta(minutes=offset)
                row = w.emit(fixture, ko, at, source=source, signal=signal, phase=phase,
                             direction=side, line=line, odds=w.odds(1.40, 3.15) if price else None,
                             volume=float(rng.randint(3, 70) * 1000), minute=minute, score=score,
                             delay=rng.randint(1, 200))
                if side is not None:
                    row.update(period=period, market=market)
                return row

            if name == 'period_handover':
                send(0, 'Polymarket', 'POLYMARKET', line='1.5', period='first_half')
                send(16, 'Stake', 'STAKE_LOAD', side='over', line='2.5', period='second_half')
                send(37, 'Polymarket', 'POLYMARKET', side='under', line='3.5')
            elif name == 'draw_after_deficit':
                send(32, 'Stake', 'STAKE_LOAD', phase='live', minute=18, score=[1, 0], line='2.5')
                send(51, 'Polymarket', 'POLYMARKET', phase='live', minute=36, score=[1, 1], line='3.5')
                send(67, 'Stake', 'STAKE_LOAD', phase='live', minute=52, score=[1, 1], line='3.5')
            elif name == 'cross_market_relay':
                send(0, 'Polymarket', 'POLYMARKET', line='4.5', market='Team Total')
                send(12, 'Stake', 'STAKE_LOAD', line='2.5', market='Asian Total')
                send(28, 'Polymarket', 'POLYMARKET', side='over', line='3.5', market='Team Total')
                send(44, 'Stake', 'STAKE_LOAD', side='over', line='4.5', market='Asian Total')
            else:
                send(0, 'ScanGoal', 'OPEN_GAME', side=None, price=False)
                send(14, 'ScanGoal', 'OPEN_GAME', side=None, price=False)
                send(29, 'ScanGoal', 'OPEN_GAME', side=None, price=False)
                send(57, 'Stake', 'STAKE_LOAD', side='over', line='5.5')
            # Deliberately no historical return label in this shifted bank.
    end = start + timedelta(days=days)
    return w, end

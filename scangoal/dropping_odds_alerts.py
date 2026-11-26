"""Independent ScanGoal Dropping Odds parser, replay and at-most-once delivery."""
import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .dropping_odds_journal import GitJournal, JournalError, utc
from .store import data_dir, message_rows

UTC = timezone.utc
LISBON = ZoneInfo('Europe/Lisbon')
LINES = {Decimal(n) for n in ('0', '0.25', '-0.25', '0.75', '-0.75')}
MIN_DROP, MAX_DROP = Decimal(15), Decimal(19)
MIN_ODD, MAX_ODD = Decimal('1.50'), Decimal('3.00')
NUMBER = r'[0-9]{1,9}(?:[.,][0-9]{1,9})?'
PREFIX = re.compile(r'^(?:📉\s*)?Odds drop\b', re.I)
HEADER = re.compile(r'(?:📉\s*)?Odds drop(?:\s*:\s*(.+))?', re.I)
CONTEXT = re.compile(r'(?:⚽\s*)?Football\s*·\s*([^·]+?)\s*·\s*(.+)', re.I)
COUNTDOWN = re.compile(r'starts in (?:(\d{1,5}) h(?: (\d{1,2}) min)?|(\d{1,5}) min)', re.I)
HANDICAP = re.compile(rf'(?:Asian )?Handicap (.+?) \(\s*([+-]?\s*{NUMBER})\s*\)\s*[—–-]\s*'
                      rf'odds\s+({NUMBER})\s*→\s*({NUMBER})\s*\(\s*-?\s*({NUMBER})\s*%\s*\)', re.I)
LINK = re.compile(r'https://scangoal\.com/MySignals\.ashx\?go=([0-9]{1,20})')
TEAM_SEPARATOR = re.compile(r'\s+[–—-]\s+')
WARNING = '⚠ Not a bet and not a forecast: the signal flags a statistical anomaly in the match. The decision and the risk are yours.'
AMBIGUOUS = {'message_format', 'invalid_metadata', 'invalid_countdown', 'ambiguous_fixture',
             'ambiguous_selection', 'ambiguous_edit', 'invalid_handicap', 'percentage_mismatch', 'alert_too_long'}


def clean(text):
    text = unicodedata.normalize('NFKC', text).translate(str.maketrans({'−': '-', '\u200b': '', '\ufe0f': ''}))
    text = re.sub(r'</?(?:b|strong)>|\*\*|__', '', text, flags=re.I)
    return tuple(' '.join(line.split()) for line in text.splitlines() if line.strip())


def identity(text):
    return unicodedata.normalize('NFC', text.casefold())


def number(value):
    return Decimal(value.replace(',', '.').replace(' ', ''))


def canonical(value):
    return format(value.normalize(), 'f') if value else '0'


@dataclass(frozen=True)
class Quote:
    teams: tuple
    league: str
    side: int
    line: Decimal
    initial: Decimal
    final: Decimal
    reported: Decimal
    calculated: Decimal
    go: str


@dataclass(frozen=True)
class Decision:
    reason: str
    kickoff: datetime | None = None
    quote: Quote | None = None
    fixture: tuple | None = None


def parse(lines, sent_at):
    if (len(lines) not in (6, 7) or len(lines) == 7 and lines[6] != WARNING
            or not (header := HEADER.fullmatch(lines[0]))
            or not re.fullmatch(r'Strategy:\s*\S.*', lines[4], re.I)
            or not (link := LINK.fullmatch(lines[5]))):
        return Decision('message_format')
    context = CONTEXT.fullmatch(lines[1])
    if not context:
        return Decision('not_football')
    period = context[2]
    duration = COUNTDOWN.fullmatch(period)
    if not duration:
        return Decision('invalid_countdown' if period.lower().startswith('starts in') else 'not_prematch')
    if duration[2] and int(duration[2]) >= 60:
        return Decision('invalid_countdown')
    try:
        wait = timedelta(hours=int(duration[1] or 0), minutes=int(duration[2] or duration[3] or 0))
        kickoff = sent_at + wait
    except OverflowError:
        return Decision('invalid_countdown')
    if not wait:
        return Decision('not_prematch', kickoff)
    teams = tuple(TEAM_SEPARATOR.split(lines[2]))
    if len(teams) != 2 or not all(teams) or identity(teams[0]) == identity(teams[1]):
        return Decision('ambiguous_fixture', kickoff)
    league = context[1].strip()
    fixture = (league, *teams)
    handicap = HANDICAP.fullmatch(lines[3])
    if not handicap:
        reason = 'invalid_handicap' if re.match(r'(?:Asian )?Handicap\b', lines[3], re.I) else 'market'
        return Decision(reason, kickoff, fixture=fixture)
    selection = identity(handicap[1])
    normalized = tuple(map(identity, teams))
    if selection not in normalized or header[1] and identity(header[1]) != selection:
        return Decision('ambiguous_selection', kickoff, fixture=fixture)
    line, initial, final, reported = map(number, handicap.groups()[1:])
    if initial <= 0:
        return Decision('odds_not_decreasing', kickoff, fixture=fixture)
    # Compare without rounding the calculated drop, at the precision of the displayed percentage.
    with localcontext() as ctx:
        ctx.prec = 48
        scaled_drop = (initial - final) * 100
        calculated = scaled_drop / initial
        half_unit = Decimal('0.5').scaleb(reported.as_tuple().exponent)
        agrees = abs(scaled_drop - reported * initial) <= half_unit * initial
    quote = Quote(teams, league, normalized.index(selection), line,
                  initial, final, reported, calculated, link[1])
    if line not in LINES:
        reason = 'line'
    elif initial <= final:
        reason = 'odds_not_decreasing'
    elif not MIN_ODD <= final <= MAX_ODD:
        reason = 'final_odds'
    elif not MIN_DROP <= reported <= MAX_DROP:
        reason = 'reported_drop'
    elif not agrees:
        reason = 'percentage_mismatch'
    else:
        reason = 'eligible'
    return Decision(reason, kickoff, quote, fixture)


@dataclass(frozen=True)
class Observation:
    mid: int
    sent: datetime
    received: datetime
    decision: Decision


def observation(mid, rows):
    try:
        originals = [row for row in rows if row.get('edit_date_utc') is None]
        dates = {utc(row['date_utc']) for row in rows}
        received = [utc(row['collected_at_utc']) for row in rows]
        sent, available = min(dates), max(received)
        for row, receipt in zip(rows, received):
            edit = utc(row['edit_date_utc']) if row.get('edit_date_utc') is not None else sent
            if (len(dates) != 1 or not sent <= edit <= receipt
                    or (row.get('edit_date_utc') is not None and edit == sent)
                    or (row.get('sha256') is not None
                        and hashlib.sha256(row['text'].encode()).hexdigest() != row['sha256'])):
                raise ValueError
        original_texts = {clean(row['text']) for row in originals}
        version_texts = {clean(row['text']) for row in rows}
        if rows[0].get('edit_date_utc') is not None or len(original_texts) != 1 or version_texts != original_texts:
            return Observation(mid, sent, available, Decision('ambiguous_edit'))
        return Observation(mid, sent, available, parse(original_texts.pop(), sent))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return Observation(mid, None, None, Decision('invalid_metadata'))


def archive_versions(archive):
    grouped, ids = defaultdict(list), set()
    with Path(archive).open('rb'):  # A missing archive is a module failure, not an empty read.
        for _, row in message_rows(archive):
            if not isinstance(row, dict):
                raise ValueError('invalid archive row')
            mid, text = row.get('message_id'), row.get('text')
            header = next((line for line in text.splitlines() if line.strip()), '') if isinstance(text, str) else ''
            lines = clean(header) if header else ()
            is_signal = bool(lines and PREFIX.match(lines[0]))
            if type(mid) is not int or mid <= 0:
                if is_signal:
                    raise ValueError('Dropping Odds row without Telegram identity')
                continue
            grouped[mid].append(row)
            if is_signal:
                ids.add(mid)
    return {mid: grouped[mid] for mid in ids}


def temporal(item, now, max_age):
    if item.received > now or item.sent > now:
        return 'invalid_metadata'
    kickoff = item.decision.kickoff
    if kickoff <= now:
        return 'started'
    if kickoff.astimezone(LISBON).date() != now.astimezone(LISBON).date():
        return 'other_day'
    if max_age is not None and now - item.sent > max_age:
        return 'stale'
    return 'eligible'


def snapshot(item):
    decision, quote = item.decision, item.decision.quote
    result = {'sent_utc': item.sent.isoformat() if item.sent else None,
              'received_utc': item.received.isoformat() if item.received else None,
              'kickoff_utc': decision.kickoff.isoformat() if decision.kickoff else None,
              'message_rule': decision.reason}
    if quote:
        result.update(teams=list(quote.teams), league=quote.league, side=quote.side,
                      line=canonical(quote.line), initial=canonical(quote.initial), final=canonical(quote.final),
                      reported=str(quote.reported), calculated=str(quote.calculated), go=quote.go)
    return result


class SeenQuotes:
    def __init__(self):
        self.matches = defaultdict(list)

    def add(self, data):
        if not isinstance(data, dict) or not {'teams', 'league', 'side', 'line', 'kickoff_utc',
                                               'go', 'initial', 'final'} <= data.keys():
            raise JournalError('missing eligible signal identity')
        try:
            fixture = (identity(data['league']), *(identity(team) for team in data['teams']),
                       data['side'], data['line'])
            kickoff = utc(data['kickoff_utc'])
            quote = (data['initial'], data['final'])
            previous = self.matches[fixture]
            duplicate = any(abs(kickoff - start) <= timedelta(minutes=2) and (go == data['go'] or odds == quote)
                            for start, go, odds in previous)
            if not duplicate:
                previous.append((kickoff, data['go'], quote))
            return duplicate
        except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
            raise JournalError('invalid eligible signal identity') from exc


def odds(value):
    return format(value, f'.{max(2, -value.as_tuple().exponent)}f')


def alert(item):
    quote = item.decision.quote
    stamp = item.decision.kickoff.astimezone(LISBON)
    local_time = stamp.strftime('%H:%M')
    if stamp.utcoffset() != stamp.replace(fold=1 - stamp.fold).utcoffset():
        offset = stamp.strftime('%z')
        local_time += f' UTC{offset[:3]}:{offset[3:]}'
    line = canonical(quote.line)
    if quote.line > 0:
        line = '+' + line
    return (f'📉 DROPPING ODDS — HANDICAP\n\n'
            f'⚽ {" – ".join(quote.teams)}\n🏆 {quote.league}\n'
            f'🕒 {local_time} (Portugal continental)\n\n'
            f'🎯 {quote.teams[quote.side]} AH {line.replace("-", "−")}\n'
            f'📉 {odds(quote.initial)} → {odds(quote.final)} | −{quote.reported}%\n\n'
            f'🔗 ScanGoal · https://scangoal.com/MySignals.ashx?go={quote.go}\n\n'
            '⚠️ Sinal estatístico. Rentabilidade não demonstrada.')


def assess(item, now, max_age, seen, data):
    if not item.decision.quote:
        return item.decision.reason
    reason = temporal(item, now, max_age)
    if reason != 'eligible':
        return reason
    if item.decision.reason != 'eligible':
        return item.decision.reason
    if len(alert(item)) > 3900:
        return 'alert_too_long'
    return 'duplicate' if seen.add(data) else 'eligible'


def sort_key(item):
    return (item.received or datetime.min.replace(tzinfo=UTC), item.mid)


def replay(archive, max_age):
    """Replay the first observable version per Telegram ID at its recorded receipt time."""
    versions = archive_versions(archive)
    first = [observation(mid, rows[:1]) for mid, rows in versions.items()]
    summary = {'messages': len(first), 'archive_versions': sum(map(len, versions.values())),
               'extra_telegram_versions': sum(len(rows) - 1 for rows in versions.values()),
               'same_day': 0, 'eligible': 0, 'ambiguous': 0, 'duplicates': 0, 'excluded': Counter(),
               'handicap_lines': Counter(), 'reported_drops': Counter(), 'final_odds': Counter(), 'alerts': []}
    seen, fixtures = SeenQuotes(), set()
    for item in sorted(first, key=sort_key):
        kickoff, quote = item.decision.kickoff, item.decision.quote
        if item.decision.fixture and kickoff.astimezone(LISBON).date() == item.received.astimezone(LISBON).date():
            summary['same_day'] += 1
            fixtures.add((*(identity(part) for part in item.decision.fixture), kickoff.astimezone(LISBON).date()))
        if quote:
            summary['handicap_lines'][canonical(quote.line)] += 1
            summary['reported_drops'][str(quote.reported)] += 1
            summary['final_odds'][canonical(quote.final)] += 1
        reason = assess(item, item.received, max_age, seen, snapshot(item))
        if reason == 'eligible':
            summary['eligible'] += 1
            summary['alerts'].append({'message_id': item.mid, 'at_utc': item.received.isoformat(),
                                      'text': alert(item)})
        else:
            summary['excluded'][reason] += 1
            summary['ambiguous'] += reason in AMBIGUOUS or item.decision.reason in AMBIGUOUS
            summary['duplicates'] += reason == 'duplicate'
    summary['same_day_fixtures'] = len(fixtures)
    return summary


def mode(env):
    value = env.get('GOAL_ROI_DROPPING_ODDS')
    return value if value in ('shadow', 'on') else 'off'


def age_limit(value):
    if not re.fullmatch(r'[1-9][0-9]{0,4}', value or ''):
        raise ValueError('positive maximum signal age required')
    return timedelta(minutes=int(value))


def run(archive, env, store=None, sender=None, clock=None):
    selected = mode(env)
    if selected == 'off':
        return Counter()
    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    maximum, onset = None, None
    if selected == 'on':
        maximum = age_limit(env.get('GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN'))
        onset = utc(env.get('GOAL_ROI_DROPPING_ODDS_ON_SINCE', ''))
        if onset > now:
            return Counter()
    store = store if store is not None else GitJournal()
    journal = store.read()
    if now < journal.initialized_at or onset and onset < journal.initialized_at:
        raise JournalError('activation precedes available state')
    original_count = len(journal.events)
    counts = Counter()
    for mid, status in list(journal.status.items()):
        if status == 'pending':
            journal.append(mid, 'uncertain', now, reason='interrupted')
            counts['uncertain'] += 1
    seen = SeenQuotes()
    for event in journal.events:
        if event['status'] == 'eligible':
            if seen.add(event.get('signal')):
                raise JournalError('duplicated eligible signal in journal')

    observations = sorted((observation(mid, rows) for mid, rows in archive_versions(archive).items()
                           if mid not in journal.status), key=sort_key)
    token, chat = ('', '')
    if selected == 'on':
        token = (env.get('TG_BOT_TOKEN') or '').strip()
        chat = (env.get('TG_CHAT_ID') or '').strip()

    for item in observations:
        if item.sent and item.sent <= max(journal.initialized_at, onset or journal.initialized_at):
            continue
        decision_time = clock()
        data = snapshot(item)
        reason = assess(item, decision_time, maximum, seen, data)
        journal.append(item.mid, 'received', decision_time, signal=data)
        if reason != 'eligible':
            status = 'expired' if reason in ('started', 'stale') else 'rejected'
            journal.append(item.mid, status, decision_time, reason=reason)
            counts[reason] += 1
            continue
        journal.append(item.mid, 'eligible', decision_time, signal=data, mode=selected)
        if selected == 'shadow':
            counts['eligible'] += 1
            continue
        if not token or not chat:
            journal.append(item.mid, 'failed', decision_time, reason='missing_credentials')
            counts['failed'] += 1
            continue
        if sender is None:
            # The existing transport owns Telegram requests and response classification.
            from .notify import send
            sender = send
        from .notify import DeliveryFailed
        text = alert(item)
        journal.append(item.mid, 'pending', clock(), intent=uuid4().hex)
        store.publish(journal)
        send_time = clock()
        reason = temporal(item, send_time, maximum)
        if reason != 'eligible':
            status = 'expired'
        else:
            try:
                sender(token, chat, text)
            except DeliveryFailed as exc:
                status = 'failed'
                reason = 'telegram_rate_limited' if exc.retryable else 'telegram_rejected'
            except Exception:
                status, reason = 'uncertain', 'telegram_unconfirmed'
            else:
                status, reason = 'sent', 'telegram_confirmed'
        journal.append(item.mid, status, clock(), reason=reason)
        counts[status] += 1
    if len(journal.events) != original_count:
        store.publish(journal)
    return counts


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    offline = commands.add_parser('replay')
    offline.add_argument('archive', type=Path)
    offline.add_argument('--max-age-minutes')
    commands.add_parser('run')
    commands.add_parser('initialize')
    args = parser.parse_args()
    if args.command == 'replay':
        maximum = age_limit(args.max_age_minutes) if args.max_age_minutes else None
        print(json.dumps(replay(args.archive, maximum), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if mode(os.environ) == 'off':
        return 0
    try:
        if (os.environ.get('GITHUB_ACTIONS') != 'true'
                or os.environ.get('GITHUB_REPOSITORY') != 'nunovinhas-creator/goal-roi'
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
            raise JournalError('live entry point requires the Goal-ROI workflow on main')
        if args.command == 'initialize':
            if mode(os.environ) != 'shadow':
                raise JournalError('initialization requires shadow mode')
            GitJournal().bootstrap(datetime.now(UTC))
            return 0
        counts = run(data_dir() / 'messages.jsonl', os.environ)
        print('DROPPING_ODDS', json.dumps(counts, sort_keys=True))
        return 3 if counts['failed'] or counts['uncertain'] else 0
    except (OSError, JournalError, ValueError) as exc:
        print('DROPPING_ODDS_SUSPENDED', type(exc).__name__)
        return 3


if __name__ == '__main__':
    sys.exit(main())

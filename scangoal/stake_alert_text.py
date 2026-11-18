"""Extract Stake occurrences from the text already archived by the ScanGoal reader."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .parser import HEAD, kickoff_utc

STAKE_HEADER = re.compile(r'^(?:💸\s*)+\s*(?:🟦\s*)?Stake(?:\s+money load\b|\s*:\s*load growing\b)', re.I)
BRANDED = re.compile(r'^(?:💸\s*)*🟦\s*Stake\b', re.I)
OTHER_HOUSE = r'(?:Betfair|Polymarket|Kalshi|Bet365|Pinnacle)(?:\s*:|\s+money load\b|\s+LIVE\b|\s*$)'
OTHER_HEADER = re.compile(rf'^\W*{OTHER_HOUSE}', re.I)
INLINE_OTHER = re.compile(rf'\s*\|\s*📊\s*{OTHER_HOUSE}', re.I)
MARKET_LINE = re.compile(r'^📊\s+(.+?)\s+→\s+(.+)$')
SCORE_LINE = re.compile(r'(.+?)\s+(\d+)\s*')
ODD = r'[+−-]?\d+(?:[.,/]\d+)?'
ODDS = re.compile(rf'{ODD}(?:\s*[–—-]\s*{ODD})?')
LISBON = ZoneInfo('Europe/Lisbon')
MAX_UNITS = 3900


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else None


def stake_blocks(text):
    """Respect source headers, including other bookmakers in a composite message."""
    start = None
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        heading = line.strip()
        stake = STAKE_HEADER.match(heading) or BRANDED.match(heading)
        if stake or HEAD.match(heading) or OTHER_HEADER.match(heading):
            if start is not None:
                yield ''.join(lines[start:i])
            start = i if stake else None
        if start is not None and (other := INLINE_OTHER.search(line)):
            yield ''.join(lines[start:i]) + line[:other.start()]
            start = None
    if start is not None:
        yield ''.join(lines[start:])


def match_context(row, lines):
    # The source uses an em dash for prematch and two score lines for live.
    # Edits can rewrite those lines, so an edited layout alone is not evidence.
    single = lines[2].partition('—') if len(lines) > 2 else ('', '', '')
    scores = [SCORE_LINE.fullmatch(line.strip()) if not line.lstrip().startswith('📊') else None
              for line in lines[2:4]]
    prematch = bool(single[0].strip() and single[1] and single[2].strip())
    teams = None
    live = not prematch and len(scores) == 2 and all(scores)
    if prematch:
        teams = f'{single[0].strip()} — {single[2].strip()}'
    elif live:
        teams = f'{scores[0][1].strip()} — {scores[1][1].strip()}'
    try:
        kickoff = kickoff_utc(lines[1]) if len(lines) > 1 else None
    except ValueError:
        kickoff = None
    start, message_time = timestamp(kickoff), timestamp(row.get('date_utc'))
    before = start is not None and message_time is not None and message_time < start
    after = start is not None and message_time is not None and message_time > start
    phase = 'NÃO DETERMINADO'
    if row.get('edit_date_utc'):
        if before:
            phase = 'PRÉ-JOGO'
    elif live and not before:
        phase = 'LIVE'
    elif prematch and not after:
        phase = 'PRÉ-JOGO'
    elif before and not live:
        phase = 'PRÉ-JOGO'
    return teams, phase


def block_bodies(row, block):
    lines = block.splitlines()
    teams, phase = match_context(row, lines)
    market_lines = list(
        part.strip() for line in lines if line.lstrip().startswith('📊')
        for part in re.split(r'\s*\|\s*(?=📊)', line)
    )
    fields = [f'⚽ Jogo: {teams}'] if teams else []
    fields.append(f'📍 Tipo: {phase}')
    bets = []
    for line in market_lines:
        selection = MARKET_LINE.fullmatch(line)
        if selection is None:
            break
        pick, _, price = selection[2].partition('@')
        if not pick.strip():
            break
        bets.append((selection[1], pick.strip(), price.strip(), line))
    if not bets or len(bets) != len(market_lines):
        yield '\n'.join([*fields, '', 'Mensagem original', block])
        return
    for market, pick, price, line in bets:
        details = [*fields, f'🎯 Mercado: {market}', f'Seleção: {pick}']
        if ODDS.fullmatch(price):
            details.append(f'💰 Odd: {price}')
        elif price:
            details.append(f'Linha original: {line}')
        yield '\n'.join(details)


def telegram_chunks(header, body):
    """Keep the entire body, allowing for astral characters and continuation headers."""
    prefix = header
    chunk, units = [], len(prefix.encode('utf-16-le')) // 2
    for char in body:
        width = 1 + (ord(char) > 0xFFFF)
        if units + width > MAX_UNITS:
            yield prefix + ''.join(chunk)
            prefix = header + 'Continuação\n\n'
            chunk, units = [], len(prefix.encode('utf-16-le')) // 2
        chunk.append(char)
        units += width
    yield prefix + ''.join(chunk)


def payloads(row):
    """Texts in source order. A long occurrence may have continuation parts."""
    when = timestamp(row.get('date_utc'))
    header = '🚨 GOAL-ROI | STAKE\n🏦 Casa: Stake\n'
    if when:
        header += f'🕒 Hora da mensagem: {when.astimezone(LISBON):%H:%M de %d/%m}\n'
    header += f'Origem: ScanGoal · mensagem #{row["message_id"]}\n\n'
    for block in stake_blocks(row['text']):
        if not STAKE_HEADER.match(block.lstrip()):
            print('STAKE_FORMAT_REVIEW')
            if not any(MARKET_LINE.fullmatch(line.strip()) for line in block.splitlines()):
                continue
            yield from telegram_chunks(header, '📍 Tipo: NÃO DETERMINADO\nMensagem original\n' + block)
            continue
        for body in block_bodies(row, block):
            yield from telegram_chunks(header, body)

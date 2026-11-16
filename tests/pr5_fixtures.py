"""PR 5 fixtures: the `presentation` block a real report carries (brain.presentation), for synthetic reports in tests.

Since PR 5 a status change is only announced when the report itself shows it (hypothesis present, no pending status, last
fixed change pointing to the new status) and a positive claim needs the hypothesis' own certificate. Synthetic reports of
older tests did not carry that evidence; `present()` adds exactly what the brain would publish for a genuine change.
"""
CHANGE_UTC = '2026-11-12T10:00:00+00:00'
CERT_UTC = '2026-11-12T09:00:00+00:00'


def row(status, games=31, **extra):
    r = {'status': status, 'pending_status': None, 'changes': 1,
         'last_change': {'games': games, 'from': 'em observação', 'to': status, 'prob_above': 0.97, 'known_utc': CHANGE_UTC},
         'family': 'Jogo aberto', 'conditions': [], 'expect': None, 'games': games, 'game_ids': None, 'confirmation': None}
    r.update(extra)
    return r


def present(report, certified=(), observed=(), extra=None):
    """Rebuild report.brain.presentation from the current status_list. certified: ids with their own certificate (and listed
    in validation.certified); observed: ids explicitly back to "em observação"; extra: {id: status} rows not in status_list."""
    brain = report['brain']
    hyps = {hid: row(st) for hid, st in (brain.get('status_list') or {}).items()}
    for hid, st in (extra or {}).items():
        hyps.setdefault(hid, row(st))
    for hid in observed:
        hyps[hid] = row('em observação')
    for rank, hid in enumerate(certified, 1):
        hyps.setdefault(hid, row('forte'))['confirmation'] = {'rank': rank, 'certified_utc': CERT_UTC, 'certifiable': True}
    brain['presentation'] = {'version': 'telegram_presentation_v1', 'hypotheses': hyps}
    return report


def body(text):
    """The notice without the scientific-state lines that PR 5 puts on top of every message."""
    lines = text.split('\n')
    while len(lines) > 1 and lines[0].startswith('Estado científico:'):
        lines.pop(0)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------------------------------------------------
# Daily-summary schedule (telegram_bet_short_v1): status changes are announced ONLY in the 22:00 Europe/Lisbon summary.

def evening(t, minute=5):
    """22:MM in Lisbon on the Lisbon date of `t`, as UTC (the daily summary, the only place a status change is announced)."""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    local = t.astimezone(ZoneInfo('Europe/Lisbon')).replace(hour=22, minute=minute, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def changes(texts):
    """Status changes announced in the daily summaries among `texts`: [(hypothesis id, state line)]."""
    out = []
    for t in texts:
        if not t.startswith('Resumo das táticas'):
            continue
        for block in t.split('\n\n')[1:]:
            lines = block.split('\n')
            if lines[0].startswith('✅'):
                out.append((lines[0].rsplit(': ', 1)[1], lines[0]))
            elif len(lines) > 1 and lines[1].startswith(('Estado: ', '⛔ Estado: ')):
                out.append((lines[0], lines[1]))
    return out

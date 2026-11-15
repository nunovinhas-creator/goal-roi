"""Shared, fail-closed eligibility for evidence supporting betting decisions."""
import math

from .parser import MIN_ODDS


def text_identity(value):
    """A verifiable identity is a non-empty string (after stripping); anything else is None. Never invent one."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def game_identity(signal):
    """Game a signal belongs to, or None when it cannot be proven (no match_id: no independent observation)."""
    return text_identity(signal.get('match_id'))


def bet_identity(signal):
    """(market, selection) of a signal inside its game; a missing part stays None (conservatively merged, never split)."""
    return text_identity(signal.get('market')), text_identity(signal.get('selection'))


def exclusion_reasons(signal):
    reasons = []
    for flag in ('had_ft_at_first_sight', 'edited_at_first_sight', 'burst'):
        if signal.get(flag):
            reasons.append(flag)
    if signal.get('in_roi') is not True:
        reasons.append('provenance_not_verified')
    odds = signal.get('odds')
    if (isinstance(odds, bool) or not isinstance(odds, (int, float))
            or not math.isfinite(odds) or odds < MIN_ODDS):
        reasons.append('no_valid_odds')
    if not signal.get('market'):
        reasons.append('no_market')
    # PR 2 (review #5910343843): an observation only supports decisions when its game and bet are identifiable.
    if game_identity(signal) is None:
        reasons.append('no_match_identity')
    if text_identity(signal.get('selection')) is None:
        reasons.append('no_selection')
    return reasons


def eligible(signal):
    return not exclusion_reasons(signal)

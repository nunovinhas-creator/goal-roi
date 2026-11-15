"""Shared, fail-closed eligibility for evidence supporting betting decisions."""
import math

from .parser import MIN_ODDS


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
    return reasons


def eligible(signal):
    return not exclusion_reasons(signal)

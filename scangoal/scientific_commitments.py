"""Bind settlement events to the admission and the available result evidence."""

from datetime import datetime

from . import chronology, opportunity_results, scientific_receipts
from .parser import settle


ADMISSION = 'certification-admission'
SETTLEMENT = 'certification-settlement'
LEGACY_OUTCOME = 'certification-outcome'


def _observation(row):
    fields = ('source', 'record_id', 'period', 'score', 'observed_at', 'available_at',
              'published_at', 'kickoff', 'fixture_id', 'link', 'provider_record')
    record = {key: getattr(row, key, None) for key in fields}
    return {key: chronology.iso(value) if isinstance(value, datetime) else value for key, value in record.items()}


def settlements(admission, hypothesis, bet, as_of, *, profit, odds_loss):
    """Build an append-only sequence from consensus transitions, including post-deadline corrections."""
    kickoff = chronology.kickoff_time(bet)
    if kickoff is None or as_of is None or admission['period'] not in (None, 'regulation'):
        return
    market = admission['market']
    if not isinstance(market, str) or (bet.get('phase') == 'live' and market.casefold().strip() == 'asian handicap'):
        return
    contract = {key: admission[key] for key in ('market', 'selection', 'period', 'odds')}
    contract.update(teams=bet.get('teams'), phase=bet.get('phase'), odds_loss=odds_loss)
    earliest = kickoff + opportunity_results.REGULATION_TIME
    rows = [row for row in bet.get('_ft_observations', ())
            if isinstance(row, opportunity_results.Observation) and row.period == 'regulation'
            and row.source and row.record_id and row.score is not None
            and row.observed_at is not None and row.available_at is not None
            and earliest <= row.observed_at <= row.available_at <= as_of
            and (not row.source.startswith('telegram') or row.published_at is not None)
            and (row.published_at is None or earliest <= row.published_at <= row.available_at)]
    observations = {}
    for row in rows:
        observations.setdefault(row.available_at, []).append(row)

    previous = None
    for available, observed in sorted(observations.items()):
        result = opportunity_results.resolve(rows, kickoff=kickoff, as_of=available)
        if result.score is None or result.available_at != available:
            continue
        won = settle(market, admission['selection'], result.score, contract['teams'])
        value = profit(dict(contract, w=won))
        if value is None:
            continue
        proof = {}
        for row in observed:
            if row.score == result.score:
                item = _observation(row)
                proof[scientific_receipts.digest('certification-observation', item)] = item
        if not proof:
            continue
        result_version = {'fixture': admission['fixture'], 'period': 'regulation', 'kickoff_utc': chronology.iso(kickoff),
                          'ft': list(result.score), 'available_utc': available.isoformat(),
                          'observations': [proof[key] for key in sorted(proof)]}
        content = {'admission_id': admission['id'], 'hypothesis': hypothesis, 'ft': list(result.score),
                   'profit': value, 'available_utc': available.isoformat(), 'contract': contract,
                   'result_version': scientific_receipts.digest('certification-result-version', result_version),
                   'evidence': result_version, 'supersedes': previous}
        previous = scientific_receipts.digest(SETTLEMENT, content)
        yield content, previous, available


def verify(directory, domain, content, as_of, archive):
    if archive and directory is not None:
        scientific_receipts.save_commitment(directory, domain, content)
    return scientific_receipts.verify(directory, domain, content, as_of=as_of)


def receipt_summary(receipt):
    if receipt is None:
        return None
    return {'id': receipt.identifier, 'digest': receipt.digest, 'at_utc': chronology.iso(receipt.at)}

"""Paper observation of book snapshots and separately verified event tapes."""

from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from scangoal import event_intelligence as events
from scangoal.discovery.replay import Budget
from scangoal.discovery.v32 import riskset
from . import books, fixtures, manifest


def open_fixture(directory, recorded=()):
    plan, rows, catalogue, trusted, authority, capture = fixtures.build(recorded)
    plan_sha, _ = manifest.load_plan(plan, authority.public_key())
    log = manifest.Journal(directory, plan_sha, capture.public_key())
    head = log.append_many(rows, capture)
    replay = manifest.VerifiedReplay(plan, authority.public_key(), directory, capture.public_key(), witness=head)
    if manifest.sha(catalogue) != replay.catalog_sha256:
        raise manifest.Invalid('catalogue_changed_after_plan')
    return replay, books.catalog(catalogue, trusted), trusted


def _book(contract, state):
    if state['status'] != 'received':
        return state['status'], None
    try:
        return 'observed', books.read_book(contract, state['receipt'])
    except manifest.Invalid as exc:
        return str(exc), None


def inventory(replay, contracts, final):
    by_id = {x.id: x for x in contracts}
    slots = []
    for slot_id, slot in replay.slots.items():
        if slot.mode != manifest.POLL:
            continue
        contract = by_id[slot.target]
        state = final.slots[slot_id]
        status, book = _book(contract, state)
        row = state['receipt'] or {}
        request = row.get('requested_utc')
        received = row.get('available_utc')
        latency = (manifest.stamp(received) - manifest.stamp(request)).total_seconds() if request and received else None
        slots.append(dict(slot=slot_id, scheduled_utc=slot.end, contract_id=slot.target, **books.identity(contract),
                          status=status, planned=True, attempted=state['attempted'], observed=book is not None,
                          lost=book is None, requested_utc=request, available_utc=received,
                          source_utc=row.get('source_utc'), latency_seconds=latency,
                          body_sha256=row.get('body_sha256'), book=book))
    return sorted(slots, key=lambda row: (row['scheduled_utc'], row['slot']))


def _comparisons(replay, contracts, recorded, trusted):
    known = {item.id: item for item in contracts}
    by_scope = {game: {} for game in trusted}
    by_contract = defaultdict(list)
    for item in contracts:
        by_scope[item.game.id][item.scope] = item
    for row in recorded:
        by_contract[row['contract_id']].append((manifest.stamp(row['scheduled_utc']), row['slot']))
    times = {key: [clock for clock, _ in rows] for key, rows in by_contract.items()}
    probes = []
    evidence_at = {}
    captured = set()
    for _, receipt in replay.entries:
        slot = replay.slots[receipt['slot']]
        if slot.mode != manifest.POLL or slot.id in captured:
            continue
        ref = known[slot.target]
        if ref.game.id not in fixtures.REFERENCES:
            continue
        at = manifest.stamp(receipt['available_utc'])
        evidence_at.setdefault(at, None)
        if evidence_at[at] is None:
            evidence_at[at] = replay.at(at)
        evidence = evidence_at[at]
        if _book(ref, evidence.slots[slot.id])[0] != 'observed':
            continue
        captured.add(slot.id)
        for game_id, game in sorted(trusted.items()):
            if game_id == ref.game.id:
                continue
            comparator = by_scope[game_id].get(ref.scope)
            candidate_id = match_slot = None
            age = None
            status = 'excluded'
            if abs(manifest.stamp(game.kickoff) - manifest.stamp(ref.game.kickoff)) > books.KICKOFF_GAP:
                reason = 'kickoff_outside_45_minutes'
            elif comparator is None:
                reason = 'protocol_or_contract_scope_mismatch'
            elif at >= manifest.stamp(game.kickoff):
                reason = 'comparator_started'
            else:
                candidate_id = comparator.id
                index = bisect_right(times[candidate_id], at) - 1 if candidate_id in times else -1
                if index < 0:
                    status, reason = 'indeterminate', 'no_comparator_slot_due'
                else:
                    _, match_slot = by_contract[candidate_id][index]
                    actual = evidence.slots[match_slot]
                    reason, snapshot = _book(comparator, actual)
                    if snapshot is None:
                        status = 'indeterminate'
                    else:
                        age = (at - manifest.stamp(actual['receipt']['source_utc'])).total_seconds()
                        if age > books.MAX_BOOK_AGE.total_seconds():
                            reason = 'comparator_book_stale'
                        else:
                            status, reason = 'valid', 'exact_sampled_book_comparator'
            probes.append(dict(reference_slot=slot.id, comparison_utc=events.iso(at),
                               candidate_game=game_id, candidate_contract=candidate_id, candidate_slot=match_slot,
                               status=status, reason=reason, age_seconds=age))
    return probes


def proof_probe(replay, trusted):
    """No raw CLOB token price enters the frozen inference tape."""
    at = fixtures.DECISION
    evidence = replay.at(at)
    output = {}
    for protocol in (books.CTF, books.V2):
        fixtures_by_id = {name: game.fixture for name, (game, venue) in fixtures.games().items()
                          if venue == protocol and name in trusted and name != 'outside-46'}
        keys = set(fixtures_by_id.values())
        spans = [span for span in evidence.coverage if span.fixture in keys]
        tape = replay.tape_at(at, fixtures=fixtures_by_id)
        cohort = riskset.RiskSet(tape).run(fixtures.FEED_RULE, Budget(50_000))
        draft = []
        sequence = riskset.Sequence(fixtures.FEED_RULE)
        for name, fixture in sorted(fixtures_by_id.items()):
            view = riskset.view_at(tape, fixture, at)
            if len(view.quotes) != 1:
                draft.append(dict(game=name, antecedent='indeterminate', streams=[]))
                continue
            quote, = view.quotes.values()
            match, _ = riskset.opportunity(tape, sequence, quote, view, at, Budget(50_000))
            draft.append(dict(game=name, antecedent=match.state,
                              streams=sorted({stream for span in spans if span.fixture == fixture for stream in span.streams})))
        output[protocol] = dict(probe_utc=events.iso(at), rows=draft, admissible_pairs=len(cohort.pairs))
    return output


def run(directory, *, captures=()):
    replay, contracts, trusted = open_fixture(directory, captures)
    final = replay.at(max(manifest.stamp(game.kickoff) for game in trusted.values()))
    recorded = inventory(replay, contracts, final)
    comparisons = _comparisons(replay, contracts, recorded, trusted)
    proof = proof_probe(replay, trusted)
    book_counts = dict(planned=len(recorded), attempted=sum(row['attempted'] for row in recorded),
                       observed=sum(row['observed'] for row in recorded), lost=sum(row['lost'] for row in recorded),
                       reasons=dict(Counter(row['status'] for row in recorded)))
    measurements = {}
    for protocol in (books.CTF, books.V2):
        selected = [row for row in recorded if row['protocol'] == protocol]
        observed = [row for row in selected if row['observed']]
        latency = sorted(row['latency_seconds'] for row in observed)
        measurements[protocol] = dict(
            planned=len(selected), observed=len(observed), distinct_tokens=len({row['contract_id'] for row in observed}),
            max_latency_seconds=max(latency, default=None), median_latency_seconds=median(latency) if latency else None,
            zero_two_sided_liquidity=sum(row['status'] == 'no_two_sided_liquidity' for row in selected),
            reopened=sum(bool(row['book'] and row['book']['market_state'] == 'reopened') for row in selected))
    ages = [row['age_seconds'] for row in comparisons if row['status'] == 'valid']
    return dict(schema='goal_roi_offline_observation_v1', evidence_domain=manifest.DOMAIN,
                manifest_sha256=replay.plan_sha, chain_head_sha256=replay.head, catalog_sha256=replay.catalog_sha256,
                recorded_book_responses=len(captures),
                window=dict(hours_before_kickoff=6, max_cadence_minutes=15, kickoff_caliper_minutes=45,
                            included_games=sorted({row['game_id'] for row in recorded})),
                slots=book_counts, by_protocol=measurements, observations=recorded,
                comparators=dict(Counter(row['status'] for row in comparisons)),
                comparator_reasons=dict(Counter(row['reason'] for row in comparisons)),
                comparator_age_seconds=dict(min=min(ages, default=None), max=max(ages, default=None),
                                            median=median(ages) if ages else None),
                comparisons=comparisons, independent_stream_probe=proof,
                coverage_by_stream=dict(Counter(next(iter(span.streams)) for span in final.coverage)),
                book_continuous_coverage_spans=0, price_basis=books.PRICE_UNIT, orders_sent=0, network_calls=0,
                notifications_sent=0, confirmatory_pairs=0, power=None)


def temporary_run(*, captures=()):
    with TemporaryDirectory(prefix='goal-roi-offline-pilot-') as work:
        return run(Path(work) / 'journal', captures=captures)

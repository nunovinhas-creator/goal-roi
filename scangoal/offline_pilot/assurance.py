"""Independent executable challenges for v3.2 and the experimental trust boundary."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scangoal import event_intelligence as events
from scangoal.discovery.replay import Budget
from scangoal.discovery.v32 import engine, riskset
from scangoal.discovery.v32.tape import STREAMS, Tape
from scangoal.observability import contract_key
from . import books, fixtures, manifest

T = datetime(2041, 3, 8, 19, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)
M = timedelta(minutes=1)
LEFT = T - 30 * M


def lab_games():
    return [books.Game(name, events.iso(T + 80 * M), name + '-home', name + '-away', 'audit-competition')
            for name in ('challenger', 'clean-control')]


def slot_for(game, stream, payload, *, left=LEFT, right=T):
    return manifest.Slot(game.fixture, stream, manifest.COMPLETE, events.iso(left), events.iso(right),
                         manifest.sha((manifest.COMPLETE, game.fixture, stream)), manifest.sha(payload))


def planned_records(games, rows, *, gaps=(), late=(), omitted=(), replacements=None):
    out = []
    replacements = {} if replacements is None else replacements
    for game in games:
        for stream in sorted(STREAMS):
            payload = [row for row in rows if row['match_id'] == game.id and row['event_kind'] == stream]
            slot = slot_for(game, stream, payload)
            key = game.id, stream
            if key in omitted:
                out.append((slot, None))
                continue
            if key in gaps:
                row = manifest.receipt(slot, requested=None, available=T, source_clock=None,
                                       status='offline', payload=None)
            else:
                seen = T + SECOND if key in late else T
                received = replacements.get(key, payload)
                row = manifest.receipt(slot, requested=T, available=seen, source_clock=T,
                                       status='received' if received else 'empty', payload=received)
            out.append((slot, row))
    return out


def verified(directory, records):
    authority, capture = fixtures.authorities()
    plan = manifest.make_plan((slot for slot, _ in records), sealed_at=LEFT - M,
                              catalog_sha256=manifest.sha('independent-audit-catalogue'), authority=authority)
    plan_sha, _ = manifest.load_plan(plan, authority.public_key())
    log = manifest.Journal(directory, plan_sha, capture.public_key())
    rows = sorted((row for _, row in records if row is not None), key=lambda row: (row['available_utc'], row['slot']))
    head = log.append_many(rows, capture)
    return manifest.VerifiedReplay(plan, authority.public_key(), directory, capture.public_key(), witness=head)


def control_state(tape, fixture, rule=fixtures.FEED_RULE):
    state = riskset.view_at(tape, fixture, tape.as_of)
    if not state.quotes or tape.blocked_at(fixture, tape.as_of):
        return 'indeterminate'
    quote, = state.quotes.values()
    match, _ = riskset.opportunity(tape, riskset.Sequence(rule), quote, state, tape.as_of, Budget(10_000))
    return match.state


def _support(tape):
    cohort = riskset.RiskSet(tape).run(fixtures.FEED_RULE, Budget(50_000))
    return dict(pairs=len(cohort.pairs), edges=manifest.sha(cohort.admissible), signature=engine.pair_signature(cohort))


def run():
    case, control = lab_games()
    target = fixtures.typed_event(case, T - 4 * M, 'alert', source='Polymarket', volume_usd=90_000)
    first = fixtures.typed_event(case, T - 2 * M)
    comparator = fixtures.typed_event(control, T - 8 * M)
    late_alert = fixtures.typed_event(control, T - M, 'alert', source='Polymarket', volume_usd=91_000)
    rows = [target, first, comparator]

    scenario = [
        ('proved_empty', rows, {}, 'absent', 1),
        ('outage_alert', rows, {'gaps': [(control.id, 'alert')]}, 'indeterminate', 0),
        ('missing_revision_receipt', rows, {'omitted': [(control.id, 'revision')]}, 'indeterminate', 0),
        ('late_goal_seal', rows, {'late': [(control.id, 'goal')]}, 'indeterminate', 0),
        ('suppressed_known_alert', [*rows, late_alert], {'replacements': {(control.id, 'alert'): []}},
         'indeterminate', 0),
        ('antecedent_after_comparator_quote', [*rows, late_alert], {}, 'present', 0),
    ]
    findings = []
    for name, events_, options, answer, matches in scenario:
        with TemporaryDirectory() as work:
            replay = verified(Path(work) / 'captured', planned_records([case, control], events_, **options))
            evidence = replay.at(T)
            tape = replay.tape_at(T)
            state = control_state(tape, control.fixture)
            support = _support(tape)
            if (state, support['pairs']) != (answer, matches):
                raise AssertionError((name, state, support))
            # Future copies never publish an earlier synthetic observation.
            if any(events.seen(e) != T for e in evidence.rows):
                raise AssertionError('backdated_captured_tape')
            before = replay.tape_at(T - SECOND)
            if before.by_fixture:
                raise AssertionError('future_sealed_tape_leaked')
            # Transport order, source IDs and inert results cannot change
            # the point-in-time answer, support or event model fingerprint.
            for seed in (1789, 2207, 3739, 9923):
                rng = Random(seed)
                shuffled = [dict(row, message_id=rng.randrange(10**9), journal_line=rng.randrange(10**9),
                                 ft=[8, 5]) for row in [*evidence.rows, *evidence.rows]]
                rng.shuffle(shuffled)
                transformed = Tape(manifest.conservative_quotes(shuffled), T, reversed(evidence.coverage))
                if transformed.snapshot_sha256 != tape.snapshot_sha256 or _support(transformed) != support:
                    raise AssertionError((name, 'permutation_or_late_results_changed_support'))
            findings.append(dict(scenario=name, antecedent=state, coverage_slots=len(evidence.coverage),
                                 receipt_reasons=evidence.counts, **support))

    # Isolated velocity-independent conflict. Frozen v3.2 masks conflicting
    # prices but a deterministic sort cannot resolve contradictory volumes.
    two_quotes = [dict(comparator, volume_usd=80_000), dict(comparator, volume_usd=100)]
    with TemporaryDirectory() as work:
        archive = verified(Path(work) / 'conflict', planned_records([case, control], [target, first, *two_quotes]))
        at = archive.at(T)
        raw = Tape(at.rows, T, at.coverage)
        gated = archive.tape_at(T)
        raw_quote, = riskset.view_at(raw, control.fixture, T).quotes.values()
        masked, = riskset.view_at(gated, control.fixture, T).quotes.values()
        if raw_quote['volume_usd'] not in {100, 80_000} or masked['volume_usd'] is not None:
            raise AssertionError('equal_price_volume_collision_not_reproduced')
        if not raw.covered(control.fixture, LEFT, T, T) or gated.covered(control.fixture, LEFT, T, T):
            raise AssertionError('conflicting_state_must_withhold_absence')
        findings.append(dict(scenario='equal_price_conflicting_volume', frozen='picked_one_report',
                             pilot='unknown_volume_and_absence_indeterminate'))
        scope = contract_key(dict(comparator, fixture_id=control.fixture))
        different = [dict(comparator, period='first_half'), dict(comparator, source='Polymarket'),
                     dict(comparator, selection='Under 2.5', direction='under'), dict(comparator, line='3.5')]
        if any(contract_key(dict(row, fixture_id=control.fixture)) == scope for row in different):
            raise AssertionError('inexact_contract_equivalence')
    findings.extend(_durability(case, control, rows))
    return dict(schema='goal_roi_executable_offline_audit_v1', checks=findings,
                check_count=len(findings), permutation_seeds=[1789, 2207, 3739, 9923],
                state_counts=dict(Counter(x['antecedent'] for x in findings if 'antecedent' in x)))


def _durability(case, control, rows):
    planned = planned_records([case, control], rows)
    authority, capture = fixtures.authorities()
    plan = manifest.make_plan((slot for slot, _ in planned), sealed_at=LEFT - M,
                              catalog_sha256=manifest.sha('ignored-fixture-catalogue'), authority=authority)
    scope, _ = manifest.load_plan(plan, authority.public_key())
    ordered = sorted((row for _, row in planned), key=lambda row: row['slot'])
    with TemporaryDirectory() as work:
        root = Path(work)
        journal = manifest.Journal(root / 'recovered', scope, capture.public_key())
        publish = journal._publish

        def intermittent(fd, index, row):
            if index == 4:
                raise OSError('simulated disk exhaustion')
            publish(fd, index, row)

        with patch.object(journal, '_publish', side_effect=intermittent):
            try:
                journal.append_many(ordered, capture)
            except OSError:
                pass
            else:
                raise AssertionError('no_simulated_disk_failure')
        prefix = journal.read()
        if len(prefix) != 3:
            raise AssertionError('durable_prefix_lost')
        rebuilt = manifest.Journal(root / 'recovered', scope, capture.public_key(), witness=prefix[-1][0])
        head = rebuilt.append_many(ordered, capture)
        fresh = manifest.Journal(root / 'clean', scope, capture.public_key())
        if head != fresh.append_many(ordered, capture) or head != rebuilt.append_many(ordered, capture):
            raise AssertionError('non_idempotent_restart')

        file = root / 'recovered' / f'{len(ordered):08d}.json'
        file.unlink()
        try:
            manifest.Journal(root / 'recovered', scope, capture.public_key(), witness=head).read()
        except manifest.Invalid as exc:
            if str(exc) != 'witnessed_checkpoint_missing':
                raise
        else:
            raise AssertionError('truncation_accepted')

        file = root / 'clean' / '00000001.json'
        original = file.read_bytes()
        file.write_bytes(original.replace(b'"received"', b'"empty"', 1) if b'"received"' in original
                         else original.replace(b'"empty"', b'"received"', 1))
        try:
            fresh.read()
        except manifest.Invalid as exc:
            if str(exc) != 'invalid_signature':
                raise
        else:
            raise AssertionError('forged_capture_accepted')
    return [dict(scenario='restart_after_disk_failure', durable_prefix=3, identical_chain_head=True),
            dict(scenario='truncation_with_external_checkpoint', rejected=True),
            dict(scenario='forged_capture', rejected=True)]

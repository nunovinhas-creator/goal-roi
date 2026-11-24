"""New outcome-blind transfer challenge for autonomous trajectory discovery."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
import shutil
from types import SimpleNamespace

from scangoal import event_intelligence as events
from scangoal.discovery import dsl, miner_v2, researcher
from scangoal.discovery.replay import Budget
from scangoal.discovery.v32 import engine, language, mining, registry, riskset
from scangoal.discovery.v32.tape import Tape
from . import books, fixtures

M = timedelta(minutes=1)
BASE = datetime(2030, 2, 5, 18, tzinfo=timezone.utc)
TRAIN = 23801
HOLDOUTS = (59219, 80657)
FAMILIES = ('planted', 'alert_order_reversed', 'price_rises', 'late_alert', 'wrong_period', 'stale_marker')
GATES = dict(precision=.90, recall=.80, exact_witness_share=.75)


def atom(field, value, op='eq'):
    return dict(field=field, value=value, op=op)


ORACLE = language.canonical(dict(
    version=32, within_seconds=720,
    steps=[[atom('event_kind', 'quote'), atom('goals', 0)],
           [atom('event_kind', 'alert'), atom('source', 'Polymarket'), atom('volume_usd', 65000, 'ge')],
           [atom('event_kind', 'alert'), atom('direction', 'under'), atom('source', 'Betfair')],
           [atom('event_kind', 'goal')],
           [atom('event_kind', 'score'), atom('goals', 1)],
           [atom('event_kind', 'quote'), atom('source', 'Stake'), atom('point_odds', 2, 'lt'), atom('goals', 1)]],
    relations=[dict(atom('odds_delta_bp', 0, 'lt'), **{'from': 0, 'to': 5}),
               dict(atom('goal_delta', 0, 'gt'), **{'from': 0, 'to': 5})],
))


def bank(seed, *, start=BASE):
    rng = Random(seed)
    rows, targets, families = [], {}, {}
    for day in range(6):
        names = list(FAMILIES)
        rng.shuffle(names)
        for index, family in enumerate(names):
            name = f'lab-{seed}-{day}-{index}'
            at = start + timedelta(days=day) + index * 3 * M
            ko = at - 27 * M
            game = books.Game(name, events.iso(ko), name + '-home', name + '-away', f'league-{index % 2}')
            earlier, later = (at - 6 * M, at - 7 * M) if family == 'alert_order_reversed' else (at - 7 * M, at - 6 * M)

            def ev(when, kind='quote', **extra):
                return fixtures.typed_event(game, when, kind,
                                            **dict(dict(phase='live', minute=int((when - ko) / M)), **extra))

            def odds(value):
                return dict(odds=[value, value], price_low=float(value), price_high=float(value))

            low = ('1.83', '1.86', '1.90')[rng.randrange(3)]
            high = '1.70' if family == 'price_rises' else ('2.15', '2.22', '2.29')[rng.randrange(3)]
            old_epoch = 'first_half' if family == 'wrong_period' else 'regulation'
            early = ev(at - 9 * M, score=[0, 0], period=old_epoch, **odds(high))
            alert = ev(earlier, 'alert', source='Polymarket', volume_usd=67_000 + rng.randrange(20_000), score=[0, 0])
            if family == 'late_alert':
                alert['available_utc'] = events.iso(at + 8 * M)
            contradiction = ev(later, 'alert', source='Betfair', direction='under', selection='Under 2.5',
                               volume_usd=12_000 + rng.randrange(5_000), score=[0, 0])
            goal = ev(at - 4 * M, 'goal', score=[1, 0])
            marker = ev(at - 2 * M, 'score', score=[0, 1] if family == 'stale_marker' else [1, 0])
            terminal = ev(at, score=[1, 0], **odds(low))
            rows.extend((early, alert, contradiction, goal, marker, terminal))
            if family == 'stale_marker':
                # The later contradictory marker shares the terminal receipt.
                marker['emitted_utc'] = marker['available_utc'] = events.iso(at)
            families[game.fixture] = family
            if family == 'planted':
                targets[game.fixture] = (at, tuple((row['event_kind'], row['emitted_utc']) for row in
                                                   (early, alert, contradiction, goal, marker)))
    rng.shuffle(rows)
    return rows, targets, families


def _metrics(design, rule, truth, families):
    cohort = design.run(rule, Budget(100_000))
    by_fixture = {case.fixture: case for case in cohort.cases}
    overlap = set(by_fixture) & set(truth)
    terminal = {fixture for fixture in overlap if by_fixture[fixture].at == truth[fixture][0]}
    witness = {fixture for fixture in terminal if
               tuple((row['event_kind'], row['emitted_utc']) for row in by_fixture[fixture].witness[:-1]) == truth[fixture][1]}
    precision = len(terminal) / len(by_fixture) if by_fixture else 0.
    recall = len(terminal) / len(truth)
    rate = len(witness) / len(truth)
    return dict(cases=len(by_fixture), target_games=len(truth), exact_terminal=len(terminal), exact_witness=len(witness),
                wrong_time=len(overlap) - len(terminal), precision_at_t=precision, recall_at_t=recall,
                exact_witness_share=rate, false_motifs=dict(Counter(families[x] for x in by_fixture if x not in truth)),
                gate_passed=precision >= GATES['precision'] and recall >= GATES['recall']
                and rate >= GATES['exact_witness_share'],
                search_complete=not bool(cohort.counts['search_incomplete'] or cohort.counts['budget_exhausted']))


def _available_model():
    if shutil.which('llama-completion') is None:
        return None
    candidates = [file for root in (Path('models'), Path('/workspace/shared')) if root.is_dir()
                  for file in root.rglob('*.gguf') if file.is_file()]
    return min(candidates, default=None)


class LegacyProjection:
    """Keep typed rows in the new miner and pass legacy-shaped prefixes to v2."""

    def __init__(self, timeline):
        self.timeline = timeline
        self.eligible_anchors = timeline.eligible_anchors
        self.replay = SimpleNamespace(history={
            fixture: [row for row in history if self._supported(row)]
            for fixture, history in timeline.replay.history.items()
        })

    @staticmethod
    def _supported(row):
        return row['event_kind'] in {'quote', 'alert'} and isinstance(row['signal_type'], str)

    def context(self, index, window):
        original = self.timeline.context(index, window)
        return SimpleNamespace(anchor=original.anchor, at=original.at,
                               prefix=[row for row in original.prefix if self._supported(row)])


def _candidates(tape):
    trajectory = mining.Timeline(tape)
    legacy, _ = miner_v2.mine(LegacyProjection(trajectory), emissions=80_000)
    enriched, _ = mining.enrich(trajectory, emissions=80_000)
    lifted = [mining.Candidate(language.lift(row.rule), 'legacy_projection', row.mechanism, row.refutation)
              for row in legacy]
    return trajectory, _unique([*enriched, *lifted])


def _unique(candidates):
    return list({registry.hypothesis_id(row.rule):
                 dict(id=registry.hypothesis_id(row.rule), rule=row.rule, origin=row.origin)
                 for row in candidates}.values())


def run(*, try_model=True):
    training_rows, training_truth, training_families = bank(TRAIN)
    training_tape = Tape(training_rows, BASE + timedelta(days=7))
    development = riskset.RiskSet(training_tape)
    try:
        frozen = engine.discover(training_tape, work=800_000, emissions=160_000)
        before = dict(status='completed', candidates=len(frozen['investigations']))
    except dsl.Invalid as exc:
        if str(exc) != 'invalid_literal':
            raise
        before = dict(status='invalid_literal_on_mixed_trajectory', candidates=None)
    trajectory, predictions = _candidates(training_tape)
    model = _available_model() if try_model else None
    model_status = 'local_model_or_runtime_unavailable'
    optional = []
    if model is not None:
        raw, model_status = researcher.run_local(model, mining.prompt(trajectory))
        if raw is not None:
            candidates, _ = researcher.proposals(raw, language=language, candidate=mining.Candidate)
            optional = _unique(candidates)
    holdouts = []
    for index, seed in enumerate(HOLDOUTS, 1):
        rows, target, families = bank(seed, start=BASE + timedelta(days=30 * index))
        tape = Tape(rows, BASE + timedelta(days=30 * index + 7))
        holdouts.append((seed, riskset.RiskSet(tape), target, families))

    def evaluate(candidates):
        scored = []
        for row in candidates:
            metrics = _metrics(development, row['rule'], training_truth, training_families)
            scored.append(dict(id=row['id'], origin=row['origin'], steps=len(row['rule']['steps']),
                               development=metrics,
                               holdout=[dict(seed=seed, **_metrics(design, row['rule'], t, f))
                                        for seed, design, t, f in holdouts]))
        ranked = sorted(scored, key=lambda row: (-row['development']['exact_terminal'],
                                                -row['development']['exact_witness'],
                                                -row['development']['precision_at_t'], row['id']))
        return dict(candidates=len(scored), selected_on_development=ranked[0] if ranked else None,
                    all_candidates=scored,
                    holdout_passes=sum(all(value['gate_passed'] for value in row['holdout']) for row in scored),
                    distinct_lengths=sorted({row['steps'] for row in scored}))

    oracle = dict(id=registry.hypothesis_id(ORACLE),
                  development=_metrics(development, ORACLE, training_truth, training_families),
                  holdout=[dict(seed=seed, **_metrics(design, ORACLE, t, f)) for seed, design, t, f in holdouts])
    det = evaluate(predictions)
    llm = dict(status=model_status, model_basename=model.name if model else None,
               metrics=evaluate(optional) if optional else None)
    generalizes = bool(det['selected_on_development']
                       and all(row['gate_passed'] and row['search_complete']
                               for row in det['selected_on_development']['holdout']))
    return dict(schema='offline_unseen_trajectory_transfer_v1', version=language.VERSION,
                training_seed=TRAIN, holdout_seeds=list(HOLDOUTS), gates=GATES,
                frozen_entrypoint=before,
                deterministic=det, llm=llm, oracle=oracle, transfer_gate=generalizes,
                outcomes_visible_to_miner=False, confirmatory=False)

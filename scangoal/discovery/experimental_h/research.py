"""Optional real local inference over the same typed observations."""

from pathlib import Path
import re
import shutil

from .. import dsl, researcher
from ..v32 import language, mining
from .miner import WINDOW
from .observed import Observed
from .rules import FIELDS, RELATIONS, Meter

SAMPLE_FIELDS = ('event_kind', 'source', 'direction', 'period', 'price_kind', 'point_odds', 'volume_usd',
                 'volume_cumulative_usd', 'biggest_ticket_usd', 'n_bets', 'goals', 'score_state', 'minute')


def available_model():
    if shutil.which('llama-completion') is None:
        return None
    roots = (Path('models'), Path('/workspace/shared'), Path.home() / '.cache/llama.cpp')
    return min((path for root in roots if root.is_dir() for path in root.rglob('*.gguf') if path.is_file()), default=None)


def prompt(tape):
    instructions = (
        'Find recurring observable trajectories. Return one JSON object {"proposals":[...]}. '
        'Each entry has rule, alternative (rule or null), mechanism and refutation. A rule has version=32, '
        'within_seconds, steps (2-8 lists of conjunctive predicates), and relations. A predicate has field, op, value. '
        'Relations add zero-based from and to with from < to. Use eq/ne, or lt/le/gt/ge for numbers. '
        'Steps are strictly ordered, ending at an eligible quote receipt. Consider non-adjacent relations and '
        'contrast cases. Price and volume movements require exact contract identity. '
        'No results, actions or profit claims. Return at most five proposals.\n'
    )
    meter = Meter(100_000)
    histories = [(*decision.within(WINDOW)[-7:], decision.quote) for decision in Observed(tape).decisions(meter)]
    histories.sort(key=lambda path: (-len(path), path[-1]['available_utc'], path[-1]['fixture_id']))
    fields = {name: primitive.kind for name, primitive in FIELDS.items()}
    relations = {name: primitive.kind for name, primitive in RELATIONS.items()}
    data = dict(fields=fields, relations=relations, observed_sequences=[])

    for history in histories[:8]:
        sample = []
        for row in history:
            point = {field: FIELDS[field].read(row) for field in SAMPLE_FIELDS}
            sample.append({field: value for field, value in point.items()
                           if value is not None and (not isinstance(value, str)
                                                    or re.fullmatch(r'[\w .\-/]{1,36}', value))})
        data['observed_sequences'].append(sample)
        if len(instructions.encode() + dsl.encoded(data)) > researcher.MAX_PROMPT_BYTES:
            data['observed_sequences'].pop()
            break
    return instructions + dsl.encoded(data).decode()


def investigate(tape, model):
    if model is None:
        return dict(status='local_model_or_runtime_unavailable', candidates=None, rejected=[])
    output, status = researcher.run_local(model, prompt(tape))
    if output is None:
        return dict(status=status, candidates=None, rejected=[])
    candidates, rejected = researcher.proposals(output, language=language, candidate=mining.Candidate)
    return dict(status=status, candidates=[dict(id=item.id, rule=item.rule, alternative=item.alternative,
                                              mechanism=item.mechanism, refutation=item.refutation)
                                        for item in candidates], rejected=rejected)

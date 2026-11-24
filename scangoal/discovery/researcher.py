"""Optional bounded, local llama.cpp researcher and strict proposal boundary."""

from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryFile

from . import dsl, miner

MAX_PROMPT_BYTES = 4300
MAX_REPLY_BYTES = 12_000
MAX_PROPOSALS = 5
MAX_TOKENS = 1536
CONTEXT = 6144
TIMEOUT_SECONDS = 45
# Explanations describe a mechanism. Return, certainty, validation and betting
# instructions are outside the researcher's role in either language.
CLAIM = re.compile(r'garant|guarant|certific|rentáv|rentab|lucr|profit|\broi\b|\byield|\bedge\b|value.?bet|'
                   r'validat|validad|comprovad|proven|risk.?free|sem risco|sure.?(win|bet|thing)|certain.?win|'
                   r'aposte|apostar|bet now|place (a )?bet|stake now|\bwin(s|ning)? every', re.I)
SENTINEL = b'[end of text]'
# Output grammar for llama.cpp. It fixes the envelope only; the DSL validator
# remains the only authority on rule content.
REPLY_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['proposals'],
    'properties': {'proposals': {'type': 'array', 'maxItems': MAX_PROPOSALS, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['rule', 'alternative', 'mechanism', 'refutation'],
        'properties': {'rule': {'type': 'object'}, 'alternative': {'anyOf': [{'type': 'object'}, {'type': 'null'}]},
                       'mechanism': {'type': 'string', 'maxLength': 280},
                       'refutation': {'type': 'string', 'maxLength': 280}}}}},
}
SHORT_TEXT = re.compile(r'^[^\x00-\x1f\x7f<>]{8,280}$')


def prompt(replay, memory):
    """Offer diverse observed inputs without raw Telegram text or free-form identity."""
    fields = {name: p.kind for name, p in dsl.FIELDS.items()}
    relations = {name: p.kind for name, p in dsl.RELATIONS.items()}
    sample_fields = ('source', 'phase', 'direction', 'market_key', 'period', 'line',
                     'point_odds', 'volume_usd', 'minute', 'goals')
    examples = []
    distinct = set()
    anchors = replay.anchors
    for index in range(8):
        if not anchors:
            break
        at, event = anchors[(2 * index + 1) * len(anchors) // 16]
        anchor, prior = replay.prefix(event, at, miner.DISCOVERY_WINDOW)
        if anchor['fixture_id'] in distinct:
            continue
        distinct.add(anchor['fixture_id'])
        points = []
        for e in [*prior[-2:], anchor]:
            point = {name: dsl.FIELDS[name].read(e) for name in sample_fields}
            points.append({k: v for k, v in point.items()
                           if v is not None and (not isinstance(v, str) or re.fullmatch(r'[\w .\-/]{1,36}', v))})
        if len(points) > 1:
            examples.append(points)
    instructions = (
        'Investigate temporal associations. Return exactly one JSON object {"proposals":[...]}. '
        'Each item has rule, alternative (rule or null), mechanism, refutation. '
        'A rule is {"version":1,"within_seconds":21600,"steps":[[predicate],...],"relations":[link,...]}. '
        'Each predicate is {"field":name,"op":"eq","value":literal}. A link adds integer '
        '"from" and "to" step indexes with from < to. Ops eq/ne, and gt/ge/lt/le for numbers. '
        'Steps are strictly emitted in order, last is the exposure opportunity. Use 2–8 steps. '
        'Use prior_alerts, prior_sources and prior_compatible_quotes only on the anchor. Try different time, market, price and source explanations. Supply a falsification and a '
        'contrasting relation with the same anchor conditions. Describe hypotheses only. FT, outcomes, '
        'code, actions and causal/profit claims are outside the DSL. Maximum 5 proposals. '
        'Use only fields and numeric/typed relations listed below. odds_delta_bp is change in basis '
        'points on the same point-price contract. odds_velocity_bp_hour divides that change by elapsed hours.'
    )
    root = dict(fields=fields, relations=relations, observed_sequences=examples,
                prior_rejection_counts=dict(Counter(r['reason'] for r in memory if r['status'] != 'experimental').most_common(6)))
    while len(instructions.encode()) + len(dsl.encoded(root)) + 1 > MAX_PROMPT_BYTES and root['observed_sequences']:
        root['observed_sequences'].pop()
    result = instructions + '\n' + dsl.encoded(root).decode()
    if len(result.encode()) > MAX_PROMPT_BYTES:
        raise ValueError('model_prompt_budget_exceeded')
    return result


def run_local(model, request):
    """Use an installed local model through llama-completion. One attempt."""
    if len(request.encode()) > MAX_PROMPT_BYTES:
        return None, 'prompt_budget_exceeded'
    engine = shutil.which('llama-completion')
    model = Path(model).resolve()
    if engine is None or not model.is_file() or model.suffix.lower() != '.gguf':
        return None, 'local_model_or_runtime_unavailable'
    with NamedTemporaryFile() as prompt_file, TemporaryFile() as output:
        prompt_file.write(request.encode())
        prompt_file.flush()
        try:
            subprocess.run([engine, '-m', str(model), '-f', prompt_file.name,
                            '-n', str(MAX_TOKENS), '-c', str(CONTEXT), '-t', '2',
                            '--temp', '0', '--seed', '82', '--no-display-prompt', '--log-disable',
                            '--json-schema', dsl.encoded(REPLY_SCHEMA).decode()],
                           check=True, timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
                           stdout=output, stderr=subprocess.DEVNULL,
                           env={k: os.environ[k] for k in ('PATH', 'HOME', 'LD_LIBRARY_PATH') if k in os.environ})
        except subprocess.TimeoutExpired:
            return None, 'local_model_timeout'
        except (OSError, subprocess.CalledProcessError):
            return None, 'local_model_failed'
        output.seek(0)
        return output.read(MAX_REPLY_BYTES + 1), 'returned'


def rejection(value, reason):
    content = value if isinstance(value, bytes) else dsl.encoded(value)
    return dict(id='rejected:' + hashlib.sha256(content).hexdigest(), status='rejected',
                origin='llm', reason=reason)


def proposals(raw):
    """Validate before use, preserving failures as fingerprints, never free-form actions."""
    if not isinstance(raw, bytes) or len(raw) > MAX_REPLY_BYTES:
        # For a truncated response only the retained bytes identify the failure.
        return [], [rejection(raw[:MAX_REPLY_BYTES + 1] if isinstance(raw, bytes) else b'', 'model_response_budget')]
    body = raw.rstrip()
    # llama.cpp may terminate stdout with its end-of-generation marker. Markdown
    # fences and any other surrounding text remain invalid. Failures keep the
    # fingerprint of the original bytes.
    body = body[:-len(SENTINEL)] if body.endswith(SENTINEL) else raw
    try:
        obj = dsl.strict_json(body)
        if not isinstance(obj, dict) or set(obj) != {'proposals'} or not isinstance(obj['proposals'], list):
            raise dsl.Invalid('model_response_schema')
        if len(obj['proposals']) > MAX_PROPOSALS:
            raise dsl.Invalid('model_proposal_budget')
    except dsl.Invalid as exc:
        return [], [rejection(raw, str(exc))]
    accepted, refusals, seen = [], [], set()
    for entry in obj['proposals']:
        try:
            if not isinstance(entry, dict) or set(entry) != {'rule', 'alternative', 'mechanism', 'refutation'}:
                raise dsl.Invalid('model_proposal_schema')
            rule = dsl.canonical(entry['rule'])
            if dsl.identifier(rule) in seen:
                raise dsl.Invalid('duplicate_model_proposal')
            seen.add(dsl.identifier(rule))
            alt = dsl.canonical(entry['alternative']) if entry['alternative'] is not None else None
            if alt is not None and (alt['steps'][-1] != rule['steps'][-1]
                                    or alt['within_seconds'] != rule['within_seconds'] or alt == rule):
                raise dsl.Invalid('invalid_falsifying_alternative')
            for key in ('mechanism', 'refutation'):
                val = entry[key]
                if not isinstance(val, str) or not SHORT_TEXT.fullmatch(val) or CLAIM.search(val):
                    raise dsl.Invalid('invalid_research_explanation')
            accepted.append(miner.Candidate(rule, 'llm', entry['mechanism'], entry['refutation'], alt))
        except dsl.Invalid as exc:
            # A valid JSON object can contain finite-but-out-of-range floats.
            # If canonical JSON itself is invalid, refer to the full raw reply.
            try:
                document = dsl.encoded(entry)
            except (ValueError, RecursionError):
                document = raw
            refusals.append(rejection(document, str(exc)))
    return accepted, refusals

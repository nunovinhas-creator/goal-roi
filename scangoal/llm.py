"""Investigação de hipóteses com Cloudflare Workers AI.

O pedido distingue descoberta histórica de resultados prospetivos e contém apenas agregados.
O modelo propõe investigações; o motor determina evidência e certificação. As propostas são
validadas e registadas após a resposta. Cada chamada real exige confirmação Free e reserva remota.
"""
import fcntl
import hashlib
import json, math, os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import brain, chronology, equivalence, insights
from .eligibility import game_identity
from .llm_budget import BudgetUnavailable, GitBudget
from .results_bsd import load_json
from .store import data_dir

MODELS = [m for m in (os.environ.get('LLM_MODEL', ''), '@cf/meta/llama-3.3-70b-instruct-fp8-fast', '@cf/meta/llama-3.1-8b-instruct-fast') if m]
MIN_INTERVAL_MIN = int(os.environ.get('LLM_MIN_INTERVAL_MIN', '55'))
MAX_PER_RUN, MAX_CONDITIONS, MIN_SUPPORT = 5, 4, 5          # sem limite diário nem total de hipóteses: só o orçamento de neurónios limita
DAILY_NEURONS = 8000                                        # margem sobre os 10 000 gratuitos
PRICES = {'@cf/meta/llama-3.3-70b-instruct-fp8-fast': (26668, 204805), '@cf/meta/llama-3.1-8b-instruct-fast': (4119, 34868)}   # neurónios por milhão de tokens (entrada, saída)
MODES = ('novas', 'refinar', 'evitar', 'notas')
MAX_RESPONSE_TOKENS = 900
REQUEST_TIMEOUT_SECONDS = 60
UTC_DAY_GUARD = timedelta(minutes=5)
CHAT_TEMPLATE_ALLOWANCE = 256
SYSTEM_PROMPT = 'És um analista de apostas desportivas rigoroso. Respondes só com JSON válido.'
CLAIM_WORDS = re.compile(r'\b(?:certificad[ao]s?|validad[ao]s?|rentáve(?:l|is)|lucro comprovado|vantagem comprovada)\b', re.I)
UTC = timezone.utc
_REMOTE_RESERVATION = object()


class LLMError(RuntimeError):
    pass


def request(account, key, model, prompt, *, _reservation=None):
    if _reservation is not _REMOTE_RESERVATION:
        raise LLMError('unverified remote reservation')
    url = 'https://api.cloudflare.com/client/v4/accounts/%s/ai/v1/chat/completions' % account
    body = json.dumps({'model': model, 'temperature': 0.7, 'max_tokens': MAX_RESPONSE_TOKENS, 'messages': [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': prompt}]}).encode('utf-8')
    req = Request(url, data=body, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as r:
            data = json.loads(r.read(2_000_000))
        return str(data['choices'][0]['message']['content']), data.get('usage') or {}
    except HTTPError as exc:
        raise LLMError('HTTP %d' % exc.code) from None       # nunca imprimir URL, corpo ou cabeçalhos
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
        raise LLMError('unavailable or invalid response') from None


def vocabulary(signals):
    """{família: {característica: {valor: n.º de sinais}}} — só o que se conhece no momento do sinal."""
    vocab = {}
    for b in signals:
        fam = vocab.setdefault(insights.family(b), {})
        for dim, val in brain.atoms(b, set()):
            if dim in brain.RESEARCH_DIMENSIONS:
                fam.setdefault(dim, {})
                fam[dim][val] = fam[dim].get(val, 0) + 1
    return vocab


def opportunity_prompt(report):
    """Bounded, numeric-only context. No match names, raw signals or post-hoc ROI."""
    if not isinstance(report, dict) or report.get('status') != 'exploratory_only':
        return []
    rows = report.get('ranking')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return []
    candidate = rows[0]
    if (candidate.get('rule_id') != 'polymarket_pre_over25_share_lt80_total_lt10000_v1'
            or candidate.get('certified') is not False
            or report.get('certification') != 'not_activated'
            or report.get('statistical_claim') != 'none'):
        return []
    n = candidate.get('ft_confirmed')
    settled, admitted = candidate.get('settled'), candidate.get('forward_games')
    if (not all(type(v) is int for v in (n, settled, admitted))
            or not 0 <= n <= settled <= admitted):
        return []
    # No ROI / upside anecdotes in the prompt until 30 independent forward games.
    prefix = 'Polymarket PRE Over 2.5, quota <80%, valor <$10K:'
    if n < 30:
        return [prefix + ' %d jogos futuros com FT confirmado; amostra insuficiente (<30), SEM conclusão de ROI.' % n,
                'Não refines a regra congelada com estes resultados, nem a declares lucrativa.']
    if candidate.get('evidence_status') == 'amostra_concentrada':
        return [prefix + ' %d jogos com FT confirmado; amostra concentrada, SEM conclusão de ROI.' % n]
    roi = candidate.get('roi_odds_minus_2pct')
    baseline = report.get('baseline')
    benchmark = baseline.get('roi_odds_minus_2pct') if isinstance(baseline, dict) else None
    if (type(roi) not in (int, float) or not math.isfinite(roi)
            or type(benchmark) not in (int, float) or not math.isfinite(benchmark)):
        return []
    return [prefix + ' %d jogos futuros com FT confirmado; ROI teórico com odd ×0,98 = %+.3f por jogo.' % (n, roi),
            'Comparador sobreposto, não independente: ROI teórico de Polymarket PRE Over 2.5 = %+.3f.' % benchmark,
            'Comparação apenas exploratória; nenhuma vantagem certificada, nenhum envio de alertas novo.']


def opportunity_digest(now):
    """Local-only reading; never loads Telegram credentials or calls Cloudflare.

    Fail closed if no activation, corrupt data or mismatched chronology.
    Skipping the digest must never affect LLM hypothesis registration.
    """
    from . import opportunity_ranking, report
    from scripts.opportunity_forward_audit import PROTOCOL, read_json, sourced_results
    try:
        directory = data_dir()
        state = read_json(directory / 'notify_state.json', {})
        activation = ((state.get('migrations') or {}).get('polymarket_over25_v1') or {}).get('at_utc')
        if not activation:
            return None
        protocol = read_json(PROTOCOL, {})
        observed = {}
        messages, _, _, _, _ = report.load(result_observations=observed)
        results = sourced_results(observed, directory)
        return opportunity_ranking.build(
            messages, results, protocol, activation_utc=activation, now=now,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, ArithmeticError):
        print('LLM_OPPORTUNITY_CONTEXT_SKIPPED')
        return None


REJECTION_CODES = ('malformed_response', 'malformed', 'vocabulary', 'support',
                   'duplicate', 'equivalent', 'invalid_parent', 'over_limit', 'invalid_note')


def build_prompt(vocab, top, existing, mode='novas', weak=(), track=None, opportunity=None, rejections=None):
    lines = ['Medimos sinais de apostas de futebol de um bot. Cada sinal tem características. Uma hipótese é uma família de sinal',
             'mais 1 a %d condições (característica = valor). Queremos hipóteses NOVAS que possam acertar acima do que a odd exige.' % MAX_CONDITIONS, '',
             'Características disponíveis por família (valor: número de sinais):']
    for fam, dims in sorted(vocab.items()):
        lines.append('## ' + fam)
        for dim, vals in sorted(dims.items()):
            shown = sorted(vals.items(), key=lambda kv: -kv[1])[:12]
            lines.append('- %s: %s' % (dim, '; '.join('%s (%d)' % kv for kv in shown)))
    lines += ['', 'Histórico para descoberta — resultados descritivos, inclusive anteriores ao registo.',
              'Hipóteses com mais dados (resultados, acerto, taxa necessária):']
    for h in top[:25]:
        lines.append('- %s -> %d resultados, acerto %s, precisa %s' % (h['id'], h['n'], h['hit_rate'], h['break_even']))
    if weak:
        lines += ['', 'Hipóteses com pior desempenho até agora:']
        lines += ['- %s -> %d resultados, acerto %s, precisa %s' % (h['id'], h['n'], h['hit_rate'], h['break_even']) for h in weak[:12]]
    if track and track.get('llm'):
        lines += ['', 'Evidência prospetiva descritiva após o registo das propostas anteriores.',
                  'Só uma unidade comprometida por encontro. Uma taxa de acerto acima de 50 % não prova lucro.',
                  'Nenhuma está confirmada pelo balanço.']
        for h in track['llm']:
            economic = h.get('economics') or {}
            confirmed = economic.get('results_confirmed', h['games'])
            if confirmed >= track['min_results']:
                roi = economic.get('roi_odds_minus_2pct', h.get('roi'))
                lines.append('- %s -> %d resultados confirmados, %d perdas imputadas, ROI exploratório %s'
                             % (h['id'], confirmed, economic.get('imputed_losses', 0),
                                '—' if roi is None else ('%+.2f' % roi)))
    if rejections:
        feedback = ['%s=%d' % (code, rejections[code]) for code in REJECTION_CODES
                    if type(rejections.get(code)) is int and rejections[code] > 0]
        if feedback:
            lines.append('Rejeições da avaliação anterior (motivos e contagens) — ' + ', '.join(feedback))
    digest = opportunity_prompt(opportunity)
    if digest:
        lines += ['', 'Evidência prospetiva PR #55 (filtro pré-registado, sem certificação):', *digest]
    if mode == 'notas':
        lines += ['', 'Escreve até 3 observações curtas e prudentes sobre o que estes números sugerem. Cada observação refere UMA hipótese da lista,',
                  'copiando o identificador exatamente como está escrito (o texto antes de "->"). Não inventes números; diz quando a amostra é pequena.',
                  'Responde só com JSON neste formato:', '{"notes":[{"id":"Betfair | Seleção = empate","note":"frase curta"}]}']
        return '\n'.join(lines)
    task = {'novas': 'Propõe até %d hipóteses novas, diferentes das listadas, que possam acertar acima do que a odd exige.',
            'refinar': 'Propõe até %d versões mais específicas das hipóteses listadas. Indica parent_id exatamente como na lista e acrescenta UMA condição.',
            'evitar': 'Propõe até %d hipóteses que esperas que acertem ABAIXO do necessário (situações a evitar), para confirmarmos com dados novos.'}[mode] % MAX_PER_RUN
    lines += ['', 'Já existem %d hipóteses; não repitas nenhuma das listadas.' % existing, task,
              'Usa APENAS famílias, características e valores exatamente como escritos acima.',
              'Prefere combinações com lógica de futebol ou de mercado (ex.: volume forte num empate em jogo empatado) e com pelo menos %d encontros.' % MIN_SUPPORT,
              'Se existir Seleção exata, distingue linhas concretas como Over 2.5 de Over 3.5 ou Under 2.5; não as confundas com Mercado genérico.',
              'ROI ou acertos históricos só servem para propor testes prospetivos; não declares lucro comprovado nem sugiras alertas automáticos.',
              'Responde só com JSON neste formato:']
    example = {'family': 'Betfair', 'conditions': [['Seleção', 'empate'], ['Minuto', '61 em diante']],
               'reason': 'frase curta'}
    if mode == 'refinar':
        example['parent_id'] = 'Betfair | Seleção = empate'
    lines.append(json.dumps({'hypotheses': [example]}, ensure_ascii=False))
    return '\n'.join(lines)


def clean(text, limit=200):
    return re.sub(r'[^\w\s.,;:%()×+\-/áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]', '', str(text or ''))[:limit].strip()


def response_items(raw, field):
    if not isinstance(raw, str):
        return None
    match = re.search(r'\{.*\}', raw, re.S)
    try:
        obj = json.loads(match[0]) if match else None
    except ValueError:
        return None
    if isinstance(obj, dict) and set(obj) == {field} and isinstance(obj[field], list):
        return obj[field]
    return None


def rejected(reasons, reason):
    if reasons is not None:
        reasons[reason] = reasons.get(reason, 0) + 1


def accept_notes(raw, known, reasons=None):
    """Accept only observations anchored in a known hypothesis."""
    items = response_items(raw, 'notes')
    if items is None:
        rejected(reasons, 'malformed_response')
        return []
    out, seen = [], set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'id', 'note'}
                or not isinstance(item['id'], str) or not isinstance(item['note'], str)):
            rejected(reasons, 'malformed')
            continue
        hid = item['id']
        note = clean(item['note'], 240)
        if hid not in known or hid in seen or not note or CLAIM_WORDS.search(note) or re.search(r'\d', note):
            rejected(reasons, 'invalid_note')
            continue
        if len(out) >= 3:
            rejected(reasons, 'over_limit')
            continue
        out.append({'id': hid, 'note': note})
        seen.add(hid)
    return out


def upper_tokens(prompt):
    return len(prompt.encode('utf-8')) + len(SYSTEM_PROMPT.encode('utf-8')) + CHAT_TEMPLATE_ALLOWANCE


def cost_for(model, input_tokens, output_tokens):
    pin, pout = PRICES[model]
    return math.ceil((input_tokens * pin + output_tokens * pout) / 100000) / 10


def reserve(model, prompt):
    return cost_for(model, upper_tokens(prompt), MAX_RESPONSE_TOKENS)


def measured_cost(model, usage, prompt, reserved):
    tokens = (usage.get('prompt_tokens'), usage.get('completion_tokens')) if isinstance(usage, dict) else ()
    if len(tokens) != 2 or not all(type(v) is int and v >= 0 for v in tokens):
        return reserved
    tin, tout = tokens
    if tin == 0 or tin > upper_tokens(prompt) or tout > MAX_RESPONSE_TOKENS:
        return reserved
    return cost_for(model, tin, tout)


def refinement_parent(item, conditions, registry, parents):
    pid = item.get('parent_id')
    if not isinstance(pid, str) or pid not in parents:
        return None
    parent = registry.get(pid)
    if not parent or parent.get('family') != item['family'] or not brain.research_conditions(parent):
        return None
    parent_conditions = {tuple(c) for c in parent['conditions']}
    return pid if parent_conditions < conditions and len(conditions - parent_conditions) == 1 else None


def accept(raw, vocab, registry, support=None, expect='acima', redundancy=None, reasons=None, *,
           mode='novas', parents=()):
    """Validate each proposal independently and count every rejection."""
    items = response_items(raw, 'hypotheses')
    if items is None:
        rejected(reasons, 'malformed_response')
        return [], 1
    allowed = {'family', 'conditions', 'reason'} | ({'parent_id'} if mode == 'refinar' else set())
    good, bad, seen = [], 0, set()
    for item in items:
        if (not isinstance(item, dict) or not set(item) <= allowed
                or not isinstance(item.get('family'), str) or not isinstance(item.get('conditions'), list)
                or not all(isinstance(c, list) and len(c) == 2 and all(isinstance(v, str) for v in c)
                           for c in item['conditions'])
                or ('reason' in item and not isinstance(item['reason'], str))):
            bad += 1
            rejected(reasons, 'malformed')
            continue

        fam, conds = item['family'], [tuple(c) for c in item['conditions']]
        fields = vocab.get(fam, {})
        dimensions = {d for d, _ in conds}
        why = None
        parent_id = None
        if (not 1 <= len(conds) <= MAX_CONDITIONS or len(dimensions) != len(conds)
                or not all(d in brain.RESEARCH_DIMENSIONS and v in fields.get(d, {}) for d, v in conds)):
            why = 'vocabulary'
        else:
            hid = brain.hyp_id(fam, conds)
            if mode == 'refinar':
                parent_id = refinement_parent(item, set(conds), registry, parents)
                if parent_id is None:
                    why = 'invalid_parent'
            if why is None:
                if support is not None and support(fam, set(conds)) < MIN_SUPPORT:
                    why = 'support'
                elif hid in registry or hid in seen:
                    why = 'duplicate'
                elif len(good) >= MAX_PER_RUN:
                    why = 'over_limit'
                elif redundancy is not None and redundancy.reason(fam, conds):
                    why = 'equivalent'
        if why is not None:
            bad += 1
            rejected(reasons, why)
            continue
        seen.add(hid)
        if redundancy is not None:
            redundancy.register(fam, conds, hid)
        reason = clean(item.get('reason'))
        entry = {'family': fam, 'conditions': [list(c) for c in sorted(conds)], 'source': 'llm',
                 'reason': '' if CLAIM_WORDS.search(reason) else reason, 'expect': expect}
        if parent_id is not None:
            entry['parent_id'] = parent_id
        good.append((hid, entry))
    return good, bad


def save_state(path, value):
    """Replace state atomically so a restart sees a complete reservation or result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def reset_budget(state, at):
    day = at.date().isoformat()
    if state.get('day') != day:
        state.update(day=day, neurons_today=0.0, accepted_today=0, calls_today=0)


def request_window(at):
    next_day = datetime.combine(at.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return next_day - at > UTC_DAY_GUARD


def run(call=None, now=None, env=None, *, budget=None):
    env = os.environ if env is None else env
    if env.get('GOAL_ROI_LLM_FREE_CONFIRMED') != 'true':
        print('LLM_SKIPPED_FREE_UNCONFIRMED')
        return None
    account = env.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
    key = env.get('MASTER_BRAIN_LLM_API_KEY', '').strip()
    if not account or not key or not re.fullmatch(r'[0-9a-f]{32}', account):
        print('LLM_NOT_CONFIGURED')
        return None
    live = call is None or call is request
    if budget is None and env.get('LLM_BUDGET_MODE') == 'git' and env.get('GITHUB_REF_NAME') == 'main':
        budget = GitBudget(account)
    if live and not isinstance(budget, GitBudget):
        print('LLM_SKIPPED_BUDGET_UNVERIFIED')
        return None
    directory = data_dir()
    lock_id = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / ('goal-roi-llm-%d' % os.getuid())
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    with (lock_dir / lock_id).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _run_locked(request if call is None else call, account, key, directory, now, budget, live=live)


def _run_locked(call, account, key, directory, now, budget, *, live):
    from . import report

    def clock():
        return now.astimezone(UTC) if now is not None else datetime.now(UTC)

    if now is not None and now.utcoffset() is None:
        raise ValueError('A UTC-aware evaluation clock is required')
    started = clock()
    spath = directory / 'llm_state.json'
    rpath, npath = directory / 'hypotheses.json', directory / 'llm_notes.json'
    # An unreadable existing budget state cannot safely authorise a new request.
    if spath.exists():
        state = json.loads(spath.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or (state.get('last_run_utc') and not chronology.parse_utc(state['last_run_utc'])):
            raise ValueError('Invalid stored LLM accounting')
    else:
        state = {'last_run_utc': None, 'runs': 0, 'accepted': 0, 'rejected': 0}
    last = chronology.parse_utc(state.get('last_run_utc'))
    if last is not None and started - last < timedelta(minutes=MIN_INTERVAL_MIN):
        print('LLM_SKIPPED_TOO_SOON')
        return state
    stored_day = state.get('day')
    if stored_day is not None and (not isinstance(stored_day, str) or stored_day > started.date().isoformat()):
        raise ValueError('Invalid stored LLM accounting day')
    reset_budget(state, started)
    balance = state.get('neurons_today')
    if (type(balance) not in (int, float) or not math.isfinite(balance)
            or not 0 <= balance < DAILY_NEURONS):
        print('LLM_SKIPPED_DAILY_BUDGET')
        return state
    rep = report.build(now=started)
    signals = chronology.as_of_view(report.LAST['research_signals'], started)
    registry = brain.load_registry(rpath)
    vocab = vocabulary(signals)
    mode = MODES[state.get('runs', 0) % len(MODES)]
    have = [(game_identity(b), insights.family(b), set(brain.atoms(b, set()))) for b in signals]
    support = lambda fam, conds: len({mid for mid, family, atoms in have if family == fam and conds <= atoms})
    top = rep['brain']['top']
    weak = sorted((h for h in top if h['prob_above'] is not None), key=lambda h: h['prob_above'])[:12]
    outcome, good, notes, used, why = 'no_data', [], [], None, {}
    if vocab:
        digest = opportunity_digest(started)
        prompt = build_prompt(vocab, top, len(registry), mode, weak, rep['brain'].get('track'), digest,
                              state.get('last_rejection_reasons'))
        raw = None
        outcome = 'skipped_no_affordable_model'
        for model in dict.fromkeys(MODELS):
            if model not in PRICES:
                continue
            at = clock()
            reset_budget(state, at)
            if not request_window(at):
                outcome = 'skipped_utc_boundary'
                break
            reserved = reserve(model, prompt)
            before = state.get('neurons_today', 0)
            if round(before + reserved, 1) > DAILY_NEURONS:
                continue
            if budget is not None:
                try:
                    if not budget.reserve(at.date(), round(reserved * 10), round(DAILY_NEURONS * 10)):
                        continue
                except BudgetUnavailable:
                    outcome = 'budget_unverified'
                    break
                # An interruption after publication leaves the remote charge occupied. Local
                # telemetry is optional for correctness and never releases the remote hold.
            state.update(neurons_today=round(before + reserved, 1),
                         calls_today=state.get('calls_today', 0) + 1, last_run_utc=at.isoformat(),
                         last_outcome='attempt_reserved')
            save_state(spath, state)
            ready = clock()
            if ready.date() != at.date() or not request_window(ready):
                outcome = 'skipped_utc_boundary'
                break
            try:
                if live:
                    response = call(account, key, model, prompt, _reservation=_REMOTE_RESERVATION)
                else:
                    response = call(account, key, model, prompt)
            except LLMError as exc:
                outcome = 'failed: %s' % exc
                if str(exc) in ('HTTP 402', 'HTTP 429'):
                    break
                continue
            raw, usage = response if isinstance(response, tuple) and len(response) == 2 else (response, {})
            used = model
            state['neurons_today'] = round(before + measured_cost(model, usage, prompt, reserved), 1)
            save_state(spath, state)
            outcome = 'ok'
            break

        if used is not None:
            accepted_at = clock()
            if mode == 'notas':
                notes = accept_notes(raw, {h['id'] for h in top[:25]}, why)
                if notes:
                    old = load_json(npath, [])
                    stamped = [dict(n, at_utc=accepted_at.isoformat(timespec='seconds'), model=used) for n in notes]
                    save_state(npath, (stamped + old)[:40])
            else:
                parents = {h['id'] for h in top[:25] if h['id'] in registry
                           and (chronology.born(registry[h['id']]) or accepted_at) < accepted_at}
                good, _ = accept(raw, vocab, registry, support, 'abaixo' if mode == 'evitar' else 'acima',
                                 equivalence.Redundancy(signals, registry), why, mode=mode, parents=parents)
                if good:
                    provenance = {'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                                  'response_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                                  'report_generated_at_utc': rep.get('generated_at_utc'), 'registry_size': len(registry)}
                    for hid, entry in good:
                        registry[hid] = dict(entry, born_utc=accepted_at.isoformat(), model=used, mode=mode,
                                             provenance=provenance)
                    save_state(rpath, registry)
            if why.get('malformed_response'):
                outcome = 'invalid_response'

    bad = sum(why.values())
    cumulative = dict(state.get('rejection_reasons') or {})
    for reason, count in why.items():
        cumulative[reason] = cumulative.get(reason, 0) + count
    state.update(last_run_utc=clock().isoformat(timespec='seconds'), runs=state.get('runs', 0) + 1,
                 accepted=state.get('accepted', 0) + len(good), rejected=state.get('rejected', 0) + bad,
                 last_outcome=outcome, last_model=used, last_accepted=len(good), last_rejected=bad,
                 last_mode=mode, last_notes=len(notes), rejected_equivalent=state.get('rejected_equivalent', 0) + why.get('equivalent', 0),
                 last_rejected_equivalent=why.get('equivalent', 0), last_rejection_reasons=why,
                 rejection_reasons=cumulative, accepted_today=state.get('accepted_today', 0) + len(good))
    save_state(spath, state)
    print('LLM_OK', json.dumps({k: state[k] for k in ('last_mode', 'last_outcome', 'last_model', 'last_accepted', 'last_rejected', 'last_notes', 'neurons_today')}))
    return state


def main():
    try:
        run()
    except Exception as exc:      # o relatório continua sem o modelo; só o tipo do erro, nunca segredos
        print('LLM_FAILED', type(exc).__name__)
    sys.exit(0)


if __name__ == '__main__':
    main()

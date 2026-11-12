"""Hipóteses propostas por um modelo de linguagem (Cloudflare Workers AI, o mesmo serviço do asianodds-h1-lab).

O modelo só recebe números agregados (vocabulário de características e tabela das hipóteses atuais). Não recebe mensagens.
O que devolve é tratado como dados: cada proposta só entra se usar características que existem; nada do texto é executado.
Como o modelo viu resultados, as propostas entram com origem 'llm' e só contam sinais POSTERIORES ao registo.
Segredos: CLOUDFLARE_ACCOUNT_ID e MASTER_BRAIN_LLM_API_KEY (só no cabeçalho; nunca impressos). Sem eles, não faz nada.
Orçamento (plano gratuito: 10 000 neurónios por dia, reposto às 00:00 UTC): uma chamada por hora, em quatro modos que
rodam (novas, refinar, evitar, notas); pára quando a estimativa do dia chega a 8 000 neurónios.
Limites: 5 propostas por chamada, 30 por dia e 300 no total; uma proposta precisa de pelo menos 5 sinais já existentes.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import brain, insights
from .results_bsd import load_json
from .store import data_dir

MODELS = [m for m in (os.environ.get('LLM_MODEL', ''), '@cf/meta/llama-3.3-70b-instruct-fp8-fast', '@cf/meta/llama-3.1-8b-instruct-fast') if m]
MIN_INTERVAL_MIN = int(os.environ.get('LLM_MIN_INTERVAL_MIN', '55'))
MAX_PER_RUN, MAX_PER_DAY, MAX_TOTAL, MAX_CONDITIONS, MIN_SUPPORT = 5, 30, 300, 3, 5
DAILY_NEURONS = 8000                                        # margem sobre os 10 000 gratuitos
PRICES = {'@cf/meta/llama-3.3-70b-instruct-fp8-fast': (26668, 204805), '@cf/meta/llama-3.1-8b-instruct-fast': (4119, 34868)}   # neurónios por milhão de tokens (entrada, saída)
MODES = ('novas', 'refinar', 'evitar', 'notas')
UTC = timezone.utc


class LLMError(RuntimeError):
    pass


def request(account, key, model, prompt):
    url = 'https://api.cloudflare.com/client/v4/accounts/%s/ai/v1/chat/completions' % account
    body = json.dumps({'model': model, 'temperature': 0.7, 'max_tokens': 900, 'messages': [
        {'role': 'system', 'content': 'És um analista de apostas desportivas rigoroso. Respondes só com JSON válido.'},
        {'role': 'user', 'content': prompt}]}).encode('utf-8')
    req = Request(url, data=body, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=60) as r:
            data = json.loads(r.read(2_000_000))
        return str(data['choices'][0]['message']['content']), data.get('usage') or {}
    except HTTPError as exc:
        raise LLMError('HTTP %d' % exc.code) from None       # nunca imprimir URL, corpo ou cabeçalhos
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
        raise LLMError('unavailable or invalid response') from None


def vocabulary(signals):
    """{família: {característica: {valor: n.º de sinais}}} — só o que se conhece no momento do sinal."""
    multi, earlier, vocab = brain.multi_matches(signals), brain.earlier_index(signals), {}
    for b in signals:
        fam = vocab.setdefault(insights.family(b), {})
        for dim, val in brain.atoms(b, multi, earlier):
            if dim not in brain.POST:
                fam.setdefault(dim, {})
                fam[dim][val] = fam[dim].get(val, 0) + 1
    return vocab


def build_prompt(vocab, top, existing, mode='novas', weak=()):
    lines = ['Medimos sinais de apostas de futebol de um bot. Cada sinal tem características. Uma hipótese é uma família de sinal',
             'mais 1 a %d condições (característica = valor). Queremos hipóteses NOVAS que possam acertar acima do que a odd exige.' % MAX_CONDITIONS, '',
             'Características disponíveis por família (valor: número de sinais):']
    for fam, dims in sorted(vocab.items()):
        lines.append('## ' + fam)
        for dim, vals in sorted(dims.items()):
            shown = sorted(vals.items(), key=lambda kv: -kv[1])[:12]
            lines.append('- %s: %s' % (dim, '; '.join('%s (%d)' % kv for kv in shown)))
    lines += ['', 'Hipóteses já em teste com mais dados (resultados, acerto, taxa necessária):']
    for h in top[:25]:
        lines.append('- %s -> %d resultados, acerto %s, precisa %s' % (h['id'], h['n'], h['hit_rate'], h['break_even']))
    if weak:
        lines += ['', 'Hipóteses com pior desempenho até agora:']
        lines += ['- %s -> %d resultados, acerto %s, precisa %s' % (h['id'], h['n'], h['hit_rate'], h['break_even']) for h in weak[:12]]
    if mode == 'notas':
        lines += ['', 'Escreve até 3 observações curtas e prudentes sobre o que estes números sugerem. Cada observação refere UMA hipótese da lista,',
                  'copiando o identificador exatamente como está escrito (o texto antes de "->"). Não inventes números; diz quando a amostra é pequena.',
                  'Responde só com JSON neste formato:', '{"notes":[{"id":"Betfair | Seleção = empate","note":"frase curta"}]}']
        return '\n'.join(lines)
    task = {'novas': 'Propõe até %d hipóteses novas, diferentes das listadas, que possam acertar acima do que a odd exige.',
            'refinar': 'Escolhe as hipóteses com melhor desempenho e propõe até %d versões mais específicas, juntando UMA condição que faça sentido.',
            'evitar': 'Propõe até %d hipóteses que esperas que acertem ABAIXO do necessário (situações a evitar), para confirmarmos com dados novos.'}[mode] % MAX_PER_RUN
    lines += ['', 'Já existem %d hipóteses; não repitas nenhuma das listadas.' % existing, task,
              'Usa APENAS famílias, características e valores exatamente como escritos acima.',
              'Prefere combinações com lógica de futebol ou de mercado (ex.: volume forte num empate em jogo empatado) e com pelo menos %d sinais.' % MIN_SUPPORT,
              'Responde só com JSON neste formato:',
              '{"hypotheses":[{"family":"Betfair","conditions":[["Seleção","empate"],["Minuto","61 em diante"]],"reason":"frase curta"}]}']
    return '\n'.join(lines)


def clean(text, limit=200):
    return re.sub(r'[^\w\s.,;:%()×+\-/áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]', '', str(text or ''))[:limit].strip()


def accept_notes(raw, known):
    """Observações do modelo: só entram as que referem uma hipótese existente. O texto é limpo; os números mostrados vêm dos dados."""
    m = re.search(r'\{.*\}', raw or '', re.S)
    try:
        items = json.loads(m[0])['notes'] if m else []
    except (ValueError, KeyError, TypeError):
        items = []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and it.get('id') in known and clean(it.get('note')) and len(out) < 3:
            out.append({'id': it['id'], 'note': clean(it['note'], 240)})
    return out


def neurons(model, usage, prompt, raw):
    """Estimativa do custo da chamada; se a resposta não trouxer contagem de tokens, estima-se pelo tamanho do texto."""
    pin, pout = PRICES.get(model, max(PRICES.values()))
    tin = usage.get('prompt_tokens') if isinstance(usage, dict) else None
    tout = usage.get('completion_tokens') if isinstance(usage, dict) else None
    tin, tout = tin if isinstance(tin, int) else len(prompt) // 3, tout if isinstance(tout, int) else len(raw or '') // 3
    return round((tin * pin + tout * pout) / 1e6, 1)


def accept(raw, vocab, registry, support=None, expect='acima'):
    """Texto do modelo -> propostas válidas [(id, entrada)] e número de rejeitadas. Tudo o que não bate com o vocabulário é rejeitado."""
    m = re.search(r'\{.*\}', raw or '', re.S)
    try:
        items = json.loads(m[0])['hypotheses'] if m else []
    except (ValueError, KeyError, TypeError):
        items = []
    good, bad, seen = [], 0, set()
    for it in items if isinstance(items, list) else []:
        try:
            fam, conds = it['family'], [(str(c[0]), str(c[1])) for c in it['conditions']]
        except (KeyError, TypeError, IndexError):
            bad += 1
            continue
        dims = [c[0] for c in conds]
        ok = (fam in vocab and 1 <= len(conds) <= MAX_CONDITIONS and len(set(dims)) == len(dims)
              and all(d in vocab[fam] and v in vocab[fam][d] for d, v in conds))
        hid = brain.hyp_id(fam, conds) if ok else None
        if ok and support is not None and support(fam, set(conds)) < MIN_SUPPORT:
            ok = False                                       # poucos sinais assim: nunca chegaria a ter amostra
        if not ok or hid in registry or hid in seen or len(good) >= MAX_PER_RUN:
            bad += 1
            continue
        seen.add(hid)
        good.append((hid, {'family': fam, 'conditions': [list(c) for c in sorted(conds)], 'source': 'llm', 'reason': clean(it.get('reason')), 'expect': expect}))
    return good, bad


def run(call=request, now=None, env=None):
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    account, key = env.get('CLOUDFLARE_ACCOUNT_ID', '').strip(), env.get('MASTER_BRAIN_LLM_API_KEY', '').strip()
    if not account or not key or not re.fullmatch(r'[0-9a-f]{32}', account):
        print('LLM_NOT_CONFIGURED')
        return None
    spath, rpath, npath = data_dir() / 'llm_state.json', data_dir() / 'hypotheses.json', data_dir() / 'llm_notes.json'
    state = load_json(spath, {'last_run_utc': None, 'runs': 0, 'accepted': 0, 'rejected': 0})
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(minutes=MIN_INTERVAL_MIN):
        print('LLM_SKIPPED_TOO_SOON')
        return state
    day = now.date().isoformat()
    if state.get('day') != day:
        state.update(day=day, neurons_today=0.0, accepted_today=0, calls_today=0)
    if state.get('neurons_today', 0) >= DAILY_NEURONS:
        print('LLM_SKIPPED_DAILY_BUDGET')
        return state
    from . import report
    rep = report.build()
    signals, registry = report.LAST['signals'], brain.load_registry(rpath)
    total_llm = sum(1 for h in registry.values() if h.get('source') == 'llm')
    vocab = vocabulary(signals)
    mode = MODES[state.get('runs', 0) % len(MODES)]
    if mode != 'notas' and (total_llm >= MAX_TOTAL or state.get('accepted_today', 0) >= MAX_PER_DAY):
        mode = 'notas'                                        # limite de hipóteses atingido: o modelo só comenta
    multi, earlier = brain.multi_matches(signals), brain.earlier_index(signals)
    have = [(insights.family(b), set(brain.atoms(b, multi, earlier))) for b in signals]
    support = lambda fam, conds: sum(1 for f, at in have if f == fam and conds <= at)
    top = rep['brain']['top']
    weak = sorted((h for h in top if h['prob_above'] is not None), key=lambda h: h['prob_above'])[:12]
    outcome, good, bad, used, notes, cost = 'ok', [], 0, None, [], 0.0
    if not vocab:
        outcome = 'no_data'
    else:
        prompt, raw = build_prompt(vocab, top, len(registry), mode, weak), None
        for model in MODELS:
            try:
                res = call(account, key, model, prompt)
                raw, usage = res if isinstance(res, tuple) else (res, {})
                used, cost = model, neurons(model, usage, prompt, raw)
                break
            except LLMError as exc:
                outcome = 'failed: %s' % exc
        if raw is not None:
            outcome = 'ok'
            if mode == 'notas':
                notes = accept_notes(raw, {h['id'] for h in top})
                if notes:
                    old = load_json(npath, [])
                    stamped = [dict(n, at_utc=now.isoformat(timespec='seconds'), model=used) for n in notes]
                    npath.write_text(json.dumps((stamped + old)[:40], indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
            else:
                good, bad = accept(raw, vocab, registry, support, 'abaixo' if mode == 'evitar' else 'acima')
                good = good[:max(0, min(MAX_TOTAL - total_llm, MAX_PER_DAY - state.get('accepted_today', 0)))]
                for hid, entry in good:
                    registry[hid] = dict(entry, born_utc=now.isoformat(timespec='seconds'), model=used, mode=mode)
                if good:
                    rpath.write_text(json.dumps(registry, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    state.update(last_run_utc=now.isoformat(timespec='seconds'), runs=state.get('runs', 0) + 1, accepted=state.get('accepted', 0) + len(good),
                 rejected=state.get('rejected', 0) + bad, last_outcome=outcome, last_model=used, last_accepted=len(good), last_rejected=bad,
                 last_mode=mode, last_notes=len(notes), neurons_today=round(state.get('neurons_today', 0) + cost, 1),
                 accepted_today=state.get('accepted_today', 0) + len(good), calls_today=state.get('calls_today', 0) + (1 if used else 0))
    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(json.dumps(state, indent=1, sort_keys=True) + '\n', encoding='utf-8')
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

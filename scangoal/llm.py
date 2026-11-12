"""Hipóteses propostas por um modelo de linguagem (Cloudflare Workers AI, o mesmo serviço do asianodds-h1-lab).

O modelo só recebe números agregados (vocabulário de características e tabela das hipóteses atuais). Não recebe mensagens.
O que devolve é tratado como dados: cada proposta só entra se usar características que existem; nada do texto é executado.
Como o modelo viu resultados, as propostas entram com origem 'llm' e só contam sinais POSTERIORES ao registo.
Segredos: CLOUDFLARE_ACCOUNT_ID e MASTER_BRAIN_LLM_API_KEY (só no cabeçalho; nunca impressos). Sem eles, não faz nada.
Orçamento: uma chamada por dia, no máximo 5 propostas por chamada e 80 no total.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import brain, insights
from .results_bsd import load_json
from .store import data_dir

MODELS = [m for m in (os.environ.get('LLM_MODEL', ''), '@cf/meta/llama-3.3-70b-instruct-fp8-fast', '@cf/meta/llama-3.1-8b-instruct-fast') if m]
MIN_INTERVAL_H = int(os.environ.get('LLM_MIN_INTERVAL_H', '20'))
MAX_PER_RUN, MAX_TOTAL, MAX_CONDITIONS = 5, 80, 3
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
        return str(data['choices'][0]['message']['content'])
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


def build_prompt(vocab, top, existing):
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
    lines += ['', 'Já existem %d hipóteses; não repitas nenhuma das listadas.' % existing,
              'Propõe até %d hipóteses novas. Usa APENAS famílias, características e valores exatamente como escritos acima.' % MAX_PER_RUN,
              'Prefere combinações com lógica de futebol ou de mercado (ex.: volume forte num empate em jogo empatado) e com sinais suficientes.',
              'Responde só com JSON neste formato:',
              '{"hypotheses":[{"family":"Betfair","conditions":[["Seleção","empate"],["Minuto","61 em diante"]],"reason":"frase curta"}]}']
    return '\n'.join(lines)


def accept(raw, vocab, registry):
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
        if not ok or hid in registry or hid in seen or len(good) >= MAX_PER_RUN:
            bad += 1
            continue
        seen.add(hid)
        reason = re.sub(r'[^\w\s.,;:%()×+\-/áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]', '', str(it.get('reason') or ''))[:200].strip()
        good.append((hid, {'family': fam, 'conditions': [list(c) for c in sorted(conds)], 'source': 'llm', 'reason': reason}))
    return good, bad


def run(call=request, now=None, env=None):
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    account, key = env.get('CLOUDFLARE_ACCOUNT_ID', '').strip(), env.get('MASTER_BRAIN_LLM_API_KEY', '').strip()
    if not account or not key or not re.fullmatch(r'[0-9a-f]{32}', account):
        print('LLM_NOT_CONFIGURED')
        return None
    spath, rpath = data_dir() / 'llm_state.json', data_dir() / 'hypotheses.json'
    state = load_json(spath, {'last_run_utc': None, 'runs': 0, 'accepted': 0, 'rejected': 0})
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(hours=MIN_INTERVAL_H):
        print('LLM_SKIPPED_TOO_SOON')
        return state
    from . import report
    rep = report.build()
    signals, registry = report.LAST['signals'], brain.load_registry(rpath)
    total_llm = sum(1 for h in registry.values() if h.get('source') == 'llm')
    vocab = vocabulary(signals)
    outcome, good, bad, used = 'ok', [], 0, None
    if total_llm >= MAX_TOTAL or not vocab:
        outcome = 'cap_reached' if vocab else 'no_data'
    else:
        prompt, raw = build_prompt(vocab, rep['brain']['top'], len(registry)), None
        for model in MODELS:
            try:
                raw, used = call(account, key, model, prompt), model
                break
            except LLMError as exc:
                outcome = 'failed: %s' % exc
        if raw is not None:
            outcome = 'ok'
            good, bad = accept(raw, vocab, registry)
            good = good[:MAX_TOTAL - total_llm]
            for hid, entry in good:
                registry[hid] = dict(entry, born_utc=now.isoformat(timespec='seconds'), model=used)
            if good:
                rpath.write_text(json.dumps(registry, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    state.update(last_run_utc=now.isoformat(timespec='seconds'), runs=state.get('runs', 0) + 1, accepted=state.get('accepted', 0) + len(good),
                 rejected=state.get('rejected', 0) + bad, last_outcome=outcome, last_model=used, last_accepted=len(good), last_rejected=bad)
    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(json.dumps(state, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print('LLM_OK', json.dumps({k: state[k] for k in ('last_outcome', 'last_model', 'last_accepted', 'last_rejected')}))
    return state


def main():
    try:
        run()
    except Exception as exc:      # o relatório continua sem o modelo; só o tipo do erro, nunca segredos
        print('LLM_FAILED', type(exc).__name__)
    sys.exit(0)


if __name__ == '__main__':
    main()

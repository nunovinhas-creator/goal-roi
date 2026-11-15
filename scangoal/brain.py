"""Cérebro: gera hipóteses a partir das características dos sinais e testa-as com regras fixas. Sem LLM, sem rede.

Disciplina (fixada a 12/11/2026):
- O gerador só olha para QUANTOS sinais têm cada combinação de características, nunca para os resultados.
  Por isso as hipóteses do gerador podem ser avaliadas em todos os dados.
- Hipóteses de origem 'manual' ou 'llm' (escritas por quem já viu resultados) só contam sinais POSTERIORES ao registo.
- O registo (data/hypotheses.json) só cresce; uma hipótese nunca é apagada nem reescrita.
- Unidade estatística (15/11/2026, PR 2): o JOGO. Sinais repetidos na mesma aposta (jogo, mercado, seleção) contam uma vez;
  apostas diferentes no mesmo jogo repartem um voto (insights.Votes). Todos os limiares contam jogos independentes,
  nunca sinais: estados, fasquia corrigida, quadro, agenda, walk-forward e balanço das propostas.
- Estados (limiares de entrada): em observação (<30 jogos); promissora (>=30 e prob. >= 95 %, sem correção: pode ser acaso);
  forte (>=100 e prob. corrigida pelo número de hipóteses testadas); confirmada (>=300, idem); abandonada (>=30 e prob. <= 10 %).
- Estabilidade (15/11/2026, PR 2): o estado é reconstituído jogo a jogo, pela ordem em que os resultados ficaram conhecidos.
  Histerese: uma promissora só sai abaixo de 90 %; uma abandonada só sai acima de 20 %; uma validada mantém-se enquanto a
  prob. não cair abaixo da fasquia com o dobro do erro admitido. Confirmação: cada mudança exige que a nova condição se mantenha
  em CONFIRM_STEPS jogos seguidos. Uma oscilação de um jogo não muda o estado; uma deterioração sustentada muda.
- "Acrescenta ao tipo" (prob_adds): probabilidade de a hipótese ter mais vantagem do que os restantes sinais do mesmo tipo.
  Só informa; não muda o estado.
- "Seguir o cérebro" (walk_forward): cada aposta é decidida só com os resultados que já se conheciam nesse momento.
- Validade cronológica (15/11/2026, PR 3, regras chronological_prospective_v1; ver audits/pr3-chronological-validity.md):
  * um resultado só é conhecido a partir da primeira observação PROVADA pelo sistema (chronology.result_known), nunca por uma
    aproximação; sem prova, só conta "agora", depois de todos os provados;
  * uma hipótese não toma decisões antes de existir: a evidência anterior ao registo (born_utc) junta-se num único passo
    "no registo"; sem born_utc válido não há estados;
  * fasquia corrigida cronológica: em cada passo usa-se 1 − α/m(t), com m(t) = hipóteses registadas que já tinham 30 jogos
    conhecidos nesse instante. Hipóteses acrescentadas depois não reescrevem decisões anteriores. Bonferroni não assume
    independência entre hipóteses (é válido sob qualquer dependência; com hipóteses sobrepostas é conservador);
  * forte/confirmada exigem ainda evidência PROSPETIVA: >= 30 jogos cujo sinal o sistema leu depois do registo, com
    prob. >= 95 % (manter: >= 90 %). Evidência histórica, mesmo corrigida, é exploratória: não demonstra capacidade preditiva.
- Características que só se conhecem depois do primeiro sinal (reforço, movimento da odd...) tornam a hipótese
  "informativa": explica, mas não dá para apostar no momento do sinal, e não entra na lista "a seguir".
"""
import hashlib, itertools, json
from datetime import datetime, timezone

from . import chronology, insights
from .parser import competition_kind
from .eligibility import eligible, exclusion_reasons, game_identity

MIN_SUPPORT = 8          # sinais com a combinação para a hipótese ser registada
MAX_CONDITIONS = 3       # o gerador combina até 3 características; o registo não tem limite de tamanho
HINT_AT, STRONG_AT = 30, 100                       # jogos independentes
BOARD_MIN, BOARD_GOOD, BOARD_BAD = 8, 0.80, 0.20   # quadro em destaque: mínimo de jogos e limiares de probabilidade
TRACK_MIN = 10           # jogos para uma hipótese entrar no balanço das propostas
STATUS_RULES = 'chronological_prospective_v1'     # muda quando as regras de estado mudam (migração silenciosa no Telegram)
FWER_ALPHA = 0.05        # erro de família (Bonferroni cronológico) para forte/confirmada
VALIDATED = ('forte', 'confirmada')
PROSPECTIVE_MIN = HINT_AT   # jogos prospetivos (sinal lido depois do registo) exigidos a uma tática validada
PROMOTE_AT, KEEP_PROMISING = 0.95, 0.90            # entrar em promissora / manter-se (histerese: o dobro do erro admitido)
ABANDON_AT, KEEP_ABANDONED = 0.10, 0.20            # entrar em abandonada / manter-se
CONFIRM_STEPS = 2        # jogos seguidos em que a nova condição se tem de manter para o estado mudar
POST = {'Outros sinais no mesmo jogo', 'Reforço', 'Odd depois do sinal', 'Quota depois do sinal', 'Velocidade do reforço'}


def earlier_index(signals):
    """Por jogo: (hora, família) de cada sinal, para saber se já havia outro tipo de sinal antes."""
    idx = {}
    for b in signals:
        idx.setdefault(b['match_id'], []).append((b['date_utc'], insights.family(b)))
    return idx


def atoms(b, multi, earlier=None):
    """Características de um sinal: as dimensões fixas da análise e mais algumas próprias do cérebro."""
    b['kind'] = competition_kind(b.get('league'), b.get('teams'))
    out = insights.dimensions(b, multi)
    fam = insights.family(b)
    if b.get('league'):
        out.append(('Liga', b['league']))
    if earlier is not None:
        before = any(d < b['date_utc'] and f != fam for d, f in earlier.get(b['match_id'], ()))
        out.append(('Sinal anterior de outro tipo no mesmo jogo', 'sim' if before else 'não'))
    s = b.get('score_at_signal')
    if s:
        gap = abs(s[0] - s[1])
        out += [('Marcador no momento', 'empatado' if gap == 0 else 'diferença de 1 golo' if gap == 1 else 'diferença de 2 ou mais golos'),
                ('Golos até ao sinal', 'nenhum' if sum(s) == 0 else '1 ou 2' if sum(s) <= 2 else '3 ou mais')]
    if b.get('minute') is not None:
        out.append(('Minuto', 'até ao 30' if b['minute'] <= 30 else '31 a 60' if b['minute'] <= 60 else '61 em diante'))
    if b['type'] == 'BETFAIR':
        out.append(('Seleção', {'1': 'equipa da casa', 'X': 'empate', '2': 'equipa de fora'}.get(b.get('selection'))))
        if b.get('sel_volume') is not None:
            out.append(('Volume na seleção', '10 mil $ ou mais' if b['sel_volume'] >= 10000 else 'menos de 10 mil $'))
    if b['type'] in ('STAKE_LOAD', 'POLYMARKET'):
        if b.get('total') is not None:
            out.append(('Valor da aposta', '10 mil $ ou mais' if b['total'] >= 10000 else 'menos de 10 mil $'))
        if b.get('n_bets') is not None:
            out.append(('Número de apostas', 'uma' if b['n_bets'] == 1 else 'várias'))
    out = [(d, v) for d, v in out if v]
    hour = datetime.fromisoformat(b['date_utc']).hour
    out.append(('Hora (UTC)', 'madrugada' if hour < 6 else 'manhã' if hour < 12 else 'tarde' if hour < 18 else 'noite'))
    if b['type'] == 'BETFAIR':
        if b['n_signals'] > 1 and b.get('share_last') is not None and b['share_last'] != b['share']:
            out.append(('Quota depois do sinal', 'subiu' if b['share_last'] > b['share'] else 'desceu'))
        if b.get('x4_min') is not None:
            out.append(('Velocidade do reforço', '×4 em 30 min ou menos' if b['x4_min'] <= 30 else '×4 em mais de 30 min'))
    return out


def hyp_id(family, conds):
    return family + ' | ' + ' & '.join('%s = %s' % c for c in sorted(conds))


def multi_matches(signals):
    by = {}
    for b in signals:
        by.setdefault(b['match_id'], set()).add(insights.family(b))
    return {m for m, t in by.items() if len(t) > 1}


def candidates(signals):
    """Combinações de 1 a MAX_CONDITIONS características por família, com o número de sinais que as têm. Não lê resultados."""
    signals = [b for b in signals if eligible(b)]
    multi, support, earlier = multi_matches(signals), {}, earlier_index(signals)
    for b in signals:
        fam, at = insights.family(b), sorted(set(atoms(b, multi, earlier)))
        for k in range(1, MAX_CONDITIONS + 1):
            for c in itertools.combinations(at, k):
                if len({x[0] for x in c}) == k:   # uma característica só entra uma vez
                    support[(fam, c)] = support.get((fam, c), 0) + 1
    return support


def update_registry(signals, registry, now=None):
    """Acrescenta ao registo as combinações com apoio suficiente. Devolve quantas entraram."""
    now = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    added = 0
    ranked = sorted(candidates(signals).items(), key=lambda kv: (len(kv[0][1]), -kv[1], hyp_id(*kv[0])))   # simples primeiro, depois as mais frequentes
    support = dict(ranked)
    for (fam, conds), n in ranked:
        hid = hyp_id(fam, conds)
        if n < MIN_SUPPORT or hid in registry:
            continue
        if len(conds) > 1 and any(support.get((fam, sub)) == n for sub in itertools.combinations(conds, len(conds) - 1)):
            continue   # apanha exatamente os mesmos sinais que uma combinação mais simples: não acrescenta nada
        registry[hid] = {'family': fam, 'conditions': [list(c) for c in sorted(conds)], 'born_utc': now, 'source': 'gerador'}
        added += 1
    return added


def look_share(games):
    """Parte de α gasta no olhar com `games` jogos: STRONG_AT/(n(n+1)). Soma 1 sobre n >= STRONG_AT (série telescópica).

    O estado é reavaliado a cada jogo novo (olhares repetidos). Sem esta repartição, uma hipótese sem vantagem atravessa a
    fasquia de um só olhar muito mais vezes do que α (demonstrado em tests/test_chronological_validity.py, Scenarios).
    """
    n = max(games, STRONG_AT)
    return STRONG_AT / (n * (n + 1))


def corrected_bar(tested, games=STRONG_AT):
    """Fasquia das validadas: Bonferroni sobre `tested` hipóteses E sobre os olhares (union bound, qualquer dependência)."""
    return 1 - FWER_ALPHA * look_share(games) / max(1, tested)


def prospective_ok(state, prospective):
    """Condição prospetiva de uma tática validada. prospective=None: sem porta (só chamadas diretas à máquina de estados)."""
    if prospective is None:
        return True
    games, prob = prospective
    keep = state in VALIDATED
    return games >= PROSPECTIVE_MIN and prob is not None and prob >= (KEEP_PROMISING if keep else PROMOTE_AT)


def passes_bar(prob, tail, bar, keep=False):
    """prob/tail contra a fasquia (keep: o dobro do erro admitido). Com a cauda não arredondada quando existe."""
    allowed = (1 - bar) * (2 if keep else 1)
    return tail <= allowed if tail is not None else prob >= 1 - allowed


def status_of(n, prob, corrected_bar, min_settled, prospective=None, tail=None):
    """Estado pelos limiares de entrada, sem memória (n = jogos independentes). É o alvo a partir de "em observação".

    prospective = (jogos prospetivos, prob. prospetiva): forte/confirmada exigem-na (ver prospective_ok).
    tail = cauda posterior não arredondada (insights.tail_below), usada contra a fasquia das validadas.
    """
    if prob is None or n < HINT_AT:
        return 'em observação'
    if prob <= ABANDON_AT:
        return 'abandonada'
    if n >= STRONG_AT and passes_bar(prob, tail, corrected_bar) and prospective_ok('em observação', prospective):
        return 'confirmada' if n >= min_settled else 'forte'
    return 'promissora' if prob >= PROMOTE_AT else 'em observação'


def next_status(state, games, prob, corrected_bar, min_settled, prospective=None, tail=None):
    """Estado-alvo dado o estado atual (histerese): entrar exige o limiar de entrada; manter-se, só o limiar de saída."""
    if prob is None or games < HINT_AT:
        return 'em observação'
    if state == 'abandonada' and prob <= KEEP_ABANDONED:
        return 'abandonada'
    if prob <= ABANDON_AT:
        return 'abandonada'
    if games >= STRONG_AT and passes_bar(prob, tail, corrected_bar, keep=state in VALIDATED) and prospective_ok(state, prospective):
        return 'confirmada' if games >= min_settled else 'forte'
    if prob >= (KEEP_PROMISING if state in ('promissora', 'forte', 'confirmada') else PROMOTE_AT):
        return 'promissora'
    return 'em observação'


_LAST = datetime.max.replace(tzinfo=timezone.utc)


def ordered_games(match):
    """Jogos com resultado decidido, pela ordem em que o sistema PROVADAMENTE os conheceu: [(conhecido, jogo, sinais)].

    Um jogo fica conhecido quando todas as suas apostas o estão (o último resultado). Sem hora provada (desconhecida ou
    contraditória) o jogo vai para o fim, com conhecido = None ("agora"). Empates na mesma hora: pela hora do jogo (o primeiro
    sinal) e depois pela identidade. A ordem física dos dados nunca conta. Fail-closed: sem jogo comprovável não há observação.
    """
    games = {}
    for b in match:
        key = game_identity(b)
        if b['w'] is not None and b['w'] != 0 and key is not None:
            games.setdefault(key, []).append(b)
    out = []
    for key, rows in games.items():
        times = [chronology.result_known(b) for b in rows]
        known = None if any(t is None for t in times) else max(times)
        first = min((chronology.signal_time(b) or _LAST) for b in rows)
        out.append((known, first, key, rows))
    out.sort(key=lambda g: (g[0] or _LAST, g[0] is None, g[1], g[2]))
    return [(known, key, rows) for known, _, key, rows in out]


def game_ids(match):
    return {game_identity(b) for b in match if b['w'] is not None and b['w'] != 0 and game_identity(b) is not None}


def game_set(match):
    """Impressão digital do conjunto de jogos com resultado decidido (hipóteses com o mesmo conjunto são a mesma evidência)."""
    ids = sorted(game_ids(match))
    return hashlib.sha256('\n'.join(ids).encode('utf-8')).hexdigest()[:16] if ids else None


def prospective_game(rows, born):
    """Jogo prospetivo para uma hipótese: todos os seus sinais foram lidos pelo sistema DEPOIS do registo."""
    if born is None:
        return False
    for b in rows:
        t = chronology.decision_time(b)
        if t is None or t <= born:
            return False
    return True


def prospective_summary(match, born):
    """Evidência PROSPETIVA de uma hipótese: só sinais que o sistema leu depois do registo (None sem registo válido).

    Jogos, acertos e a mesma evidência (um voto por jogo); a prob. só a partir de BOARD_MIN jogos, para poupar tempo.
    """
    if born is None:
        return None
    rows = [b for b in match if prospective_game([b], born)]
    v = insights.Votes()
    for b in rows:
        v.add(b)
    h, m, be, n = v.totals()
    done = [b for b in rows if b['w'] is not None]
    pls = [(b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w']) for b in done if b['odds']]
    return {'signals': len(rows), 'games': n, 'hits': round(h, 2), 'misses': round(m, 2),
            'prob_above': insights.evidence(h, m, be)['prob_above'] if n >= BOARD_MIN else None,
            'roi': round(sum(pls) / len(pls), 4) if pls else None}


def game_steps(match, born=None, *, prospective_from=None):
    """Evidência acumulada jogo a jogo, pela ordem provada em que os resultados ficaram conhecidos.

    -> [(jogos, prob_above, hora ISO ou None, extra)], só a partir de HINT_AT jogos (antes disso: "em observação").
    born: registo da hipótese. Os passos anteriores juntam-se num só passo "no registo" (a hipótese não decidia antes de
    existir). extra = {'at': datetime ou None, 'prospective': (jogos, prob) dos jogos lidos depois de prospective_from}.
    """
    votes, pro, raw = insights.Votes(), insights.Votes(), []
    for k, (known, _, rows) in enumerate(ordered_games(match), 1):
        for b in rows:
            votes.add(b)
            if prospective_from is not None and prospective_game(rows, prospective_from):
                pro.add(b)
        if k >= HINT_AT:
            h, m, be, n = votes.totals()
            pn, pp = len(pro.m), None
            if n >= STRONG_AT and pn >= PROSPECTIVE_MIN:   # a porta prospetiva só interessa às validadas
                ph, pm, pbe, pn = pro.totals()
                pp = insights.evidence(ph, pm, pbe)['prob_above']
            tail = insights.tail_below(h, m, be) if n >= STRONG_AT else None
            raw.append([n, insights.evidence(h, m, be)['prob_above'], known, (pn, pp), tail])
    if born is not None:
        early = [s for s in raw if s[2] is not None and s[2] < born]
        if early:
            rest = [s for s in raw if s[2] is None or s[2] >= born]
            early[-1][2] = born          # a evidência disponível no registo, como um só passo
            raw = early[-1:] + rest
    return [(n, p, chronology.iso(at), {'at': at, 'prospective': pro_, 'tail': tail}) for n, p, at, pro_, tail in raw]


def stable_status(steps, corrected_bar, min_settled):
    """Máquina de estados com histerese e confirmação. -> (estado, estado pendente ou None, mudanças).

    corrected_bar: número (fasquia fixa) ou função hora -> (fasquia, hipóteses testadas), avaliada em cada passo: a fasquia
    de uma decisão é a que existia nesse instante e fica registada na mudança. Passos com extra['prospective'] aplicam a
    porta prospetiva às validadas.
    """
    state, pending, run, changes = 'em observação', None, 0, []
    for step in steps:
        games, prob, when = step[:3]
        extra = step[3] if len(step) > 3 else {}
        if callable(corrected_bar):
            tested = corrected_bar(extra.get('at'))[1]
            bar = globals()['corrected_bar'](tested, games)
        else:
            bar, tested = corrected_bar, None
        pro = extra.get('prospective')
        target = next_status(state, games, prob, bar, min_settled, pro, extra.get('tail'))
        if target == state:
            pending, run = None, 0
            continue
        run = run + 1 if target == pending else 1
        pending = target
        if run >= CONFIRM_STEPS:
            change = {'games': games, 'from': state, 'to': target, 'prob_above': prob, 'known_utc': when}
            if callable(corrected_bar):
                change.update(bar=round(bar, 6), tested=tested)
            if pro is not None:
                change.update(prospective_games=pro[0], prospective_prob=pro[1])
            changes.append(change)
            state, pending, run = target, None, 0
    return state, pending, changes


def family_timeline(entries):
    """entries: hora em que cada hipótese entrou na família testada (None = só "agora"). -> função hora -> (fasquia, m)."""
    known = sorted(t for t in entries if t is not None)
    total = len(entries)

    def at(t):
        if t is None:
            m = total
        else:
            lo, hi = 0, len(known)
            while lo < hi:
                mid = (lo + hi) // 2
                if known[mid] <= t:
                    lo = mid + 1
                else:
                    hi = mid
            m = lo
        return corrected_bar(m), m
    return at


def evaluate(signals, registry, min_settled, *, exploratory=False):
    if not exploratory:
        signals = [b for b in signals if eligible(b)]
    multi, earlier = multi_matches(signals), earlier_index(signals)
    have = [(b, insights.family(b), set(atoms(b, multi, earlier))) for b in signals]
    rows, matched, born_of = [], {}, {}
    for hid in sorted(registry):
        h = registry[hid]
        conds = {tuple(c) for c in h['conditions']}
        born = chronology.born(h)
        born_of[hid] = born
        gen = h.get('source') == 'gerador'
        # Propostas de quem já viu resultados (llm/manual) só contam sinais posteriores ao registo; sem registo válido, nenhum.
        pool = [(b, at) for b, fam, at in have if fam == h['family'] and (gen or (born is not None and (chronology.signal_time(b) or born) > born))]
        match = [b for b, at in pool if conds <= at]
        s = insights.summarize(match)
        s['prob_adds'] = insights.prob_better(match, [b for b, at in pool if not conds <= at]) if conds else None
        if s['games'] >= HINT_AT:
            matched[hid] = match
        row = dict(s, id=hid, family=h['family'], conditions=h['conditions'], source=h.get('source'), born_utc=h.get('born_utc'), reason=h.get('reason'), expect=h.get('expect'), mode=h.get('mode'),
                   tradable=not any(c[0] in POST for c in conds), open=[b for b in match if b['w'] is None and not b.get('no_source')], born_verified=born is not None,
                   game_set=None if exploratory else game_set(match), _games=None if exploratory else game_ids(match))
        if not exploratory:
            row['prospective'] = prospective_summary(match, born)
            row['evidence_level'] = ('não verificável' if born is None else 'prospetiva' if row['prospective']['games'] >= PROSPECTIVE_MIN else 'exploratória')
        rows.append(row)
    # Família testada, no tempo: cada hipótese entra quando está registada E tem HINT_AT jogos provadamente conhecidos.
    ordered = {hid: ordered_games(m) for hid, m in matched.items()}
    entries = []
    for hid, games in ordered.items():
        t30, born = games[HINT_AT - 1][0], born_of[hid]
        entries.append(None if t30 is None or born is None else max(t30, born))
    timeline = family_timeline(entries)
    bar, tested = timeline(None)
    for r in rows:
        r['status'], r['pending_status'], r['status_changes'] = 'em observação', None, []
        born = born_of[r['id']]
        tail = None
        if r['id'] in matched and born is not None:   # sem registo válido: nunca toma decisões (fail-closed)
            steps = game_steps(matched[r['id']], born, prospective_from=born)
            r['status'], r['pending_status'], r['status_changes'] = stable_status(steps, timeline, min_settled)
            tail = steps[-1][3]['tail'] if steps else None
        if not exploratory and r['id'] in matched:
            before = [g for g in ordered[r['id']] if g[0] is not None and born is not None and g[0] < born]
            v = insights.Votes()
            for _, _, gr in before:
                for b in gr:
                    v.add(b)
            h, m, be, n = v.totals()
            r['at_birth'] = {'games': n, 'prob_above': insights.evidence(h, m, be)['prob_above'] if n else None}
            r['result_time_unknown'] = sum(1 for g in ordered[r['id']] if g[0] is None)
        # Transparency: the status the entry thresholds alone would give now. Different from `status` when the status is held
        # by hysteresis (inside the keep band) or a change is still waiting for confirmation (`pending_status`).
        pro = r.get('prospective')
        r['entry_status'] = status_of(r['games'], r['prob_above'], corrected_bar(tested, r['games']), min_settled,
                                      (pro['games'], pro['prob_above']) if pro else (0, None), tail) if born is not None else 'em observação'
    return rows, tested, bar


def board(rows, now=None, family_n=None):
    """Quadro em destaque: as táticas apostáveis com melhor e pior evidência até agora, e os sinais em aberto que encaixam nas melhores.

    Antes dos 30 resultados uma tática é só um "indício inicial"; o quadro diz sempre quantos resultados tem e quantos faltam.
    """
    now = now or datetime.now(timezone.utc)
    cand = [r for r in rows if r['tradable'] and r['conditions'] and r['prob_above'] is not None and r['games'] >= BOARD_MIN]

    def pick(ordered, limit):
        out, per_family, samples = [], {}, set()
        for r in ordered:
            whole = bool(family_n) and r['n'] >= 0.9 * family_n.get(r['family'], 1e9)   # apanha quase todos os sinais do tipo: é o tipo inteiro
            r['whole'] = whole
            sample = (r['family'], 'todos') if whole else (r['family'], r['n'], r['hits'])
            if sample in samples or per_family.get(r['family'], 0) >= 2:
                continue   # mesma amostra que uma tática já escolhida, ou família já com duas
            samples.add(sample)
            per_family[r['family']] = per_family.get(r['family'], 0) + 1
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def card(r):
        return {'id': r['id'], 'family': r['family'], 'conditions': [] if r.get('whole') else r['conditions'], 'n': r['n'], 'hits': r['hits'], 'hit_rate': r['hit_rate'],
                'break_even': r['break_even'], 'prob_above': r['prob_above'], 'roi': r['roi'], 'source': r['source'], 'reason': r.get('reason'),
                'games': r.get('games'), 'edge': r.get('edge'), 'edge_ci': r.get('edge_ci'), 'prob_above_cut': r.get('prob_above_cut'), 'prob_adds': None if r.get('whole') else r.get('prob_adds'),
                'bets': r.get('bets'), 'pending_status': r.get('pending_status'), 'entry_status': r.get('entry_status'),
                'level': r['status'] if r['status'] != 'em observação' else 'indício inicial', 'missing': max(0, HINT_AT - r['games'])}

    best = pick(sorted((r for r in cand if r['prob_above'] >= BOARD_GOOD and r['status'] != 'abandonada'), key=lambda r: (-r['prob_above'], -r['games'])), 5)
    avoid = pick(sorted((r for r in cand if r['prob_above'] <= BOARD_BAD), key=lambda r: (r['prob_above'], -r['games'])), 3)
    live = {}
    for r in best:
        for b in r['open']:
            if not eligible(b):
                continue
            ko = datetime.fromisoformat(b['kickoff_utc']) if b.get('kickoff_utc') else None
            fresh = (ko is not None and ko > now) or (now - datetime.fromisoformat(b['date_utc'])).total_seconds() <= 3600
            if fresh and b['key'] not in live:
                teams = b['teams'] or ['equipa da casa', 'equipa de fora']
                sel = {'1': teams[0], '2': teams[1], 'X': 'Empate'}.get(b['selection'], b['selection']) if b['market'] == '1X2' else b['selection']
                live[b['key']] = {'date_utc': b['date_utc'], 'kickoff_utc': b.get('kickoff_utc'), 'match': ' — '.join(b['teams'] or []), 'selection': sel,
                                  'market': b['market'], 'odds': b['odds'], 'tactic': r['id']}
    return {'best': [card(r) for r in best], 'avoid': [card(r) for r in avoid], 'open': sorted(live.values(), key=lambda x: x['date_utc'], reverse=True)[:8],
            'min_results': BOARD_MIN, 'hint_at': HINT_AT}


AGENDA_LIVE_MIN = 30     # sinal ao vivo (ou sem hora de início) só entra na agenda até 30 minutos depois, como os alertas
VERDICT_ORDER = ('favorável', 'misto', 'sem evidência', 'contraditório', 'evitar')


def agenda(rows, signals, now=None):
    """Agenda: cada sinal com odd ainda apostável, com as táticas boas e más em que encaixa neste momento.

    Usa as mesmas regras do quadro (táticas apostáveis com pelo menos BOARD_MIN resultados; boa >= BOARD_GOOD, má <= BOARD_BAD
    ou abandonada) e é refeita a cada relatório, por isso muda à medida que chegam resultados.
    Veredicto por sinal: favorável (só boas), misto (boas e más), evitar (só más), sem evidência (nenhuma),
    contraditório (o mesmo jogo tem sinais em seleções diferentes do mesmo mercado).
    odds_band_n: resultados do mesmo tipo de sinal na faixa de odd deste sinal; poucos = as táticas foram medidas noutras odds.
    """
    now = now or datetime.now(timezone.utc)
    cand = [r for r in rows if r['tradable'] and r['conditions'] and r['prob_above'] is not None and r['games'] >= BOARD_MIN]
    signals = [b for b in signals if eligible(b)]
    good, bad = {}, {}
    for r in sorted(cand, key=lambda r: (-r['prob_above'], -r['games'])):
        bucket = good if r['prob_above'] >= BOARD_GOOD and r['status'] != 'abandonada' else bad if r['prob_above'] <= BOARD_BAD or r['status'] == 'abandonada' else None
        if bucket is not None:
            for b in r['open']:
                bucket.setdefault(b['key'], []).append(r)
    bad = {k: sorted(v, key=lambda r: (r['prob_above'], -r['games'])) for k, v in bad.items()}
    band_n = {}
    for b in signals:
        if b['w'] is not None and b['w'] != 0 and b.get('odds'):
            k = (insights.family(b), insights.odds_band(b['odds']))
            band_n[k] = band_n.get(k, 0) + 1

    def live(b):
        ko = b.get('kickoff_utc') and datetime.fromisoformat(b['kickoff_utc'])
        if ko and (ko if ko.tzinfo else ko.replace(tzinfo=timezone.utc)) > now:
            return 'por começar'
        if (now - datetime.fromisoformat(b['date_utc'])).total_seconds() <= AGENDA_LIVE_MIN * 60:
            return 'a decorrer'
        return None

    def distinct(hs, limit):
        out, seen = [], set()
        for r in hs:
            sample = (r['family'], r['n'], r['hits'])   # "mercado pequeno" e "volume pequeno" apanham muitas vezes os mesmos sinais
            if sample not in seen:
                seen.add(sample)
                out.append(r)
        return out[:limit], len(out)

    def card(r):
        return {'id': r['id'], 'conditions': r['conditions'], 'n': r['n'], 'games': r['games'], 'hits': r['hits'], 'break_even': r['break_even'], 'prob_above': r['prob_above'],
                'roi': r['roi'], 'edge': r.get('edge'), 'status': r['status'], 'source': r['source']}

    open_ = [(b, live(b)) for b in signals if b['w'] is None and not b.get('no_source') and b.get('market') is not None and b.get('odds')]
    open_ = [(b, when) for b, when in open_ if when]
    picks = {}
    for b, _ in open_:
        picks.setdefault((b['match_id'], b['market']), set()).add(b['selection'])
    out = []
    for b, when in open_:
        g, ng = distinct(good.get(b['key'], []), 3)
        x, nx = distinct(bad.get(b['key'], []), 2)
        conflict = len(picks[(b['match_id'], b['market'])]) > 1
        verdict = 'contraditório' if conflict else 'favorável' if g and not x else 'misto' if g else 'evitar' if x else 'sem evidência'
        teams = b.get('teams') or []
        sel = {'1': teams[0], '2': teams[1], 'X': 'Empate'}.get(b['selection'], b['selection']) if b['market'] == '1X2' and len(teams) == 2 else b['selection']
        band = insights.odds_band(b['odds'])
        out.append({'match_id': b['match_id'], 'match': ' — '.join(teams) or b['match_id'], 'league': b.get('league'), 'family': insights.family(b), 'type': b['type'],
                    'phase': b.get('phase'), 'when': when, 'kickoff_utc': b.get('kickoff_utc'), 'date_utc': b['date_utc'], 'minute': b.get('minute'),
                    'score_at_signal': b.get('score_at_signal'), 'market': b['market'], 'selection': sel, 'odds': b['odds'], 'odds_last': b.get('odds_last'),
                    'max_mult': b.get('max_mult'), 'share': b.get('share'), 'share_last': b.get('share_last'), 'verdict': verdict, 'conflict': conflict,
                    'validated': any(r['status'] in ('forte', 'confirmada') for r in g) and verdict == 'favorável',
                    'odds_band': band, 'odds_band_n': band_n.get((insights.family(b), band), 0), 'good': [card(r) for r in g], 'good_total': ng, 'bad': [card(r) for r in x], 'bad_total': nx})
    rank = {v: i for i, v in enumerate(VERDICT_ORDER)}
    out.sort(key=lambda a: (rank[a['verdict']], -(a['good'][0]['prob_above'] if a['good'] else 0), a['kickoff_utc'] or a['date_utc']))
    counts = {v: sum(1 for a in out if a['verdict'] == v) for v in VERDICT_ORDER}
    return {'items': out[:40], 'counts': counts, 'generated_utc': now.isoformat(timespec='seconds'),
            'rules': {'min_results': BOARD_MIN, 'good': BOARD_GOOD, 'bad': BOARD_BAD, 'live_max_min': AGENDA_LIVE_MIN, 'hint_at': HINT_AT, 'strong_at': STRONG_AT}}


def multiplicity(rows, tested, bar):
    """Testes múltiplos sob dependência: o que a fasquia controla e quanta evidência distinta existe por trás dos estados.

    Bonferroni (1 − α/m) controla o erro de família para QUALQUER dependência entre hipóteses (desigualdade da união): não
    assume independência e, com hipóteses sobrepostas, é conservador. O inverso não vale para as promissoras (sem correção):
    hipóteses sobre os mesmos jogos sobem e descem juntas, por isso N promissoras não são N descobertas. distinct_game_sets
    conta conjuntos de jogos diferentes; as hipóteses com o mesmo conjunto são a mesma evidência.
    """
    tested_rows = [r for r in rows if r['games'] >= HINT_AT]
    sets = lambda rs: len({r['game_set'] for r in rs})
    good = [r for r in tested_rows if r['status'] in ('promissora', 'forte', 'confirmada')]
    return {'method': 'bonferroni_chronological_over_hypotheses_and_looks', 'alpha': FWER_ALPHA, 'valid_under_dependence': True,
            'look_share': 'alpha * %d / (n (n + 1)) at n games (sums to alpha over n >= %d)' % (STRONG_AT, STRONG_AT),
            'family': 'registered hypotheses with >= %d independent games known at each instant' % HINT_AT,
            'tested_now': tested, 'bar_now_at_first_look': round(bar, 8),
            'distinct_game_sets_tested': sets(tested_rows), 'distinct_game_sets_promising_or_better': sets(good),
            'promising_or_better': len(good),
            'promising_sum_games': sum(r['games'] for r in good),
            'promising_union_games': len(set().union(*(r.get('_games') or set() for r in good))) if good else 0,
            'validated_requires': {'games': STRONG_AT, 'bar': 'corrected at the decision instant', 'prospective_games': PROSPECTIVE_MIN,
                                   'prospective_prob': PROMOTE_AT, 'prospective_keep': KEEP_PROMISING},
            'evidence_levels': {lvl: sum(1 for r in tested_rows if r.get('evidence_level') == lvl) for lvl in ('exploratória', 'prospetiva', 'não verificável')},
            'note': 'promissora is uncorrected and exploratory; expected_by_chance is an expectation only (dependence widens its spread)'}


def build(signals, registry, min_settled):
    all_signals = signals
    signals = [b for b in all_signals if eligible(b)]
    excluded = {}
    for b in all_signals:
        for reason in exclusion_reasons(b):
            excluded[reason] = excluded.get(reason, 0) + 1
    exploratory_rows, _, _ = evaluate(all_signals, registry, min_settled, exploratory=True)
    exploratory_counts = {}
    for r in exploratory_rows:
        exploratory_counts[r['status']] = exploratory_counts.get(r['status'], 0) + 1
    rows, tested, bar = evaluate(signals, registry, min_settled)
    fams = {}
    for b in signals:
        if b['w'] is not None and b['w'] != 0 and b['odds']:
            fams[insights.family(b)] = fams.get(insights.family(b), 0) + 1
    brd = board(rows, family_n=fams)
    agd = agenda(rows, signals)
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    good = [r for r in rows if r['status'] in ('promissora', 'forte', 'confirmada') and r['tradable']]
    watch = {}
    for r in sorted(good, key=lambda r: -r['prob_above']):
        for b in r['open']:
            w = watch.setdefault(b['key'], {'date_utc': b['date_utc'], 'match': ' — '.join(b['teams'] or []), 'type': b['type'], 'market': b['market'],
                                            'selection': b['selection'], 'odds': b['odds'], 'kickoff_utc': b['kickoff_utc'], 'reasons': []})
            w['reasons'].append({'id': r['id'], 'status': r['status'], 'n': r['n'], 'games': r['games'], 'hit_rate': r['hit_rate'], 'prob_above': r['prob_above'],
                                 'edge': r.get('edge'), 'edge_ci': r.get('edge_ci'), 'prob_above_cut': r.get('prob_above_cut'),
                                 'prudent_rate': r.get('prudent_rate'), 'break_even': r.get('break_even')})
    mult = multiplicity(rows, tested, bar)
    for r in rows:
        r['open'] = len(r['open'])
        r.pop('_games', None)
    rows.sort(key=lambda r: (-(r['prob_above'] if r['prob_above'] is not None and r['n'] >= 5 else -1), -r['n']))
    return {'evidence_scope': 'eligible_bets_only', 'status_rules': STATUS_RULES,
            'eligibility': {'total': len(all_signals), 'eligible': len(signals), 'excluded': len(all_signals) - len(signals), 'reasons': excluded},
            'exploratory': {'scope': 'all_signals_not_for_recommendations', 'counts': exploratory_counts,
                            'hypotheses': [{k: r[k] for k in ('id', 'n', 'games', 'roi', 'status')} for r in sorted(exploratory_rows, key=lambda r: (-r['n'], r['id']))[:150]]},
            'hypotheses': len(rows), 'tested': tested, 'corrected_bar': round(bar, 8), 'corrected_bar_tail': float('%.3g' % (1 - bar)), 'counts': counts, 'board': brd, 'agenda': agd,
            'track': track_record(rows), 'walk_forward': walk_forward(signals, registry), 'multiplicity': mult,
            'expected_by_chance': round(0.05 * tested, 1), 'top': [{k: r.get(k) for k in ('id', 'family', 'conditions', 'n', 'hits', 'hit_rate', 'break_even', 'prob_above', 'roi', 'status', 'tradable', 'expect',
                                                                                                    'games', 'bets', 'edge', 'edge_ci', 'prob_above_cut', 'prob_adds', 'pending_status', 'entry_status',
                                                                                                    'born_utc', 'born_verified', 'evidence_level', 'prospective', 'at_birth', 'result_time_unknown', 'game_set')}
                    | {'status_changes': len(r['status_changes']), 'last_change': r['status_changes'][-1] if r['status_changes'] else None}
                    for r in rows if r['n'] >= 5][:150],
            'status_list': {r['id']: r['status'] for r in rows if r['status'] != 'em observação'},
            'abandoned': [r for r in rows if r['status'] == 'abandonada'][:10],
            'llm': sorted((r for r in rows if r['source'] == 'llm'), key=lambda r: r['born_utc'], reverse=True)[:20],
            'llm_total': sum(1 for r in rows if r['source'] == 'llm'),
            'watch': sorted(watch.values(), key=lambda w: w['date_utc'], reverse=True)[:30],
            'rules': {'min_support': MIN_SUPPORT, 'max_conditions': MAX_CONDITIONS, 'hint_at': HINT_AT, 'strong_at': STRONG_AT, 'confirm_at': min_settled,
                      'unit': 'independent_games', 'status_rules': STATUS_RULES, 'promote_at': PROMOTE_AT, 'keep_promising': KEEP_PROMISING,
                      'abandon_at': ABANDON_AT, 'keep_abandoned': KEEP_ABANDONED, 'confirm_steps': CONFIRM_STEPS,
                      'fwer_alpha': FWER_ALPHA, 'corrected_bar': 'chronological', 'prospective_min': PROSPECTIVE_MIN,
                      'result_time': 'first_observation_by_the_system', 'observation_margin_min': chronology.OBSERVATION_MARGIN_MIN,
                      'decisions_from': 'born_utc'}}


def track_record(rows):
    """Balanço do processo de descoberta: das hipóteses com 10 ou mais resultados, quantas foram no sentido esperado, por origem.

    As do modelo de linguagem só contam resultados posteriores à proposta; as do gerador servem de termo de comparação.
    """
    groups, worked, failed = {}, [], []
    for r in rows:
        if not r['conditions']:
            continue
        key = 'gerador' if r['source'] == 'gerador' else '%s/%s' % (r['source'], r.get('mode') or 'novas')
        g = groups.setdefault(key, {'hypotheses': 0, 'judged': 0, 'as_expected': 0})
        g['hypotheses'] += 1
        if r['games'] < TRACK_MIN or r['prob_above'] is None:
            continue
        ok = (r['prob_above'] < 0.5) if r.get('expect') == 'abaixo' else (r['prob_above'] > 0.5)
        g['judged'] += 1
        g['as_expected'] += ok
        if r['source'] == 'llm':
            (worked if ok else failed).append({k: r.get(k) for k in ('id', 'n', 'games', 'hit_rate', 'break_even', 'prob_above', 'expect')})
    llm = [g for k, g in groups.items() if k.startswith('llm')]
    return {'by_source': groups, 'min_results': TRACK_MIN, 'llm_judged': sum(g['judged'] for g in llm), 'llm_as_expected': sum(g['as_expected'] for g in llm),
            'worked': sorted(worked, key=lambda r: -r['games'])[:6], 'failed': sorted(failed, key=lambda r: -r['games'])[:6]}


def known_at(b):
    """Momento PROVADO a partir do qual o resultado de um sinal estava disponível ao sistema, ou None (desconhecido).

    PR 3: substitui a aproximação de 150 minutos depois do início, que usou resultados antes de o sistema os ter.
    """
    return chronology.result_known(b)


def walk_forward(signals, registry):
    """E se tivesse seguido o quadro em destaque? Só decisões que o sistema podia ter tomado, só com o que sabia nesse momento.

    Para cada sinal com odd e origem provada, pela hora da decisão (quando o sistema leu o sinal):
    - decisão reconstituível só se o sinal foi lido enquanto ainda dava para apostar (chronology.decidable); os restantes
      (por exemplo, a recolha inicial de 12/11, que leu mensagens de dias anteriores) contam como "não reconstituíveis";
    - só resultados com hora de observação provada e anterior à decisão; sem hora provada, nunca;
    - só hipóteses já registadas nessa altura (born_utc <= decisão); sem born_utc válido, nunca. As do modelo de linguagem
      ou manuais só usam resultados de sinais posteriores ao seu registo;
    - segue-se o sinal se alguma tática apostável tinha pelo menos BOARD_MIN jogos e probabilidade >= BOARD_GOOD.
    Compara os sinais seguidos com os não seguidos: se o cérebro acrescenta alguma coisa, os seguidos têm de render mais.
    """
    signals = [b for b in signals if eligible(b)]
    multi, earlier = multi_matches(signals), earlier_index(signals)
    hyps, invalid_birth = [], 0
    for hid in sorted(registry):
        h = registry[hid]
        if not h.get('conditions') or any(c[0] in POST for c in h['conditions']):
            continue
        born = chronology.born(h)
        if born is None:
            invalid_birth += 1
            continue
        hyps.append((hid, h['family'], {tuple(c) for c in h['conditions']}, born, h.get('source') == 'gerador'))
    fits = {}
    for b in signals:
        fam, at = insights.family(b), set(atoms(b, multi, earlier))
        fits[id(b)] = [i for i, (_, f, conds, _, _) in enumerate(hyps) if f == fam and conds <= at]
    settled = [b for b in signals if b['w'] is not None and b['w'] != 0]
    timed = sorted(((known_at(b), b) for b in settled if known_at(b) is not None),
                   key=lambda kb: (kb[0], chronology.signal_time(kb[1]), str(kb[1].get('key'))))
    votes, pos = [insights.Votes() for _ in hyps], 0
    sides, unknown = {True: [], False: []}, []
    order = sorted((b for b in signals if b.get('odds') and b.get('market') is not None),
                   key=lambda b: (chronology.decision_time(b) or chronology.signal_time(b), chronology.signal_time(b), str(b.get('key'))))
    for b in order:
        if not chronology.decidable(b):
            unknown.append(b)
            continue
        when = chronology.decision_time(b)
        while pos < len(timed) and timed[pos][0] <= when:
            r = timed[pos][1]
            for i in fits[id(r)]:
                if hyps[i][4] or chronology.signal_time(r) > hyps[i][3]:
                    votes[i].add(r)
            pos += 1
        follow = False
        for i in fits[id(b)]:
            if hyps[i][3] <= when and len(votes[i].m) >= BOARD_MIN:
                h, m, be, games = votes[i].totals()
                if games >= BOARD_MIN and (insights.evidence(h, m, be)['prob_above'] or 0) >= BOARD_GOOD:
                    follow = True
                    break
        sides[follow].append(b)

    def side(rows):
        done = [b for b in rows if b['w'] is not None]
        pl = lambda b, cut: b['w'] * (b['odds'] * (1 - cut) - 1) if b['w'] > 0 else b['w']
        units = sum(pl(b, 0.0) for b in done)
        return {'bets': len(rows), 'settled': len(done), 'games': len({b['match_id'] for b in done}), 'wins': sum(1 for b in done if b['w'] > 0),
                'units': round(units, 2), 'roi': round(units / len(done), 4) if done else None,
                'roi_cut': round(sum(pl(b, insights.ODDS_LOSS) for b in done) / len(done), 4) if done else None}

    curve, total = [], 0.0
    for b in sides[True]:
        if b['w'] is not None:
            total += b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w']
            curve.append(round(total, 2))
    decided = sides[True] + sides[False]
    checks = [chronology.result_check(b)[1] for b in settled]
    return {'followed': side(sides[True]), 'skipped': side(sides[False]), 'not_reconstructible': side(unknown), 'curve': curve[-400:],
            'prob_followed_better': insights.prob_better(sides[True], sides[False]),
            'first_followed_utc': sides[True][0]['date_utc'] if sides[True] else None,
            'first_decision_utc': chronology.iso(min(chronology.decision_time(b) for b in decided)) if decided else None,
            'chronology': {'results_verified': checks.count('verified'), 'results_unknown_time': checks.count('unknown'),
                           'results_contradictory_time': checks.count('contradictory'), 'hypotheses_without_valid_birth': invalid_birth},
            'rule': {'min_results': BOARD_MIN, 'unit': 'independent_games', 'min_prob': BOARD_GOOD, 'method': 'chronological_v1',
                     'result_time': 'first_observation_by_the_system', 'observation_margin_min': chronology.OBSERVATION_MARGIN_MIN,
                     'decision_time': 'signal_read_by_the_system', 'decision_delay_max_min': chronology.DECISION_DELAY_MAX_MIN,
                     'max_lag_min': chronology.DECISION_MAX_LAG_MIN, 'hypotheses': 'registered_before_the_decision'}}


def entry_points(signals):
    """Betfair: entrar ao primeiro sinal ou esperar pelo reforço? Cada linha usa a odd do primeiro sinal que chegou a esse reforço."""
    signals = [b for b in signals if eligible(b)]
    out = []
    for phase in ('pre', 'live'):
        rows = [b for b in signals if b['type'] == 'BETFAIR' and b['phase'] == phase and b.get('steps')]
        for level in (1, 2, 4, 8, 16):
            virtual = []
            for b in rows:
                odds = next((o for m, o in b['steps'] if m >= level), None)
                if odds is not None:
                    virtual.append(dict(b, odds=odds))
            s = insights.summarize(virtual)
            out.append({'phase': phase, 'level': level, 'signals': s['signals'], 'n': s['n'], 'hits': s['hits'], 'hit_rate': s['hit_rate'],
                        'break_even': s['break_even'], 'roi': s['roi'], 'prob_above': s['prob_above']})
    return out


def load_registry(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

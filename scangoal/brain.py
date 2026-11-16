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
import hashlib, itertools, json, math
from datetime import datetime, timedelta, timezone

from . import certification, chronology, insights
from .parser import competition_kind
from .eligibility import bet_identity, eligible, exclusion_reasons, game_identity

MIN_SUPPORT = 8          # sinais com a combinação para a hipótese ser registada
MAX_CONDITIONS = 3       # o gerador combina até 3 características; o registo não tem limite de tamanho
HINT_AT, STRONG_AT = 30, 100                       # jogos independentes
BOARD_MIN, BOARD_GOOD, BOARD_BAD = 8, 0.80, 0.20   # quadro em destaque: mínimo de jogos e limiares de probabilidade
TRACK_MIN = 10           # jogos para uma hipótese entrar no balanço das propostas
STATUS_RULES = 'chronological_eprocess_v3'        # muda quando as regras de estado mudam (migração silenciosa no Telegram)
VALIDATION_METHOD = 'eprocess_online_alpha_v1'     # o Telegram só anuncia validadas certificadas por este método
FWER_ALPHA = 0.05        # erro de família, para toda a vida do projeto, das táticas certificadas (forte/confirmada)
EPROCESS_LAMBDAS = (0.5, 0.25, 0.125, 0.0625, 0.03125)   # fixados a priori (grelha geométrica); nunca escolhidos pelos dados
VALIDATED = ('forte', 'confirmada')
OPEN_TIMEOUT_DAYS = 3       # = report.NO_SOURCE_AFTER_DAYS: jogo da cadeia sem resultado até lá fecha como perda
PROSPECTIVE_MIN = HINT_AT   # jogos operacionais posteriores (ao registo / à seleção) exigidos à evidência prospetiva e à certificação
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


def alpha_for_rank(j):
    """Orçamento α da j-ésima hipótese selecionada (por ordem de seleção): α/(j(j+1)). Soma α sobre j >= 1 (telescópica)."""
    return certification.alpha_for_rank(j)


def status_of(n, prob, corrected_bar, min_settled, certified=None):
    """Estado pelos limiares de entrada, sem memória (n = jogos independentes). É o alvo a partir de "em observação".

    certified: certificado do teste de confirmação (PR 3). O cérebro passa sempre True/False e forte/confirmada exigem
    True. None só existe para chamadas diretas à máquina de estados da PR 2 (fasquia numérica corrected_bar).
    """
    if prob is None or n < HINT_AT:
        return 'em observação'
    if prob <= ABANDON_AT:
        return 'abandonada'
    valid = prob >= corrected_bar if certified is None else (certified is True and prob >= PROMOTE_AT)
    if n >= STRONG_AT and valid:
        return 'confirmada' if n >= min_settled else 'forte'
    return 'promissora' if prob >= PROMOTE_AT else 'em observação'


def next_status(state, games, prob, corrected_bar, min_settled, certified=None):
    """Estado-alvo dado o estado atual (histerese): entrar exige o limiar de entrada; manter-se, só o limiar de saída.

    certified (PR 3): True/False vindo do teste de confirmação; forte/confirmada exigem True e prob >= 0,95 (manter: 0,90).
    None: comportamento da PR 2 com a fasquia numérica (só chamadas diretas; o cérebro nunca usa este caminho).
    """
    if prob is None or games < HINT_AT:
        return 'em observação'
    if state == 'abandonada' and prob <= KEEP_ABANDONED:
        return 'abandonada'
    if prob <= ABANDON_AT:
        return 'abandonada'
    keep = state in VALIDATED
    if certified is None:
        valid = prob >= (1 - 2 * (1 - corrected_bar) if keep else corrected_bar)
    else:
        valid = certified is True and prob >= (KEEP_PROMISING if keep else PROMOTE_AT)
    if games >= STRONG_AT and valid:
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


# Dimensões cujo valor é conhecido NO PRIMEIRO SINAL da própria aposta, sem olhar para outras mensagens (revisão #5911603577,
# P1-C). Só hipóteses cujas condições usam APENAS estas dimensões podem ser certificadas. Excluídas: as de POST (reforço, odd e
# quota depois do sinal, velocidade do reforço, outros sinais no mesmo jogo — dependem de sinais futuros) e "Sinal anterior de
# outro tipo no mesmo jogo" (depende de outras mensagens, sem prova de que já tinham sido lidas). Lista fechada: uma dimensão
# nova não é certificável enquanto não for acrescentada aqui com justificação.
AS_OF_DIMENSIONS = frozenset({
    'Competição', 'Faixa de odd', 'Dia', 'Hora (UTC)', 'Liga', 'Momento', 'Quota de mercado', 'Tamanho do mercado', 'Antecedência',
    'Parte do jogo', 'Confiança indicada pelo bot', 'Ritmo indicado pelo bot', 'Golos em falta para ganhar', 'Vantagem indicada pelo bot',
    'Mercado', 'Momento do aviso', 'Marcador no momento', 'Golos até ao sinal', 'Minuto', 'Seleção', 'Volume na seleção',
    'Valor da aposta', 'Número de apostas'})


def certifiable(h):
    """Hipótese certificável: registo válido e todas as condições em AS_OF_DIMENSIONS (a família é o tipo do sinal)."""
    conds = h.get('conditions') if isinstance(h, dict) else None
    return (chronology.born(h) is not None and isinstance(conds, list)
            and all(isinstance(c, (list, tuple)) and len(c) == 2 and c[0] in AS_OF_DIMENSIONS for c in conds))


def operational_bet(b, t):
    """Aposta que é evidência OPERACIONAL posterior ao instante t (registo ou seleção da hipótese). Definição única (PR 3,
    revisões #5911487915 e #5911603577), usada pelo relatório, pela máquina de estados e pelo teste de confirmação:
    - sinal emitido depois de t (date_utc > t): um sinal antigo recuperado tarde nunca é prospetivo;
    - sinal lido pelo sistema depois de t (o carimbo da leitura, não só a hora de envio);
    - sinal decidível quando foi lido (chronology.decidable: ainda dava para apostar);
    - resultado com hora de observação provada (chronology.result_check == verified).
    A condição "decidida antes de qualquer resultado do jogo ser conhecido" é do jogo (game_core).
    """
    if t is None:
        return False
    sent, stamp = chronology.signal_time(b), chronology.parse_utc(b.get('signal_seen_utc'))
    if sent is None or stamp is None or sent <= t or stamp <= t or not chronology.decidable(b):
        return False
    return chronology.result_check(b)[1] == 'verified'


def latest_decision(b):
    """O instante mais tardio em que a decisão sobre a aposta pode ter acontecido: leitura + atraso máximo do relatório."""
    t = chronology.decision_time(b)
    return t + timedelta(minutes=chronology.DECISION_DELAY_MAX_MIN) if t is not None else None


def first_result_seen(rows):
    """Primeira observação de QUALQUER resultado do jogo (report.match_first_seen); None se não provada em alguma aposta."""
    stamps = [chronology.parse_utc(b.get('match_result_first_seen_utc')) for b in rows]
    return None if not stamps or any(t is None for t in stamps) else min(stamps)


def game_core(rows):
    """Apostas do jogo decididas ANTES de o sistema conhecer qualquer resultado desse jogo (P1-D). As decididas depois são
    ignoradas (nunca mudam o jogo retroativamente). None se a primeira observação do resultado não está provada."""
    first = first_result_seen(rows)
    if first is None:
        return None
    return [b for b in rows if latest_decision(b) is not None and latest_decision(b) < first]


def operational_game(rows, t):
    """Um jogo é evidência operacional posterior a t se tem apostas decididas antes do resultado e TODAS essas apostas são
    operacionais (jogos mistos: não)."""
    core = game_core(rows)
    return bool(core) and all(operational_bet(b, t) for b in core)


def game_category(rows, born):
    """Categoria de um jogo face ao registo (auditoria)."""
    core = game_core(rows)
    if core is None:
        return 'result_time_unproven'
    if not core:
        return 'result_known_before_decision'
    ok = [operational_bet(b, born) for b in core]
    if all(ok):
        return 'prospective'
    if any(ok):
        return 'mixed'
    if all((chronology.signal_time(b) or _LAST) <= born for b in core):
        return 'before_registration'
    return 'after_registration_not_operational'


CATEGORIES = ('prospective', 'mixed', 'before_registration', 'after_registration_not_operational', 'result_known_before_decision',
              'result_time_unproven')


def prospective_summary(ordered, born):
    """Evidência PROSPETIVA de uma hipótese (None sem registo válido): só jogos operacionais posteriores ao registo, com as
    apostas decididas antes do resultado (game_core). Mesma regra e mesmos jogos que a máquina de estados."""
    if born is None:
        return None
    v, cats, pls = insights.Votes(), dict.fromkeys(CATEGORIES, 0), []
    for _, _, rows in ordered:
        cat = game_category(rows, born)
        cats[cat] += 1
        if cat == 'prospective':
            for b in game_core(rows):
                v.add(b)
                pls.append(b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w'])
    h, m, be, n = v.totals()
    return {'games': n, 'hits': round(h, 2), 'misses': round(m, 2),
            'prob_above': insights.evidence(h, m, be)['prob_above'] if n >= BOARD_MIN else None,
            'roi': round(sum(pls) / len(pls), 4) if pls else None, 'categories': cats}


def bet_profit(b):
    """Lucro por unidade de UMA aposta à odd anunciada com perda ODDS_LOSS; em [-1, ∞). None se a odd ou o resultado forem inválidos."""
    o, w = b.get('odds'), b.get('w')
    if isinstance(o, bool) or not isinstance(o, (int, float)) or not math.isfinite(o) or o <= 1:
        return None
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not -1 <= w <= 1:
        return None
    return w * (o * (1 - insights.ODDS_LOSS) - 1) if w > 0 else float(w)


def committed_bet(rows):
    """A aposta COMPROMETIDA de um jogo (revisão #5911747123, P1-G): a primeira aposta da hipótese a ser decidida nesse jogo
    (hora da decisão, depois hora de envio e identidade). Fica fixada nesse instante; apostas posteriores do mesmo jogo, noutros
    mercados ou seleções, nunca a substituem nem entram numa média. Considera também apostas ainda abertas ou devolvidas."""
    timed = [b for b in rows if chronology.decision_time(b) is not None]
    if not timed:
        return None
    return min(timed, key=lambda b: (chronology.decision_time(b), chronology.signal_time(b) or _LAST, str(b.get('key'))))


def _log_mean_exp(logs):
    top = max(logs)
    return top + math.log(sum(math.exp(x - top) for x in logs) / len(logs))


def confirmation(match, since, alpha, as_of=None):
    """Teste de confirmação com nível garantido: e-processo de apostas (testing by betting), em cadeia sem sobreposição.

    Amostra (revisões #5911603577 e #5911747123), toda decidida com informação disponível na hora de cada decisão:
    1. em cada jogo, só a aposta comprometida (committed_bet): uma aposta por jogo, fixada na primeira decisão (P1-G);
    2. o jogo é candidato se essa aposta foi emitida e lida depois de `since` (a seleção registada), era decidível, e nenhum
       resultado do jogo tinha sido observado até à decisão mais tardia possível (latest_decision); um resultado já
       existente sem hora provada exclui o jogo (fail-closed);
    3. CADEIA SEM SOBREPOSIÇÃO (P1-F): um candidato só é admitido se, na hora da sua decisão, o último jogo admitido já
       estiver fechado; os outros são ignorados. Cada fator é assim decidido depois de revelados todos os anteriores;
    4. um jogo admitido fecha quando o seu resultado é observado (hora provada); se não for observado até OPEN_TIMEOUT_DAYS
       depois da decisão, fecha nesse instante como PERDA (y = −1), definitivamente — logo que o relógio do relatório
       (`as_of`, explícito e reproduzível) chegue ao prazo, mesmo sem nenhuma aposta posterior (revisão #5911845265, P2-I).
       Sem `as_of` (avaliações históricas sem relógio), um jogo pendente fica pendente: nunca se antecipa uma perda.

    Nulo H₀ (por hipótese): para cada aposta admitida, E[y | tudo o que o sistema sabia na hora da decisão] <= 0, com y o
    lucro à odd anunciada −2 % (y = 0 se devolvida/anulada). Como (1 − λ) <= 1 + λ·y para todo y >= −1, o fator efetivo nunca
    excede 1 + λ·y verdadeiro: sob H₀, E[fator | F_decisão] <= 1, e F_decisão contém os fatores anteriores (cadeia). Logo
    E_n = média sobre λ de prod(fatores) é uma supermartingala não negativa e, pela desigualdade de Ville,
    P(algum n com E_n >= 1/α) <= α, sob qualquer dependência entre jogos. Tudo em domínio logarítmico.
    """
    games = {}
    for b in match:
        key = game_identity(b)
        if key is not None:
            games.setdefault(key, []).append(b)
    cands, skipped = [], {'not_operational': 0, 'decision_window_open': 0, 'result_seen_before_decision': 0, 'overlap': 0}
    for key, rows in games.items():
        b = committed_bet(rows)
        if b is None:
            continue
        t, sent, stamp = chronology.decision_time(b), chronology.signal_time(b), chronology.parse_utc(b.get('signal_seen_utc'))
        if since is None or sent is None or stamp is None or sent <= since or stamp <= since or not chronology.decidable(b):
            skipped['not_operational'] += 1
            continue
        if as_of is not None and latest_decision(b) > as_of:
            skipped['decision_window_open'] += 1              # a janela de decisão ainda não fechou no relógio do relatório
            continue
        first = chronology.parse_utc(b.get('match_result_first_seen_utc'))
        settled = b.get('w') is not None
        if (first is not None and latest_decision(b) >= first) or (settled and first is None):
            skipped['result_seen_before_decision'] += 1      # resultado já visto, ou existente sem hora provada: nunca entra
            continue
        cands.append((t, sent, str(b.get('key')), b))
    cands.sort(key=lambda c: c[:3])
    logs, n, log_e, log_max, cert, bar = [0.0] * len(EPROCESS_LAMBDAS), 0, 0.0, 0.0, None, math.log(1 / alpha)
    open_until, pending, admitted, voided = None, False, 0, 0
    timeout = timedelta(days=OPEN_TIMEOUT_DAYS)

    def close(y, closed):
        nonlocal n, log_e, log_max, cert
        n += 1
        for k, lam in enumerate(EPROCESS_LAMBDAS):
            logs[k] += math.log1p(lam * y)
        log_e = _log_mean_exp(logs)
        log_max = max(log_max, log_e)
        if cert is None and n >= PROSPECTIVE_MIN and log_e >= bar:
            cert = closed

    for t, _, _, b in cands:
        if open_until is not None and t < open_until:
            skipped['overlap'] += 1                           # o último jogo admitido ainda estava aberto nesta decisão
            continue
        if pending:                                           # o anterior não teve resultado até ao prazo: perda, nesse prazo
            close(-1.0, open_until)
            voided, pending = voided + 1, False
        admitted += 1
        deadline = t + timeout
        if b.get('w') is None:                                # ainda aberto: bloqueia a cadeia até ao resultado ou ao prazo
            open_until, pending = deadline, True
            continue
        known, verdict = chronology.result_check(b)
        y = bet_profit(b) if verdict == 'verified' and known <= deadline else None
        if y is None:                                         # tardio, sem hora provada ou inválido: perda no prazo
            y, known = -1.0, deadline
            voided += 1
        open_until = known
        close(y, known)
    if pending and as_of is not None and as_of >= open_until:   # o prazo passou no relógio do relatório: perda, nesse prazo
        close(-1.0, open_until)
        voided, pending = voided + 1, False
    shown = lambda x: round(math.exp(x), 4) if x < 700 else None   # e-valor legível; acima de e^700 só o log10
    return {'games': n, 'admitted': admitted, 'voided_as_loss': voided, 'pending': admitted - n, 'skipped': skipped,
            'e_value': shown(log_e), 'e_max': shown(log_max), 'log10_e': round(log_e / math.log(10), 4),
            'log10_e_max': round(log_max / math.log(10), 4), 'threshold': round(1 / alpha, 2), 'alpha': alpha, 'certified_at': cert}


def game_steps(match, born=None, *, prospective_from=None, ordered=None):
    """Evidência acumulada jogo a jogo, pela ordem provada em que os resultados ficaram conhecidos.

    -> [(jogos, prob_above, hora ISO ou None, extra)], só a partir de HINT_AT jogos (antes disso: "em observação").
    born: registo da hipótese. Os passos anteriores juntam-se num só passo "no registo" (a hipótese não decidia antes de
    existir). extra = {'at': datetime ou None, 'prospective_games': jogos operacionais posteriores a prospective_from}.
    """
    votes, pro, raw = insights.Votes(), 0, []
    for k, (known, _, rows) in enumerate(ordered if ordered is not None else ordered_games(match), 1):
        for b in rows:
            votes.add(b)
        if prospective_from is not None and operational_game(rows, prospective_from):
            pro += 1
        if k >= HINT_AT:
            h, m, be, n = votes.totals()
            raw.append([n, insights.evidence(h, m, be)['prob_above'], known, pro])
    if born is not None:
        early = [s for s in raw if s[2] is not None and s[2] < born]
        if early:
            rest = [s for s in raw if s[2] is None or s[2] >= born]
            early[-1][2] = born          # a evidência disponível no registo, como um só passo
            raw = early[-1:] + rest
    return [(n, p, chronology.iso(at), {'at': at, 'prospective_games': pro_}) for n, p, at, pro_ in raw]


_PR2 = object()   # sentinela: máquina de estados chamada diretamente, com a fasquia numérica da PR 2


def stable_status(steps, corrected_bar, min_settled, *, certified_from=_PR2):
    """Máquina de estados com histerese e confirmação. -> (estado, estado pendente ou None, mudanças).

    certified_from (PR 3): instante do certificado do teste de confirmação, ou None (sem certificado: forte/confirmada
    impossíveis). O cérebro passa-o sempre. Sem ele (chamadas diretas), corrected_bar é a fasquia numérica da PR 2.
    """
    state, pending, run, changes = 'em observação', None, 0, []
    for step in steps:
        games, prob, when = step[:3]
        extra = step[3] if len(step) > 3 else {}
        if certified_from is _PR2:
            certified = None
        else:
            at = extra.get('at')
            certified = certified_from is not None and (at is None or at >= certified_from)
        target = next_status(state, games, prob, corrected_bar, min_settled, certified)
        if target == state:
            pending, run = None, 0
            continue
        run = run + 1 if target == pending else 1
        pending = target
        if run >= CONFIRM_STEPS:
            changes.append({'games': games, 'from': state, 'to': target, 'prob_above': prob, 'known_utc': when})
            state, pending, run = target, None, 0
    return state, pending, changes


def evaluate(signals, registry, min_settled, *, exploratory=False, selected=None, legacy=(), as_of=None):
    """-> (linhas, hipóteses testadas, fasquia de referência 1 − α/testadas; esta já não decide estados).

    selected: {id: (rank, α, hora)} do registo de seleções verificado (certification.selections); None = validação não
    ativada ou registo inválido: nenhuma certificação, nenhuma forte/confirmada (fail-closed). legacy: ids legados (nunca
    certificáveis). A avaliação exploratória nunca certifica.
    """
    if not exploratory:
        signals = [b for b in signals if eligible(b)]
    multi, earlier = multi_matches(signals), earlier_index(signals)
    have = [(b, insights.family(b), set(atoms(b, multi, earlier))) for b in signals]
    rows, matched, born_of, ordered, matches = [], {}, {}, {}, {}
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
        if not exploratory or hid in matched:
            ordered[hid] = ordered_games(match)
        if not exploratory:
            matches[hid] = match
        if not exploratory:
            row['prospective'] = prospective_summary(ordered[hid], born)
            row['evidence_level'] = ('não verificável' if born is None else 'prospetiva' if row['prospective']['games'] >= PROSPECTIVE_MIN else 'exploratória')
            row['_game_roi'] = game_roi(ordered[hid])   # só apresentação (PR 5); retirado antes de publicar
        rows.append(row)
    tested = len(matched)
    by_id = {r['id']: r for r in rows}
    # Certificação: só seleções REGISTADAS em tempo real (rank, α e início fixos), só hipóteses certificáveis.
    certified_at = {}
    for hid, (rank, alpha, t) in sorted((selected or {}).items()) if not exploratory else ():
        if hid not in by_id:
            continue
        ok = certifiable(registry[hid])
        c = confirmation(matches[hid], t, alpha, as_of) if ok else {'games': 0, 'certified_at': None, 'alpha': alpha, 'threshold': round(1 / alpha, 2)}
        by_id[hid]['confirmation'] = dict(c, rank=rank, selected_utc=chronology.iso(t), certifiable=ok, certified_utc=chronology.iso(c['certified_at']))
        del by_id[hid]['confirmation']['certified_at']
        if c['certified_at'] is not None:
            certified_at[hid] = c['certified_at']
    for hid in legacy:
        if hid in by_id and not exploratory:
            by_id[hid]['certification'] = 'legado: não certificável'
    for hid in matched:
        born = born_of[hid]
        if born is None:           # sem registo válido: nunca toma decisões (fail-closed)
            continue
        steps = game_steps(matched[hid], born, prospective_from=born, ordered=ordered[hid])
        r = by_id[hid]
        r['status'], r['pending_status'], r['status_changes'] = stable_status(steps, None, min_settled, certified_from=certified_at.get(hid))
    for r in rows:
        r.setdefault('status', 'em observação')
        r.setdefault('pending_status', None)
        r.setdefault('status_changes', [])
        r.setdefault('confirmation', None)
        born = born_of[r['id']]
        certified = bool(r['confirmation'] and r['confirmation']['certified_utc'])
        if r['status'] in VALIDATED and not certified:    # defesa em profundidade: nunca uma validada sem certificado
            r['status'], r['validation_blocked'] = 'promissora', True
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
        r['entry_status'] = status_of(r['games'], r['prob_above'], None, min_settled, certified) if born is not None else 'em observação'
    return rows, tested, 1 - FWER_ALPHA / max(1, tested)


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


def multiplicity(rows, tested):
    """Sobreposição entre hipóteses: quanta evidência distinta existe por trás dos estados (só descritivo).

    Promissora e abandonada não têm correção para testes múltiplos: são exploratórias. Hipóteses sobre os mesmos jogos sobem
    e descem juntas, por isso N promissoras não são N descobertas. O controlo do erro das validadas está em `validation`.
    """
    tested_rows = [r for r in rows if r['games'] >= HINT_AT]
    sets = lambda rs: len({r['game_set'] for r in rs})
    good = [r for r in tested_rows if r['status'] in ('promissora', 'forte', 'confirmada')]
    return {'tested_now': tested, 'distinct_game_sets_tested': sets(tested_rows), 'distinct_game_sets_promising_or_better': sets(good),
            'promising_or_better': len(good), 'promising_sum_games': sum(r['games'] for r in good),
            'promising_union_games': len(set().union(*(r.get('_games') or set() for r in good))) if good else 0,
            'evidence_levels': {lvl: sum(1 for r in tested_rows if r.get('evidence_level') == lvl) for lvl in ('exploratória', 'prospetiva', 'não verificável')},
            'prospective_categories_tested': {k: sum((r.get('prospective') or {}).get('categories', {}).get(k, 0) for r in tested_rows)
                                              for k in CATEGORIES},
            'note': 'promissora/abandonada are uncorrected and exploratory; expected_by_chance is an expectation only (dependence widens its spread)'}


def validation(rows, ledger, problems, registry, history_unverified=False):
    """O que "forte"/"confirmada" significam, com que garantia, e se a certificação está ativa (registo de seleções)."""
    sel = sorted((r for r in rows if r.get('confirmation')), key=lambda r: r['confirmation']['rank'])
    active = ledger is not None and not problems
    known = ({e['id'] for e in ledger.get('entries') or []} | {x.get('id') for x in ledger.get('legacy') or []}) if active else set()
    candidates = sorted(r['id'] for r in rows if active and r['status'] == 'promissora' and r['id'] not in known and certifiable(registry.get(r['id'])))
    closeness = lambda r: (r['confirmation'].get('log10_e') or 0) - math.log10(r['confirmation']['threshold'])
    return {'method': VALIDATION_METHOD, 'alpha': FWER_ALPHA,
            'state': ('active' if active else 'not_activated' if ledger is None else 'history_unverified' if history_unverified
                      else 'ledger_invalid'),
            'ledger_valid': active, 'ledger_problems': problems if ledger is not None else [],
            'activated_utc': ledger.get('activated_utc') if active else None, 'legacy': len(ledger.get('legacy') or []) if active else 0,
            'ledger_head_sha256': certification.head(ledger) if active else None, 'ledger_entries': len(ledger.get('entries') or []) if active else 0,
            'test': 'test-by-betting e-process on per-game profit at announced odds minus %d %%' % round(insights.ODDS_LOSS * 100),
            'lambdas': list(EPROCESS_LAMBDAS), 'allocation': 'alpha/(j(j+1)) by order of REGISTERED real-time selection (append-only ledger)',
            'evidence': 'one committed bet per game (the first decided), sent and read after the registered selection, decidable, decided '
                        'before any result of the game was observed; games enter a non-overlapping chain (a game is admitted only if the '
                        'previous admitted one was already closed at its decision); a chain game without proven result within %d days '
                        'closes as a loss; only hypotheses whose conditions are all known at the first signal' % OPEN_TIMEOUT_DAYS,
            'as_of_dimensions': sorted(AS_OF_DIMENSIONS),
            'min_games': PROSPECTIVE_MIN, 'also_requires': {'games': STRONG_AT, 'prob_above': PROMOTE_AT, 'keep_prob_above': KEEP_PROMISING},
            'claim': ('P(any tactic ever certified while its expected profit at odds -2 %% is <= 0 on its games) <= %.2f, over the whole '
                      'life of the project from activation, any dependence between hypotheses, continuous monitoring, under the assumptions'
                      % FWER_ALPHA) if active else 'no claim: certification is not active, no tactic can be validated',
            'null': 'for every admitted bet, E[profit at odds -2 % | everything the system knew at its decision] <= 0',
            'assumptions': ['the null above (no edge given all information available at each decision, including earlier chain results)',
                            'membership of a bet uses only dimensions known at its first signal (as_of_dimensions)',
                            'stored observation times are true (verified against git history in the PR 3 audit)',
                            'the ledger is append-only: the in-file hash chain proves only internal consistency; append-only history is '
                            'proven against git history (certification.verify_history), not inside the report'],
            'selected': len(sel), 'certified': [r['id'] for r in sel if r['confirmation']['certified_utc']], 'candidates': candidates,
            'closest': [dict(r['confirmation'], id=r['id']) for r in sorted(sel, key=closeness, reverse=True)[:5]]}


PRESENTATION = 'telegram_presentation_v1'


def game_roi(ordered):
    """ROI médio POR JOGO (apresentação, PR 5): a aposta comprometida de cada jogo decidido (committed_bet, a mesma regra da
    certificação), à odd anunciada e com a perda de odd ODDS_LOSS. ordered = ordered_games(...). -> (roi, roi com perda)."""
    plain, cut = [], []
    for _, _, rows in ordered:
        b = committed_bet(rows)
        p = bet_profit(b) if b is not None else None
        if p is None:
            continue
        plain.append(b['w'] * (b['odds'] - 1) if b['w'] > 0 else float(b['w']))
        cut.append(p)
    return (round(sum(plain) / len(plain), 4), round(sum(cut) / len(cut), 4)) if plain else (None, None)


def presentation(rows, watched=()):
    """Só apresentação (PR 5): nunca decide estados nem certificação. Para as hipóteses com estado, com mudanças de estado
    ou citadas nos sinais em aberto: o que o aviso precisa de mostrar e de verificar (estado pendente, última mudança com o jogo
    que a fixou, certificado da própria hipótese) e os jogos em que a hipótese está presente (para agrupar equivalentes por
    Jaccard; pertença em jogos, nunca resultados)."""
    out = {}
    for r in rows:
        if r['status'] == 'em observação' and not r['status_changes'] and r['id'] not in watched:
            continue
        roi, roi_cut = r.get('_game_roi') or (None, None)
        out[r['id']] = {'status': r['status'], 'pending_status': r['pending_status'], 'changes': len(r['status_changes']),
                        'last_change': r['status_changes'][-1] if r['status_changes'] else None,
                        'family': r['family'], 'conditions': r['conditions'], 'expect': r.get('expect'),
                        'games': r.get('games'), 'n': r.get('n'), 'roi': r.get('roi'), 'game_roi': roi, 'game_roi_cut': roi_cut,
                        'prob_above': r.get('prob_above'), 'prob_above_cut': r.get('prob_above_cut'), 'edge': r.get('edge'),
                        'edge_ci': r.get('edge_ci'), 'prospective_games': (r.get('prospective') or {}).get('games'),
                        'born_utc': r.get('born_utc'), 'evidence_level': r.get('evidence_level'), 'confirmation': r.get('confirmation'),
                        'game_ids': sorted(r['_games']) if r.get('_games') is not None else None}
    return {'version': PRESENTATION, 'hypotheses': out}


def build(signals, registry, min_settled, ledger=None, *, history_problems=None, as_of=None):
    """ledger: registo de seleções para certificação (certification.load); None = não ativado: nenhuma tática validada.

    history_problems: resultado de certification.history_proof (revisão #5911845265, P1-H). Com registo, a certificação só
    fica ativa se a prova de integridade histórica tiver sido feita e não tiver problemas; None (prova não feita) também
    desliga tudo (fail-closed). as_of: relógio do relatório, para fechar como perda os jogos pendentes além do prazo.
    """
    # Relógio explícito: só o que o sistema sabia em `as_of` (sinais ainda não lidos e resultados ainda não vistos não contam).
    all_signals = chronology.as_of_view(signals, as_of) if as_of is not None else signals
    signals = [b for b in all_signals if eligible(b)]
    excluded = {}
    for b in all_signals:
        for reason in exclusion_reasons(b):
            excluded[reason] = excluded.get(reason, 0) + 1
    exploratory_rows, _, _ = evaluate(all_signals, registry, min_settled, exploratory=True)
    exploratory_counts = {}
    for r in exploratory_rows:
        exploratory_counts[r['status']] = exploratory_counts.get(r['status'], 0) + 1
    selected, problems = certification.selections(ledger, registry)
    history_unverified = ledger is not None and not problems and history_problems != []
    if history_unverified:
        problems = ['history: %s' % p for p in (history_problems or ['integrity proof not performed'])]
    legacy = {x.get('id') for x in (ledger or {}).get('legacy') or [] if isinstance(x, dict)} if not problems else set()
    rows, tested, _ = evaluate(signals, registry, min_settled, selected=selected if not problems else None, legacy=legacy, as_of=as_of)
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
            w = watch.setdefault(b['key'], {'date_utc': b['date_utc'], 'match': ' — '.join(b['teams'] or []), 'match_id': game_identity(b), 'type': b['type'], 'market': b['market'],
                                            'selection': b['selection'], 'odds': b['odds'], 'kickoff_utc': b['kickoff_utc'], 'reasons': []})
            w['reasons'].append({'id': r['id'], 'status': r['status'], 'n': r['n'], 'games': r['games'], 'hit_rate': r['hit_rate'], 'prob_above': r['prob_above'],
                                 'edge': r.get('edge'), 'edge_ci': r.get('edge_ci'), 'prob_above_cut': r.get('prob_above_cut'),
                                 'prudent_rate': r.get('prudent_rate'), 'break_even': r.get('break_even'),
                                 'certified': bool(r.get('confirmation') and r['confirmation']['certified_utc'])})
    mult, valid = multiplicity(rows, tested), validation(rows, ledger, problems, registry, history_unverified)
    shown = presentation(rows, {x['id'] for w in watch.values() for x in w['reasons']})
    for r in rows:
        r['open'] = len(r['open'])
        r.pop('_games', None)
        r.pop('_game_roi', None)
    rows.sort(key=lambda r: (-(r['prob_above'] if r['prob_above'] is not None and r['n'] >= 5 else -1), -r['n']))
    return {'evidence_scope': 'eligible_bets_only', 'status_rules': STATUS_RULES,
            'eligibility': {'total': len(all_signals), 'eligible': len(signals), 'excluded': len(all_signals) - len(signals), 'reasons': excluded},
            'exploratory': {'scope': 'all_signals_not_for_recommendations', 'counts': exploratory_counts,
                            'hypotheses': [{k: r[k] for k in ('id', 'n', 'games', 'roi', 'status')} for r in sorted(exploratory_rows, key=lambda r: (-r['n'], r['id']))[:150]]},
            'hypotheses': len(rows), 'tested': tested, 'corrected_bar': None, 'counts': counts, 'board': brd, 'agenda': agd,
            'track': track_record(rows), 'walk_forward': walk_forward(signals, registry), 'multiplicity': mult, 'validation': valid,
            'expected_by_chance': round(0.05 * tested, 1), 'top': [{k: r.get(k) for k in ('id', 'family', 'conditions', 'n', 'hits', 'hit_rate', 'break_even', 'prob_above', 'roi', 'status', 'tradable', 'expect',
                                                                                                    'games', 'bets', 'edge', 'edge_ci', 'prob_above_cut', 'prob_adds', 'pending_status', 'entry_status',
                                                                                                    'born_utc', 'born_verified', 'evidence_level', 'prospective', 'at_birth', 'result_time_unknown', 'game_set',
                                                                                                    'confirmation', 'validation_blocked', 'certification')}
                    | {'status_changes': len(r['status_changes']), 'last_change': r['status_changes'][-1] if r['status_changes'] else None}
                    for r in rows if r['n'] >= 5][:150],
            'status_list': {r['id']: r['status'] for r in rows if r['status'] != 'em observação'},
            'abandoned': [r for r in rows if r['status'] == 'abandonada'][:10],
            'llm': sorted((r for r in rows if r['source'] == 'llm'), key=lambda r: r['born_utc'], reverse=True)[:20],
            'llm_total': sum(1 for r in rows if r['source'] == 'llm'),
            'watch': sorted(watch.values(), key=lambda w: w['date_utc'], reverse=True)[:30],
            'presentation': shown,
            'rules': {'min_support': MIN_SUPPORT, 'max_conditions': MAX_CONDITIONS, 'hint_at': HINT_AT, 'strong_at': STRONG_AT, 'confirm_at': min_settled,
                      'unit': 'independent_games', 'status_rules': STATUS_RULES, 'promote_at': PROMOTE_AT, 'keep_promising': KEEP_PROMISING,
                      'abandon_at': ABANDON_AT, 'keep_abandoned': KEEP_ABANDONED, 'confirm_steps': CONFIRM_STEPS,
                      'fwer_alpha': FWER_ALPHA, 'validation_method': VALIDATION_METHOD, 'prospective_min': PROSPECTIVE_MIN,
                      'result_time': 'first_observation_by_the_system', 'observation_margin_min': chronology.OBSERVATION_MARGIN_MIN,
                      'decisions_from': 'born_utc'}}


def track_record(rows):
    """Balanço DESCRITIVO do processo de descoberta, por origem (PR 4, decisão do proprietário de 15/11/2026).

    Só evidência prospetiva (a definição única da PR 3: jogos operacionais posteriores ao registo) e lucro por jogo. Não
    julga nem confirma nenhuma hipótese: uma probabilidade ou taxa de acerto acima de 50 % não é prova de nada e nunca é
    apresentada como tal, nem aqui nem ao modelo de linguagem. A avaliação incremental da LLM está em scangoal.llm_eval
    (protocolo audits/pr4-llm-protocol-v5.md).
    """
    groups, llm = {}, []
    for r in rows:
        if not r['conditions']:
            continue
        key = 'gerador' if r['source'] == 'gerador' else '%s/%s' % (r['source'], r.get('mode') or 'novas')
        g = groups.setdefault(key, {'hypotheses': 0, 'with_prospective_evidence': 0, 'prospective_games': 0})
        g['hypotheses'] += 1
        p = r.get('prospective') or {}
        if (p.get('games') or 0) < TRACK_MIN:
            continue
        g['with_prospective_evidence'] += 1
        g['prospective_games'] += p['games']
        if r['source'] == 'llm':
            llm.append({'id': r['id'], 'games': p['games'], 'roi': p.get('roi'), 'expect': r.get('expect') or 'acima', 'mode': r.get('mode')})
    return {'by_source': groups, 'min_results': TRACK_MIN, 'unit': 'prospective_independent_games', 'criterion': None,
            'meaning': 'descriptive only: no hypothesis is confirmed or refuted here; see audits/pr4-llm-protocol-v5.md',
            'llm_with_prospective_evidence': len(llm), 'llm': sorted(llm, key=lambda h: (-h['games'], h['id']))[:12]}


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

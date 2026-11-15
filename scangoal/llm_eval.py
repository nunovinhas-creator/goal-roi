"""Avaliação incremental das hipóteses do modelo de linguagem (PR 4, protocolo `llm_incremental_v1`).

Implementa audits/pr4-llm-protocol.md (o sha256 do ficheiro está fixado em PROTOCOL_SHA256; outra versão é recusada).
Puro e determinístico: sem rede, sem segredos, sem escrita. O git só é lido (histórico do registo e do protocolo).

Pergunta: as hipóteses `llm` escolhem jogos com mais lucro por jogo, depois do seu registo, do que hipóteses comparáveis do
gerador que já existiam, avaliadas no mesmo intervalo? Não mede vantagem contra o mercado nem certifica nada. A taxa de
acerto e `prob_above` são só descrição: nenhuma percentagem acima de 50 % entra num critério.
"""
import hashlib
import json
import math
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import brain, chronology, insights
from .eligibility import eligible
from .equivalence import Index, Redundancy, expectation, groups

PROTOCOL = 'llm_incremental_v1'
PROTOCOL_FILE = 'audits/pr4-llm-protocol.md'
PROTOCOL_SHA256 = '8b108bf91effb6701bd38e4a582e840804b86e1075b8af55b7976872ff1a5cb0'
REGISTRY_FILE = 'data/hypotheses.json'
TAU, SENSITIVITY, GROUP_MIN_GAMES = 0.85, (0.80, 0.90), 8
K, K_MIN = 20, 5
REPLICATES, SEED, ALPHA = 10_000, 20261115, 0.05
MIN_GROUPS, MIN_GROUP_GAMES, MIN_GAMES, MIN_DAYS, MAX_DAY_SHARE, MIN_REPLICATES = 10, 10, 200, 14, 0.20, 9_000
SECONDARY = ('S1_family_baseline', 'S2_mode_novas', 'S3_mode_refinar', 'S4_refinar_vs_parents', 'S5_evitar_vs_baseline',
             'S6_tau_0.80', 'S7_tau_0.90')
VERDICTS = ('não verificada', 'evidência insuficiente', 'sem utilidade incremental demonstrada',
            'utilidade incremental estatisticamente significativa (não é prova de vantagem nem certificação)')
UTC = timezone.utc


# --------------------------------------------------------------------------------------------- provas pelo histórico git

def _git(root, *args, data=None):
    return subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True, check=True).stdout


def _versions(root, rel, commits, chunk=16):
    """Gera (sha, hora, conteúdo ou None) das versões de `rel` nos commits dados, poucos de cada vez (memória limitada)."""
    for i in range(0, len(commits), chunk):
        part = commits[i:i + chunk]
        out, pos = _git(root, 'cat-file', '--batch', data=''.join('%s:%s\n' % (sha, rel) for sha, _ in part).encode()), 0
        for sha, when in part:
            end = out.index(b'\n', pos)
            header = out[pos:end].decode().split()
            if header[-1] == 'missing':
                pos = end + 1
                yield sha, when, None
                continue
            size = int(header[2])
            yield sha, when, out[end + 1:end + 1 + size]
            pos = end + 1 + size + 1


def registry_history(root, ref, rel=REGISTRY_FILE):
    """-> ({id: hora da primeira publicação}, problemas). Prova de registo só a crescer pela história de primeiro-pai de `ref`:
    cada versão contém todas as entradas anteriores com conteúdo idêntico; horas de commit não decrescentes. Fail-closed."""
    try:
        if _git(root, 'rev-parse', '--is-shallow-repository').decode().strip() != 'false':
            return {}, ['history unavailable: shallow repository']
        log = _git(root, 'log', '--first-parent', '--reverse', '--format=%H %cI', ref, '--', rel).decode().split('\n')
    except (OSError, subprocess.CalledProcessError):
        return {}, ['history unavailable: no git repository']
    commits = [tuple(line.split()) for line in log if line.strip()]
    if not commits:
        return {}, ['registry never published']
    first, prev, last_time, problems = {}, {}, None, []
    try:
        for sha, when, body in _versions(root, rel, commits):
            t = chronology.parse_utc(when)
            if t is None or (last_time is not None and t < last_time):
                problems.append('commit time not monotonic at %s' % sha[:12])
            last_time = t if t is not None else last_time
            if body is None:
                problems.append('registry deleted at %s' % sha[:12])
                continue
            try:
                cur = json.loads(body)
            except ValueError:
                problems.append('unreadable registry at %s' % sha[:12])
                continue
            if not isinstance(cur, dict):
                problems.append('registry not an object at %s' % sha[:12])
                continue
            for hid, entry in prev.items():
                if hid not in cur:
                    problems.append('entry removed at %s: %s' % (sha[:12], hid))
                elif cur[hid] != entry:
                    problems.append('entry rewritten at %s: %s' % (sha[:12], hid))
            for hid in cur:
                first.setdefault(hid, t)
            prev = cur
    except (OSError, ValueError, subprocess.CalledProcessError):
        return {}, ['history unavailable: cannot read versions']
    return first, problems[:50]


def protocol_proof(root, commit):
    """-> (hora P do commit que introduziu o protocolo, problemas). Exige o conteúdo fixado em PROTOCOL_SHA256."""
    try:
        sha = _git(root, 'rev-parse', '--verify', '%s^{commit}' % commit).decode().strip()
        body = _git(root, 'show', '%s:%s' % (sha, PROTOCOL_FILE))
        adds = _git(root, 'log', '--reverse', '--format=%H %cI', sha, '--', PROTOCOL_FILE).decode().split('\n')
    except (OSError, subprocess.CalledProcessError):
        return None, ['protocol unavailable at %s' % commit]
    problems = []
    if hashlib.sha256(body).hexdigest() != PROTOCOL_SHA256:
        problems.append('protocol content differs from the registered one')
    intro = [line.split() for line in adds if line.strip()]
    if not intro or intro[0][0] != sha:
        problems.append('protocol commit is not the one that introduced the file')
    return (chronology.parse_utc(intro[0][1]) if intro else None), problems


def births(registry, first_seen, verified):
    """{id: born_eff ou None}. Com prova: max(born_utc, primeira publicação); sem publicação provada: None (fail-closed).
    Sem histórico verificado: born_utc (só para descrição; a avaliação fica não verificada)."""
    out = {}
    for hid, h in registry.items():
        born = chronology.born(h)
        if born is None:
            out[hid] = None
        elif not verified:
            out[hid] = born
        else:
            pub = first_seen.get(hid)
            out[hid] = max(born, pub) if pub is not None else None
    return out


# --------------------------------------------------------------------------------------------- evidência e comparação

def admitted(match, start):
    """{jogo: (dia, lucro)} dos jogos PROSPETIVOS depois de `start` (definição única da PR 3), um voto por jogo: a aposta
    comprometida (a primeira decidida antes de qualquer resultado do jogo), à odd anunciada −2 %."""
    out = {}
    if start is None:
        return out
    for _, key, rows in brain.ordered_games(match):
        if brain.game_category(rows, start) != 'prospective':
            continue
        bet = brain.committed_bet(brain.game_core(rows))
        p = brain.bet_profit(bet) if bet is not None else None
        if p is not None:
            out[key] = (chronology.decision_time(bet).date().isoformat(), p, chronology.decision_time(bet))
    return out


def _by_day(units):
    """units: [(peso, {jogo: (dia, lucro, t)})] -> {dia: [soma das médias por jogo, n.º de jogos]} (média ponderada por jogo)."""
    games = {}
    for w, u in units:
        for g, (day, p, _) in u.items():
            e = games.setdefault(g, [day, 0.0, 0.0])
            e[0] = min(e[0], day)
            e[1] += w * p
            e[2] += w
    days = {}
    for day, s, w in games.values():
        d = days.setdefault(day, [0.0, 0])
        d[0] += s / w
        d[1] += 1
    return days, len(games)


def compare(test_units, ctrl_units):
    """D = média por jogo do lado em teste − média por jogo do lado de comparação; bootstrap por blocos de dia (H1: D > 0)."""
    tx, nt = _by_day(test_units)
    cx, nc = _by_day(ctrl_units)
    res = {'games_test': nt, 'games_control': nc, 'days_test': len(tx), 'days': len(set(tx) | set(cx)),
           'max_day_share_test': round(max((v[1] for v in tx.values()), default=0) / nt, 6) if nt else None,
           'd': None, 'lower_95': None, 'p': None, 'replicates': 0}
    if not nt or not nc:
        return res
    d = sum(v[0] for v in tx.values()) / nt - sum(v[0] for v in cx.values()) / nc
    days = sorted(set(tx) | set(cx))
    rng, stats = random.Random(SEED), []
    zero = (0.0, 0)
    for _ in range(REPLICATES):
        st = sn = cs = cn = 0
        for _ in days:
            day = days[rng.randrange(len(days))]
            a, b = tx.get(day, zero), cx.get(day, zero)
            st, sn, cs, cn = st + a[0], sn + a[1], cs + b[0], cn + b[1]
        if sn and cn:
            stats.append(st / sn - cs / cn)
    stats.sort()
    res.update(d=round(d, 6), replicates=len(stats))
    if stats:
        res['lower_95'] = round(stats[max(0, math.ceil(0.05 * len(stats)) - 1)], 6)
        res['p'] = round((1 + sum(1 for x in stats if x <= 0)) / (1 + len(stats)), 6)
    return res


def holm(pvalues):
    """p ajustados de Holm (m = número de entradas, fixo pelo protocolo); None conta como 1."""
    ps = [1.0 if p is None else p for p in pvalues]
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = round(running, 6)
    return adj


def enough(primary, group_games=None):
    """Mínimos do §7 do protocolo -> lista de mínimos falhados (vazia = suficiente). group_games só na análise primária."""
    fails = []
    if group_games is not None and sum(1 for n in group_games if n >= MIN_GROUP_GAMES) < MIN_GROUPS:
        fails.append('groups with >= %d games < %d' % (MIN_GROUP_GAMES, MIN_GROUPS))
    if primary['games_test'] < MIN_GAMES:
        fails.append('llm games < %d' % MIN_GAMES)
    if primary['games_control'] < MIN_GAMES:
        fails.append('control games < %d' % MIN_GAMES)
    if primary['days_test'] < MIN_DAYS:
        fails.append('days with llm games < %d' % MIN_DAYS)
    if primary['max_day_share_test'] is None or primary['max_day_share_test'] > MAX_DAY_SHARE:
        fails.append('one day holds more than %d %% of llm games' % round(MAX_DAY_SHARE * 100))
    if primary['replicates'] < MIN_REPLICATES:
        fails.append('valid replicates < %d' % MIN_REPLICATES)
    return fails


def _r(x):
    return None if x is None else round(x, 6)


def evaluate(signals, registry, *, as_of, born_eff, protocol_time, problems=()):
    """Avaliação completa (pura). signals: sinais do relatório (a vista `as_of` e a elegibilidade aplicam-se aqui).
    born_eff: {id: datetime ou None} (births). protocol_time: P. problems: falhas de prova (histórico, protocolo)."""
    view = [b for b in chronology.as_of_view(signals, as_of) if eligible(b)]
    multi, earlier = brain.multi_matches(view), brain.earlier_index(view)
    have = [(b, insights.family(b), frozenset(brain.atoms(b, multi, earlier))) for b in view]
    index = Index(view)

    def match(fam, conds):
        conds = {tuple(c) for c in conds}
        return [b for b, f, at in have if f == fam and conds <= at]

    times = [chronology.signal_time(b) or as_of for b in view]

    def upto(t):
        return sum(1 << i for i, s in enumerate(times) if s <= t)

    cache = {}

    def games_of(hid, start):
        key = (hid, start)
        if key not in cache:
            h = registry[hid] if hid in registry else {'family': hid[len('family:'):], 'conditions': []}
            cache[key] = admitted(match(h['family'], h['conditions']), start)
        return cache[key]

    llm_ids = sorted(hid for hid, h in registry.items() if h.get('source') == 'llm')
    valid = [hid for hid in llm_ids if born_eff.get(hid) is not None and (born_eff[hid] <= as_of)]
    excluded = {'no_proven_birth': sum(1 for hid in llm_ids if born_eff.get(hid) is None),
                'born_after_as_of': sum(1 for hid in llm_ids if born_eff.get(hid) is not None and born_eff[hid] > as_of)}

    # Duplicados à nascença (descrição): redundância extensional demonstrável no instante de registo de cada proposta.
    duplicates = []
    for hid in valid:
        h, t = registry[hid], born_eff[hid]
        before = {k: v for k, v in registry.items() if k != hid and born_eff.get(k) is not None and born_eff[k] < t}
        why = Redundancy(chronology.as_of_view(view, t), before).reason(h['family'], h['conditions'])   # o que o sistema já tinha lido
        if why:
            duplicates.append({'id': hid, 'reason': why[0], 'with': why[1]})

    order = sorted(valid, key=lambda hid: (born_eff[hid], hid))
    members = {hid: index.games(registry[hid]['family'], registry[hid]['conditions']) for hid in order}

    def grouping(tau):
        return groups([(hid, registry[hid], members[hid]) for hid in order], tau, GROUP_MIN_GAMES)

    gens = sorted(hid for hid, h in registry.items() if h.get('source') == 'gerador' and born_eff.get(hid) is not None
                  and not any(c[0] in brain.POST for c in h['conditions']))

    gen_mask = {c: index.mask(registry[c]['family'], registry[c]['conditions']) for c in gens}

    def controls(hid):
        h, t = registry[hid], born_eff[hid]
        size = min(len(h['conditions']), brain.MAX_CONDITIONS)
        own, tm = index.mask(h['family'], h['conditions']), upto(t)
        s = (own & tm).bit_count()
        cands = []
        for c in gens:
            g = registry[c]
            if g['family'] != h['family'] or len(g['conditions']) != size or born_eff[c] >= t or gen_mask[c] == own:
                continue
            if s and s / 2 <= (gen_mask[c] & tm).bit_count() <= 2 * s:
                cands.append(c)
        cands.sort(key=lambda c: hashlib.sha256(('%s\n%s' % (hid, c)).encode('utf-8')).hexdigest())
        return cands[:K]

    def parents(hid):
        h, t = registry[hid], born_eff[hid]
        conds = [tuple(c) for c in h['conditions']]
        out = []
        for i in range(len(conds)):
            pid = brain.hyp_id(h['family'], conds[:i] + conds[i + 1:])
            if pid in registry and born_eff.get(pid) is not None and born_eff[pid] < t and pid != hid:
                out.append(pid)
        return sorted(set(out))

    starts = {hid: max(born_eff[hid], protocol_time) if protocol_time is not None else None for hid in valid}
    ctrl_of = {hid: controls(hid) for hid in valid}   # sem ler resultados; a primária só usa os de expectativa 'acima' (reps_of)

    def side(reps, ref):
        """Unidades (peso, jogos) do lado em teste e do lado de comparação `ref` (controls|baseline|parents)."""
        test, other = [], []
        for hid in reps:
            t = starts[hid]
            test.append((1.0, games_of(hid, t)))
            if ref == 'controls':
                for c in ctrl_of[hid]:
                    other.append((1.0 / len(ctrl_of[hid]), games_of(c, t)))
            elif ref == 'parents':
                ps = parents(hid)
                for p in ps:
                    other.append((1.0 / len(ps), games_of(p, t)))
            else:
                other.append((1.0, games_of('family:' + registry[hid]['family'], t)))
        return test, other

    def reps_of(grp, *, expect='acima', modes=None, need_controls=True):
        out = []
        for g in grp:
            hid = g[0]
            h = registry[hid]
            if expectation(h) != expect or (modes is not None and (h.get('mode') or 'novas') not in modes):
                continue
            if need_controls and len(ctrl_of.get(hid) or ()) < K_MIN:
                continue
            out.append(hid)
        return out

    grp = grouping(TAU)
    primary_reps = reps_of(grp)
    primary = compare(*side(primary_reps, 'controls'))
    group_games = [len(games_of(hid, starts[hid])) for hid in primary_reps]
    fails = enough(primary, group_games)

    def secondary(name):
        if name == 'S1_family_baseline':
            return compare(*side(reps_of(grp, need_controls=False), 'baseline'))
        if name == 'S2_mode_novas':
            return compare(*side(reps_of(grp, modes={'novas'}), 'controls'))
        if name == 'S3_mode_refinar':
            return compare(*side(reps_of(grp, modes={'refinar'}), 'controls'))
        if name == 'S4_refinar_vs_parents':
            return compare(*side([h for h in reps_of(grp, modes={'refinar'}, need_controls=False) if parents(h)], 'parents'))
        if name == 'S5_evitar_vs_baseline':
            test, base = side(reps_of(grp, expect='abaixo', need_controls=False), 'baseline')
            return compare(base, test)   # H1: base da família − evitar > 0
        reps = reps_of(grouping(float(name.split('_')[-1])))
        return dict(compare(*side(reps, 'controls')), group_games=[len(games_of(h, starts[h])) for h in reps])

    sec = {name: secondary(name) for name in SECONDARY}
    for res in sec.values():
        res['insufficient'] = enough(res, res.pop('group_games', None))
    adj = holm([None if sec[n]['insufficient'] else sec[n]['p'] for n in SECONDARY])
    for name, a in zip(SECONDARY, adj):
        sec[name]['p_holm'] = a
        sec[name]['significant_holm'] = a <= ALPHA

    if problems:
        verdict = VERDICTS[0]
    elif fails:
        verdict = VERDICTS[1]
    elif primary['p'] is None or primary['p'] > ALPHA:
        verdict = VERDICTS[2]
    else:
        verdict = VERDICTS[3]

    # Janela pós-registo e pré-protocolo: só descrição (sem teste, sem p, sem intervalo); nunca sustenta conclusões.
    def before_p(u):
        return {g: v for g, v in u.items() if protocol_time is None or v[2] < protocol_time}
    pre_test, pre_ctrl = [], []
    for hid in primary_reps:
        pre_test.append((1.0, before_p(games_of(hid, born_eff[hid]))))
        pre_ctrl += [(1.0 / len(ctrl_of[hid]), before_p(games_of(c, born_eff[hid]))) for c in ctrl_of[hid]]
    tx, nt = _by_day(pre_test)
    cx, nc = _by_day(pre_ctrl)
    pre_protocol = {'games_test': nt, 'games_control': nc, 'days_test': len(tx),
                    'mean_profit_test': _r(sum(v[0] for v in tx.values()) / nt) if nt else None,
                    'mean_profit_control': _r(sum(v[0] for v in cx.values()) / nc) if nc else None,
                    'meaning': 'descriptive only: post-registration but observed before the protocol; no test, no conclusion'}

    group_of = {hid: i for i, g in enumerate(grp) for hid in g}
    table = []
    for hid in order:
        h = registry[hid]
        prot = games_of(hid, starts[hid])
        pre = {g: v for g, v in games_of(hid, born_eff[hid]).items() if protocol_time is None or v[2] < protocol_time}
        rows = match(h['family'], h['conditions'])
        hits = insights.Votes()
        for _, _, gr in brain.ordered_games(rows):
            if brain.game_category(gr, born_eff[hid]) == 'prospective':
                for b in brain.game_core(gr):
                    hits.add(b)
        hh, mm, be, n = hits.totals()
        table.append({'id': hid, 'group': group_of[hid], 'representative': grp[group_of[hid]][0] == hid, 'mode': h.get('mode'),
                      'expect': expectation(h), 'born_utc': h.get('born_utc'), 'born_eff': chronology.iso(born_eff[hid]),
                      'membership_games': len(members[hid]), 'controls': len(ctrl_of.get(hid) or ()), 'parents': len(parents(hid)),
                      'controls_sha256': hashlib.sha256('\n'.join(ctrl_of.get(hid) or ()).encode('utf-8')).hexdigest()[:16],
                      'protocol_games': len(prot), 'protocol_mean_profit': _r(sum(v[1] for v in prot.values()) / len(prot)) if prot else None,
                      'pre_protocol_games': len(pre), 'pre_protocol_mean_profit': _r(sum(v[1] for v in pre.values()) / len(pre)) if pre else None,
                      'descriptive_hit_rate': _r(hh / (hh + mm)) if hh + mm else None, 'descriptive_break_even': _r(be) if n else None})

    return {'protocol': PROTOCOL, 'protocol_sha256': PROTOCOL_SHA256, 'protocol_time_utc': chronology.iso(protocol_time),
            'as_of_utc': chronology.iso(as_of), 'verified': not problems, 'proof_problems': list(problems),
            'verdict': verdict, 'insufficient': fails,
            'parameters': {'tau': TAU, 'sensitivity': list(SENSITIVITY), 'group_min_games': GROUP_MIN_GAMES, 'k': K, 'k_min': K_MIN,
                           'replicates': REPLICATES, 'seed': SEED, 'alpha': ALPHA, 'min_groups': MIN_GROUPS, 'min_group_games': MIN_GROUP_GAMES,
                           'min_games': MIN_GAMES, 'min_days': MIN_DAYS, 'max_day_share': MAX_DAY_SHARE, 'min_replicates': MIN_REPLICATES,
                           'odds_loss': insights.ODDS_LOSS, 'holm_m': len(SECONDARY)},
            'population': {'eligible_signals': len(view), 'llm_hypotheses': len(llm_ids), 'evaluated': len(valid), 'excluded': excluded,
                           'groups': len(grp), 'groups_sensitivity': {str(t): len(grouping(t)) for t in SENSITIVITY},
                           'primary_representatives': len(primary_reps),
                           'without_controls': sum(1 for hid in ctrl_of if len(ctrl_of[hid]) < K_MIN),
                           'duplicates_at_birth': duplicates,
                           'by_mode': {m: sum(1 for hid in valid if (registry[hid].get('mode') or 'novas') == m) for m in ('novas', 'refinar', 'evitar')}},
            'primary': primary, 'secondary': sec, 'pre_protocol': pre_protocol, 'hypotheses': table,
            'notes': ['hit rates and prob_above are descriptive only; no criterion uses them',
                      'pre-protocol games are post-registration evidence gathered before the protocol: descriptive only']}


def page_summary(signals, registry):
    """Resumo barato para a página (sem git: nunca há conclusão científica no relatório de produção)."""
    llm = sorted(hid for hid, h in registry.items() if h.get('source') == 'llm' and chronology.born(h) is not None)
    index = Index([b for b in signals if eligible(b)])
    order = sorted(llm, key=lambda hid: (chronology.born(registry[hid]), hid))
    grp = groups([(hid, registry[hid], index.games(registry[hid]['family'], registry[hid]['conditions'])) for hid in order], TAU, GROUP_MIN_GAMES)
    return {'protocol': PROTOCOL, 'verdict': VERDICTS[0], 'llm_hypotheses': len(llm), 'groups': len(grp),
            'reason': 'the production report has no full git history: scientific evaluation only in scripts/audit_pr4_llm.py',
            'report': 'audits/pr4-llm-evaluation.md'}


def render(res):
    """Relatório científico em Markdown a partir do resultado (determinístico)."""
    p, pop, prim = res['parameters'], res['population'], res['primary']
    fmt = lambda x: '—' if x is None else ('%.4f' % x)
    lines = ['# PR 4 — Avaliação incremental das hipóteses LLM (protocolo `%s`)' % res['protocol'], '',
             'Gerado por `scripts/audit_pr4_llm.py` a partir de dados de um commit fixo; reprodutível byte a byte.', '',
             '## Veredicto', '', '**%s**' % res['verdict'], '']
    if res['proof_problems']:
        lines += ['Falhas de prova (nenhuma conclusão científica):', ''] + ['- %s' % x for x in res['proof_problems']] + ['']
    if res['insufficient']:
        lines += ['Mínimos do protocolo não cumpridos:', ''] + ['- %s' % x for x in res['insufficient']] + ['']
    lines += ['## Pergunta', '',
              'As hipóteses do modelo de linguagem escolhem jogos com mais lucro por jogo (odd anunciada −2 %), depois do seu registo,',
              'do que hipóteses comparáveis do gerador que já existiam, no mesmo intervalo? Não é uma medida de vantagem contra o',
              'mercado e não certifica nenhuma tática. Taxas de acerto, incluindo as superiores a 50 %, são só descrição.', '',
              '## Entradas', '',
              '- Protocolo: `%s`, sha256 `%s`, início P = %s' % (res['protocol'], res['protocol_sha256'], res['protocol_time_utc']),
              '- Avaliado em: %s' % res['as_of_utc']]
    for k, v in sorted((res.get('inputs') or {}).items()):
        lines.append('- %s: `%s`' % (k, v))
    lines += ['', '## População', '',
              '| Medida | Valor |', '|---|---|',
              '| Sinais elegíveis na vista do instante | %d |' % pop['eligible_signals'],
              '| Hipóteses LLM no registo | %d |' % pop['llm_hypotheses'],
              '| Avaliadas (nascimento provado) | %d |' % pop['evaluated'],
              '| Excluídas: sem nascimento provado | %d |' % pop['excluded']['no_proven_birth'],
              '| Grupos de equivalência (τ = %s) | %d |' % (str(p['tau']).replace('.', ','), pop['groups']),
              '| Grupos com τ = 0,80 / 0,90 | %s / %s |' % (pop['groups_sensitivity']['0.8'], pop['groups_sensitivity']['0.9']),
              '| Representantes na análise primária | %d |' % pop['primary_representatives'],
              '| Sem controlos suficientes (< %d) | %d |' % (p['k_min'], pop['without_controls']),
              '| Duplicados extensionais à nascença | %d |' % len(pop['duplicates_at_birth']),
              '| Por modo (novas / refinar / evitar) | %d / %d / %d |' % (pop['by_mode']['novas'], pop['by_mode']['refinar'], pop['by_mode']['evitar']),
              '', '## Comparação primária', '',
              'D = lucro médio por jogo das hipóteses LLM − lucro médio por jogo dos controlos do gerador (pesos 1/K), jogos prospetivos',
              'desde max(nascimento provado, P). Bootstrap por blocos de dia, %d réplicas, semente %d; H1: D > 0; α = %.2f.' % (p['replicates'], p['seed'], p['alpha']), '',
              '| Jogos LLM | Jogos controlo | Dias LLM | Maior fração num dia | D | Limite inferior 95 % | p | Réplicas válidas |',
              '|---|---|---|---|---|---|---|---|',
              '| %d | %d | %d | %s | %s | %s | %s | %d |' % (prim['games_test'], prim['games_control'], prim['days_test'], fmt(prim['max_day_share_test']),
                                                       fmt(prim['d']), fmt(prim['lower_95']), fmt(prim['p']), prim['replicates']),
              '', '## Janela pós-registo e pré-protocolo (só descrição)', '',
              'Mesmos representantes e controlos, jogos prospetivos desde o nascimento provado e decididos antes de P. Não é um teste:',
              'foi observada antes de o protocolo existir e não tem p-valor nem intervalo.', '',
              '| Jogos LLM | Dias | Lucro médio por jogo LLM | Jogos controlo | Lucro médio por jogo controlo |', '|---|---|---|---|---|',
              '| %d | %d | %s | %d | %s |' % (res['pre_protocol']['games_test'], res['pre_protocol']['days_test'], fmt(res['pre_protocol']['mean_profit_test']),
                                          res['pre_protocol']['games_control'], fmt(res['pre_protocol']['mean_profit_control'])),
              '', '## Análises adicionais (exploratórias, Holm m = %d)' % p['holm_m'], '',
              '| Análise | Jogos em teste | Jogos de comparação | D | p | p Holm |', '|---|---|---|---|---|---|']
    for name in SECONDARY:
        s = res['secondary'][name]
        lines.append('| %s | %d | %d | %s | %s | %s |' % (name, s['games_test'], s['games_control'], fmt(s['d']), fmt(s['p']), fmt(s['p_holm'])))
    lines += ['', 'Nenhuma análise adicional sustenta sozinha um veredicto.', '',
              '## Hipóteses (descrição)', '',
              '| Hipótese | Grupo | Modo | Jogos no protocolo | Lucro/jogo | Jogos pré-protocolo | Lucro/jogo | Controlos |', '|---|---|---|---|---|---|---|---|']
    for h in res['hypotheses']:
        lines.append('| %s%s | %d | %s | %d | %s | %d | %s | %d |' % (h['id'].replace('|', '\\|'), ' (repr.)' if h['representative'] and h['group'] is not None else '',
                                                              h['group'], h['mode'] or '—', h['protocol_games'], fmt(h['protocol_mean_profit']),
                                                              h['pre_protocol_games'], fmt(h['pre_protocol_mean_profit']), h['controls']))
    if pop['duplicates_at_birth']:
        lines += ['', '## Duplicados extensionais à nascença', '']
        lines += ['- %s — %s — %s' % (d['id'], d['reason'], d['with']) for d in pop['duplicates_at_birth']]
    lines += ['', '## Ameaças à validade e limitações', '',
              '- A hora de publicação é a hora do commit (committer date) na história de primeiro-pai; um commit com data falsificada',
              '  não seria detetado por este método (as horas têm de ser não decrescentes).',
              '- Os controlos são hipóteses do gerador com tamanho e apoio comparáveis; não esgotam o que um analista humano escolheria.',
              '- Hipóteses diferentes partilham jogos: por isso a unidade é o jogo (um voto por jogo e por lado) e o bootstrap é por dia.',
              '- A janela pré-protocolo é pós-registo, mas foi observada antes de o protocolo existir: só descrição.',
              '- O resultado não altera estados, certificação, avisos Telegram ou o registo de hipóteses.', '']
    return '\n'.join(lines)


def dumps(res):
    return json.dumps(res, indent=1, ensure_ascii=False, sort_keys=True) + '\n'

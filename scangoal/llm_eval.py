"""Avaliação incremental das hipóteses do modelo de linguagem (PR 4, protocolo `llm_incremental_v5`).

Implementa audits/pr4-llm-protocol-v5.md (o sha256 do ficheiro está fixado em PROTOCOL_SHA256; o v1 a v4 são recusados).
v5 (auditoria #5917252470): uma hipótese, e os seus controlos, só contribui a partir do dia UTC seguinte ao seu nascimento
(A-sel deixa de poder ser violada pelo próprio sistema); o relatório separa a rejeição de H0 condicional da afirmação por aposta;
os metadados de execução ficam fora do resultado científico (result_sha256).
Emendas do v2 (revisão independente da PR #27): apoio dos controlos pela hora de LEITURA (P1-A); grupos decididos no nascimento
(P1-B); início P = max(commit, primeira publicação verificável no GitHub) (P2-C). v3 (P1-D): nada depois do fecho da janela
existe para a avaliação. v4 (auditoria #5916502096, P1-E, e decisões do proprietário de 16/11/2026): o único critério
confirmatório é um teste por apostas (e-processo) com garantia de Ville para a hipótese nula definida (§6-A), válido com
qualquer dependência, mistura de cotações, assimetria e seleção dependente do passado, e a qualquer momento; horizonte de 365
dias; só dias fechados; jogos sem resultado imputados de forma desfavorável; prova contra revisões retroativas dos dados.
O bootstrap-t é só diagnóstico. Puro e determinístico: sem rede, sem segredos, sem escrita. O git só é lido.

Pergunta: as hipóteses `llm` escolhem jogos com mais lucro por jogo, depois do seu registo, do que hipóteses comparáveis do
gerador que já existiam, avaliadas no mesmo intervalo? Não mede vantagem contra o mercado nem certifica nada. A taxa de
acerto e `prob_above` são só descrição: nenhuma percentagem acima de 50 % entra num critério.
"""
import hashlib
import json
import math
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brain, chronology, insights
from .eligibility import eligible, game_identity
from .equivalence import Index, Redundancy, expectation, groups_at_birth

PROTOCOL = 'llm_incremental_v5'
PROTOCOL_FILE = 'audits/pr4-llm-protocol-v5.md'
PROTOCOL_SHA256 = '3ca34e70b955ae881658f3f0fa9823dda94aa90891f91bc749d4f1a04c6a2103'
REGISTRY_FILE = 'data/hypotheses.json'
TAU, SENSITIVITY, GROUP_MIN_GAMES = 0.85, (0.80, 0.90), 8
K, K_MIN = 20, 5
SEED, ALPHA, BOOT_REPLICATES = 20261115, 0.05, 999
LAMBDA_START, LAMBDA_CAP = 0.125, 0.5          # aposta do e-processo (§6): λ_1 = 1/8; λ_j <= 1/2
MIN_GROUPS, MIN_GROUP_GAMES, MIN_GAMES, MIN_DAYS, MAX_DAY_SHARE = 10, 10, 200, 14, 0.20
HORIZON_DAYS, SETTLE_DAYS = 365, brain.OPEN_TIMEOUT_DAYS    # H = P + 365 dias; C = H + 3 dias; dia fechado = fim + 3 dias (§0-B)
SECONDARY = ('S1_family_baseline', 'S2_mode_novas', 'S3_mode_refinar', 'S4_refinar_vs_parents', 'S5_evitar_vs_baseline',
             'S6_tau_0.80', 'S7_tau_0.90')
VERDICTS = ('não verificada',
            'utilidade incremental estatisticamente significativa (e-processo, α = 0,05; não é prova de vantagem nem certificação)',
            'em curso — sem conclusão (e-processo abaixo de 20)', 'evidência insuficiente', 'sem utilidade incremental demonstrada')
A_SEL = ('A garantia (§6-A) vale para H0 tal como definida. Com a regra do dia seguinte (v5), tudo o que o sistema escolhe para um '
         'dia existe antes de o dia começar; a leitura "por aposta" exige ainda A-sel quanto ao bot: a emissão de um sinal não depende '
         'dos resultados de outros jogos desse dia (plausível, não verificável pelos dados).')
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


def protocol_proof(root, commit, published=None, evidence=None):
    """-> (P, problemas). P = max(hora do commit que introduziu o protocolo, primeira publicação verificável no GitHub).

    Exige o conteúdo fixado em PROTOCOL_SHA256. `published` é a hora registada pelo servidor GitHub (execução de Actions ou pull
    request que contém o commit) e `evidence` a ligação para esse registo, confirmada pela auditoria independente (P2-C).
    Sem publicação comprovada, ou com uma hora anterior ao commit (impossível), a avaliação fica não verificada."""
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
    committed = chronology.parse_utc(intro[0][1]) if intro else None
    pub = chronology.parse_utc(published) if isinstance(published, str) else published
    if pub is None or not (isinstance(evidence, str) and evidence.startswith('https://github.com/')):
        problems.append('protocol publication not proven')
        return committed, problems
    if committed is not None and pub < committed:
        problems.append('protocol publication before its commit')
    return (max(committed, pub) if committed is not None else pub), problems


MESSAGES_FILE = 'data/messages.jsonl'
OBSERVATION_FILES = (('data/results_hl.json', 'results', 'matched_at_utc'), ('data/results_bsd.json', 'results', 'matched_at_utc'),
                     ('data/details_hl.json', 'details', 'fetched_at_utc'), ('data/manual_results.json', None, 'observed_utc'))
PUBLISH_TOLERANCE_MIN = 30   # = certification.PUBLISH_TOLERANCE_MIN: uma observação é publicada no commit do próprio job


def _coherent(stamp, published):
    """Carimbo de observação provado pela publicação: escrito antes do commit que o publicou e no máximo 30 min antes."""
    return stamp is not None and published is not None and stamp <= published <= stamp + timedelta(minutes=PUBLISH_TOLERANCE_MIN)


def observation_proof(root, ref, since):
    """Revisões retroativas dos dados (protocolo v4, §0-C) -> lista de problemas (vazia = provado).

    Pela história de primeiro-pai de `ref`: (1) data/messages.jsonl só cresce (nenhuma linha removida ou reescrita); (2) nos
    ficheiros de resultados, nenhuma entrada é removida ou alterada depois de publicada; (3) cada observação publicada a partir
    de `since` (mensagem recolhida, resultado associado, minutos dos golos, resultado manual) foi publicada no git entre o seu
    carimbo e 30 min depois. Um carimbo recuado, uma correção posterior ou um resultado acrescentado depois com data antiga
    tornam a avaliação não verificada (fail-closed)."""
    try:
        if _git(root, 'rev-parse', '--is-shallow-repository').decode().strip() != 'false':
            return ['observations: shallow repository']
        diff = _git(root, 'log', '--first-parent', '--reverse', '-p', '--unified=0', '--format=@@C %H %cI', ref, '--', MESSAGES_FILE)
    except (OSError, subprocess.CalledProcessError):
        return ['observations: history unavailable']
    problems, when, sha = [], None, None
    for line in diff.decode('utf-8', 'replace').split('\n'):
        if line.startswith('@@C '):
            _, sha, stamp = line.split()
            when = chronology.parse_utc(stamp)
        elif line.startswith('-{'):
            problems.append('messages rewritten at %s' % sha[:12])
        elif line.startswith('+{') and when is not None and since is not None and when >= since:
            try:
                row = json.loads(line[1:])
            except ValueError:
                problems.append('unreadable message line at %s' % sha[:12])
                continue
            if not _coherent(chronology.parse_utc(row.get('collected_at_utc')), when):
                problems.append('message %s published out of time at %s' % (row.get('message_id'), sha[:12]))
    for rel, key, field in OBSERVATION_FILES:
        try:
            log = _git(root, 'log', '--first-parent', '--reverse', '--format=%H %cI', ref, '--', rel).decode().split('\n')
        except subprocess.CalledProcessError:
            problems.append('observations: cannot read %s' % rel)
            continue
        commits = [tuple(x.split()) for x in log if x.strip()]
        prev, archived = {}, {}
        for sha, stamp, body in _versions(root, rel, commits) if commits else ():
            t = chronology.parse_utc(stamp)
            try:
                doc = json.loads(body) if body is not None else {}
                cur = (doc.get(key) or {}) if key else doc
                versions = doc.get('ft_versions', {}) if key == 'results' else {}
                if not isinstance(versions, dict):
                    raise ValueError('invalid FT versions')
            except (ValueError, AttributeError):
                problems.append('unreadable %s at %s' % (rel, sha[:12]))
                continue
            for k, v in prev.items():
                if k not in cur:
                    problems.append('%s entry removed at %s: %s' % (rel, sha[:12], k))
                elif cur[k] != v:
                    problems.append('%s entry rewritten at %s: %s' % (rel, sha[:12], k))
            for k, v in cur.items():
                if k not in prev and t is not None and since is not None and t >= since:
                    if not _coherent(chronology.parse_utc(v.get(field) if isinstance(v, dict) else None), t):
                        problems.append('%s entry published out of time at %s: %s' % (rel, sha[:12], k))
            for mid in archived.keys() | versions.keys():
                before, after = archived.get(mid, []), versions.get(mid, [])
                if not isinstance(before, list) or not isinstance(after, list) or after[:len(before)] != before:
                    problems.append('%s FT history rewritten at %s: %s' % (rel, sha[:12], mid))
                    continue
                for row in after[len(before):]:
                    observed = chronology.parse_utc(row.get('observed_at')) if isinstance(row, dict) else None
                    available = chronology.parse_utc(row.get('available_at')) if isinstance(row, dict) else None
                    if (observed is None or available is None or available < observed
                            or not _coherent(available, t)):
                        problems.append('%s FT version published out of time at %s: %s' % (rel, sha[:12], mid))
            prev, archived = cur, versions
    return problems[:50]


def confirmation_snapshot(root, ref, confirm):
    """O commit de dados da avaliação confirmatória (§0-C): o primeiro da história de primeiro-pai de `ref` com hora >= C.
    None se ainda não existe. Avaliações confirmatórias usam sempre este commit, nunca um posterior (revisões retroativas
    feitas depois não lhe chegam)."""
    try:
        log = _git(root, 'log', '--first-parent', '--reverse', '--format=%H %cI', ref).decode().split('\n')
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in log:
        if line.strip():
            sha, stamp = line.split()
            t = chronology.parse_utc(stamp)
            if t is not None and t >= confirm:
                return sha
    return None


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

def _operational_pre(b, t):
    """Aposta emitida e lida depois de t e decidível (brain.operational_bet sem a exigência do resultado: protocolo v4, §3)."""
    if t is None:
        return False
    sent, stamp = chronology.signal_time(b), chronology.parse_utc(b.get('signal_seen_utc'))
    return sent is not None and stamp is not None and sent > t and stamp > t and chronology.decidable(b)


def admitted(match, start):
    """Protocolo v4 (§3): {jogo: (dia, valor ou None, hora da decisão, ganho máximo)} dos jogos admitidos depois de `start`.

    A admissão NÃO depende do resultado: a aposta comprometida (a primeira decidida no jogo) tem de ser operacional depois de
    `start` e nenhum resultado do jogo pode ter sido observado antes da sua decisão. O valor é o lucro à odd −2 % se o resultado
    da aposta foi observado, com hora provada, até 3 dias depois da decisão; senão None (imputado nos dias fechados, §3)."""
    out = {}
    if start is None:
        return out
    games = {}
    for b in match:
        key = game_identity(b)
        if key is not None:
            games.setdefault(key, []).append(b)
    for key, rows in games.items():
        bet = brain.committed_bet(rows)
        if bet is None or not _operational_pre(bet, start) or bet.get('result_quarantined'):
            continue
        seen = [s for s in (chronology.parse_utc(b.get('match_result_first_seen_utc')) for b in rows) if s is not None]
        if seen and brain.latest_decision(bet) >= min(seen):
            continue   # um resultado do jogo já tinha sido observado na decisão: não é uma decisão válida
        dt, known = chronology.decision_time(bet), chronology.result_known(bet)
        value = brain.bet_profit(bet) if bet.get('w') is not None and known is not None and known <= dt + timedelta(days=SETTLE_DAYS) else None
        out[key] = (dt.date().isoformat(), value, dt, bet['odds'] * (1 - insights.ODDS_LOSS) - 1)
    return out


def _games(units, impute):
    """units: [(peso, {jogo: (dia, valor, t, ganho máximo)})] -> {jogo: (dia, valor médio ponderado, ganho máximo médio)}.
    Valor ausente imputado de forma desfavorável à hipótese em teste: 'loss' -> −1 (lado em teste); 'win' -> ganho máximo
    (lado de comparação). Só é usado em dias fechados, onde um valor ausente já não pode chegar (§0-B)."""
    games = {}
    for w, u in units:
        for g, (day, value, _, top) in u.items():
            if value is None:
                value = -1.0 if impute == 'loss' else top
            e = games.setdefault(g, [day, 0.0, 0.0, 0.0])
            e[0] = min(e[0], day)
            e[1] += w * value
            e[2] += w * top
            e[3] += w
    return {g: (day, s / w, t / w) for g, (day, s, t, w) in games.items()}


def _closed(day, clock, horizon):
    """Dia UTC fechado (§0-B): relógio >= min(fim do dia, H) + 3 dias."""
    end = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(days=1)
    if horizon is not None:
        end = min(end, horizon)
    return end + timedelta(days=SETTLE_DAYS) <= clock


def bet_size(history):
    """λ_j previsível (§6): só com X_1..X_{j−1}; em [0, 1/2]."""
    if not history:
        return LAMBDA_START
    m = sum(history) / len(history)
    v = sum((x - m) ** 2 for x in history) / len(history)
    return LAMBDA_START if v + m * m == 0 else min(LAMBDA_CAP, max(0.0, m / (v + m * m)))


def bootstrap_t(days, seed=SEED, replicates=None):
    """Diagnóstico (§6, sem efeito no veredicto): p unilateral do bootstrap-t por dias da média ponderada das diferenças."""
    n = len(days)
    if n < 3:
        return None

    def est(sample):
        tw = sum(w for _, w in sample)
        m = sum(d * w for d, w in sample) / tw
        se = math.sqrt(n / (n - 1) * sum((w * (d - m) / tw) ** 2 for d, w in sample))
        return m, se
    m, se = est(days)
    if se == 0:
        return None
    rng, t_obs, ge, valid = random.Random(seed), m / se, 0, 0
    for _ in range(replicates or BOOT_REPLICATES):
        mb, sb = est([days[rng.randrange(n)] for _ in range(n)])
        if sb > 0:
            valid += 1
            ge += (mb - m) / sb >= t_obs
    return round((1 + ge) / (1 + valid), 6) if valid else None


def eprocess(test_units, ctrl_units, clock, horizon, group_units=None):
    """Protocolo v4 (§6): e-processo sobre os dias fechados e emparelhados, pela ordem das datas.

    X_j = (m_L,j − m_C,j) / L_j >= −1; K_j = Π (1 + λ_j X_j), λ_j previsível em [0, 1/2]. Conclusão no primeiro dia com
    K_j >= 1/α e os mínimos de §7 cumpridos sobre os dias fechados até esse dia (só quando group_units é dado: análise primária).
    Sob H0 (§6-A), P(alguma vez K_j >= 1/α) <= α (Ville)."""
    tg, cg = _games(test_units, 'loss'), _games(ctrl_units, 'win')
    by_t, by_c = {}, {}
    for g, row in tg.items():
        by_t.setdefault(row[0], []).append(row)
    for g, row in cg.items():
        by_c.setdefault(row[0], []).append(row)
    closed = sorted(d for d in set(by_t) | set(by_c) if _closed(d, clock, horizon))
    rep_days = [[v[0] for v in u.values()] for u in (group_units or ())]   # dias dos jogos de cada representante

    def stats(upto):
        days = [d for d in closed if d <= upto]
        nt = sum(len(by_t.get(d, ())) for d in days)
        nc = sum(len(by_c.get(d, ())) for d in days)
        top = max((len(by_t.get(d, ())) for d in days), default=0)
        return {'games_test': nt, 'games_control': nc, 'days_test': sum(1 for d in days if d in by_t),
                'days_paired': sum(1 for d in days if d in by_t and d in by_c),
                'max_day_share_test': round(top / nt, 6) if nt else None}

    def fails_at(upto):
        groups = None if group_units is None else [sum(1 for d in ds if d <= upto and d in closed_set) for ds in rep_days]
        return enough(stats(upto), groups)

    closed_set = set(closed)
    logk, best, history, path, crossed = 0.0, 0.0, [], [], None
    diag = []
    for d in closed:
        if d not in by_t or d not in by_c:
            continue
        lt, lc = by_t[d], by_c[d]
        ml, mc = sum(r[1] for r in lt) / len(lt), sum(r[1] for r in lc) / len(lc)
        x = (ml - mc) / (1 + sum(r[2] for r in lc) / len(lc))
        lam = bet_size(history)
        logk += math.log1p(lam * x)
        history.append(x)
        best = max(best, logk)
        diag.append((ml - mc, len(lt) * len(lc) / (len(lt) + len(lc))))
        path.append({'day': d, 'x': round(x, 6), 'lambda': round(lam, 6), 'k': round(math.exp(logk), 6)})
        if crossed is None and logk >= math.log(1 / ALPHA) and (group_units is None or not fails_at(d)):
            crossed = d
    last = closed[-1] if closed else ''
    res = dict(stats(last), days_closed=len(closed), k_final=round(math.exp(logk), 6),
               k_sup=round(math.exp(best), 6), p_e=round(min(1.0, math.exp(-best)), 6), crossed_day=crossed, path=path,
               mean_daily_diff_descriptive=_r(sum(d * w for d, w in diag) / sum(w for _, w in diag)) if diag else None,
               diagnostic_bootstrap_t_p=bootstrap_t(diag))
    res['insufficient'] = fails_at(last)
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
    if primary['days_paired'] < MIN_DAYS:
        fails.append('paired days < %d' % MIN_DAYS)
    if primary['max_day_share_test'] is None or primary['max_day_share_test'] > MAX_DAY_SHARE:
        fails.append('one day holds more than %d %% of llm games' % round(MAX_DAY_SHARE * 100))
    return fails


def _r(x):
    return None if x is None else round(x, 6)


def next_day(t):
    """Início do dia UTC seguinte a t (protocolo v5, §3)."""
    return (t.astimezone(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def claim_scope(verdict):
    """Alcance da conclusão (§8 do protocolo v5): o que o teorema garante e o que depende ainda de A-sel."""
    if verdict == VERDICTS[1]:
        return {'conditional_h0': 'rejeitada (e-processo >= 20); P(rejeição falsa) <= 0,05 pelo teorema de §6-A',
                'per_bet_utility': ('sugerida, mas só se A-sel valer quanto ao bot (não escolher que jogos sinalizar pelos resultados de '
                                    'outros jogos do mesmo dia); não verificável pelos dados; não é prova de vantagem nem certificação')}
    return {'conditional_h0': 'não rejeitada', 'per_bet_utility': 'nenhuma afirmação'}


def result_digest(res):
    """sha256 do resultado científico, sem os metadados de execução (`run`): igual em todas as execuções com relógio >= C (§10)."""
    return hashlib.sha256(dumps({k: v for k, v in res.items() if k not in ('run', 'result_sha256')}).encode('utf-8')).hexdigest()


def windows(protocol_time):
    """(H, C) do protocolo v4 (§0-B): horizonte da janela e instante final; (None, None) sem P."""
    if protocol_time is None:
        return None, None
    horizon = protocol_time + timedelta(days=HORIZON_DAYS)
    return horizon, horizon + timedelta(days=SETTLE_DAYS)


def cutoff_of(as_of, protocol_time):
    """Fecho da janela de uma avaliação: min(relógio efetivo, H)."""
    horizon, confirm = windows(protocol_time)
    clock = confirm if confirm is not None and as_of >= confirm else as_of
    return min(clock, horizon) if horizon is not None else clock


def evaluate(signals, registry, *, as_of, born_eff, protocol_time, problems=()):
    """Avaliação completa (pura). signals: sinais do relatório (a vista do relógio e a elegibilidade aplicam-se aqui).
    born_eff: {id: datetime ou None} (births). protocol_time: P. problems: falhas de prova (histórico, protocolo).

    Protocolo v4 (§0-B): o relógio efetivo é min(as_of, C). Só dias fechados entram no e-processo, pela ordem das datas; a
    conclusão (riqueza >= 20 com os mínimos cumpridos) pode acontecer em qualquer dia fechado e nunca é reescrita por avaliações
    posteriores. Só contam jogos decididos até H e só hipóteses nascidas até H."""
    horizon, confirm = windows(protocol_time)
    final = confirm is not None and as_of >= confirm
    clock = confirm if final else as_of
    cutoff = cutoff_of(as_of, protocol_time)   # fecho da janela: min(relógio, H)
    as_of = clock
    # Só sinais LIDOS até ao fecho da janela (min(relógio, H)), com os resultados observados até ao relógio: nada lido depois de H
    # muda a avaliação confirmatória, nem sequer as características dos sinais da janela.
    view = [b for b in chronology.as_of_view(signals, as_of) if eligible(b) and chronology.decision_time(b) <= cutoff]
    have = [(b, insights.family(b), frozenset((d, v) for d, v in brain.atoms(b, set())
                                               if d in brain.RESEARCH_DIMENSIONS)) for b in view]
    index = Index(view)

    def match(fam, conds):
        conds = {tuple(c) for c in conds}
        return [b for b, f, at in have if f == fam and conds <= at]

    snaps = {}

    def snap(t):
        """Índice dos sinais elegíveis já LIDOS em t (hora de leitura provada): o que o sistema sabia nesse instante."""
        if t not in snaps:
            snaps[t] = Index(chronology.as_of_view(view, t))
        return snaps[t]

    cache = {}

    def games_of(hid, start):
        key = (hid, start)
        if key not in cache:
            h = registry[hid] if hid in registry else {'family': hid[len('family:'):], 'conditions': []}
            # A janela fecha em H porque a vista só tem sinais lidos até min(relógio, H) e a decisão de uma aposta é a sua leitura
            # (guarda única, testada: test_P1D_window_ends_at_the_horizon).
            cache[key] = admitted(match(h['family'], h['conditions']), start)
        return cache[key]

    # Hipóteses registadas depois do fecho da janela não existem para esta avaliação (nem nas contagens): o resultado não
    # depende do registo posterior. O script de auditoria indica quantas são, nas entradas.
    llm_ids = sorted(hid for hid, h in registry.items() if h.get('source') == 'llm'
                     and (born_eff.get(hid) is None or born_eff[hid] <= cutoff))
    valid = [hid for hid in llm_ids if born_eff.get(hid) is not None and brain.research_conditions(registry[hid])]
    excluded = {'no_proven_birth': sum(1 for hid in llm_ids if born_eff.get(hid) is None),
                'unavailable_at_decision': sum(1 for hid in llm_ids if born_eff.get(hid) is not None
                                               and not brain.research_conditions(registry[hid]))}

    # Duplicados à nascença (descrição): redundância extensional demonstrável no instante de registo de cada proposta.
    duplicates = []
    for hid in valid:
        h, t = registry[hid], born_eff[hid]
        before = {k: v for k, v in registry.items() if k != hid and born_eff.get(k) is not None and born_eff[k] < t}
        why = Redundancy(chronology.as_of_view(view, t), before).reason(h['family'], h['conditions'])   # o que o sistema já tinha lido
        if why:
            duplicates.append({'id': hid, 'reason': why[0], 'with': why[1]})

    order = sorted(valid, key=lambda hid: (born_eff[hid], hid))

    def games_at(hid, t):
        return snap(t).games(registry[hid]['family'], registry[hid]['conditions'])

    def grouping(tau):   # decidido no nascimento de cada hipótese, com o que já tinha sido lido (P1-B)
        return groups_at_birth([(hid, registry[hid], born_eff[hid]) for hid in order], tau, games_at, GROUP_MIN_GAMES)

    gens = sorted(hid for hid, h in registry.items() if h.get('source') == 'gerador' and born_eff.get(hid) is not None
                  and brain.research_conditions(h))

    def controls(hid):
        """Sem ler resultados; apoio e exclusão só com os sinais já LIDOS no nascimento de h (P1-A)."""
        h, t = registry[hid], born_eff[hid]
        ix = snap(t)
        size = min(len(h['conditions']), brain.MAX_CONDITIONS)
        own = ix.mask(h['family'], h['conditions'])
        s = own.bit_count()
        cands = []
        for c in gens:
            g = registry[c]
            if g['family'] != h['family'] or len(g['conditions']) != size or born_eff[c] >= t:
                continue
            m = ix.mask(g['family'], g['conditions'])
            if m == own:
                continue
            if s and s / 2 <= m.bit_count() <= 2 * s:
                cands.append(c)
        cands.sort(key=lambda c: hashlib.sha256(('%s\n%s' % (hid, c)).encode('utf-8')).hexdigest())
        return cands[:K]

    def parents(hid):
        h, t = registry[hid], born_eff[hid]
        conds = [tuple(c) for c in h['conditions']]
        out = []
        for i in range(len(conds)):
            pid = brain.hyp_id(h['family'], conds[:i] + conds[i + 1:])
            if (pid in registry and born_eff.get(pid) is not None and born_eff[pid] < t
                    and pid != hid and brain.research_conditions(registry[pid])):
                out.append(pid)
        return sorted(set(out))

    # v5 (§3): só a partir do dia UTC seguinte ao nascimento; os controlos, mães e base usam o mesmo início (side()).
    starts = {hid: max(next_day(born_eff[hid]), protocol_time) if protocol_time is not None else None for hid in valid}
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
    test_units, ctrl_units = side(primary_reps, 'controls')
    primary = eprocess(test_units, ctrl_units, clock, horizon, group_units=[u for _, u in test_units])
    fails = primary['insufficient']

    def secondary(name):
        if name == 'S1_family_baseline':
            return eprocess(*side(reps_of(grp, need_controls=False), 'baseline'), clock, horizon)
        if name == 'S2_mode_novas':
            return eprocess(*side(reps_of(grp, modes={'novas'}), 'controls'), clock, horizon)
        if name == 'S3_mode_refinar':
            return eprocess(*side(reps_of(grp, modes={'refinar'}), 'controls'), clock, horizon)
        if name == 'S4_refinar_vs_parents':
            return eprocess(*side([h for h in reps_of(grp, modes={'refinar'}, need_controls=False) if parents(h)], 'parents'), clock, horizon)
        if name == 'S5_evitar_vs_baseline':
            test, base = side(reps_of(grp, expect='abaixo', need_controls=False), 'baseline')
            return eprocess(base, test, clock, horizon)   # H1: base da família − evitar > 0 (a imputação troca de lados)
        tu, cu = side(reps_of(grouping(float(name.split('_')[-1]))), 'controls')
        return eprocess(tu, cu, clock, horizon, group_units=[u for _, u in tu])

    sec = {name: secondary(name) for name in SECONDARY}
    for res in sec.values():
        res.pop('path')                                    # a trajetória completa só é mostrada na análise primária
    adj = holm([sec[n]['p_e'] if sec[n]['days_paired'] else None for n in SECONDARY])
    for name, a in zip(SECONDARY, adj):                    # exploratórias (§9): p_e válido a qualquer momento, Holm m = 7
        sec[name]['p_holm'] = a
        sec[name]['significant_holm_exploratory'] = a <= ALPHA

    if problems:
        verdict = VERDICTS[0]
    elif primary['crossed_day'] is not None:
        verdict = VERDICTS[1]
    elif not final:
        verdict = VERDICTS[2]
    elif fails:
        verdict = VERDICTS[3]
    else:
        verdict = VERDICTS[4]

    # Janela pós-registo e pré-protocolo: só descrição (sem teste); só valores observados, nunca sustenta conclusões.
    def observed_before_p(u):
        return [v[1] for v in u.values() if v[1] is not None and (protocol_time is None or v[2] < protocol_time)]
    pre_test, pre_ctrl = [], []
    for hid in primary_reps:
        pre_test += observed_before_p(games_of(hid, born_eff[hid]))
        for c in ctrl_of[hid]:
            pre_ctrl += observed_before_p(games_of(c, born_eff[hid]))
    pre_protocol = {'bets_test': len(pre_test), 'bets_control': len(pre_ctrl),
                    'mean_profit_test': _r(sum(pre_test) / len(pre_test)) if pre_test else None,
                    'mean_profit_control': _r(sum(pre_ctrl) / len(pre_ctrl)) if pre_ctrl else None,
                    'meaning': 'descriptive only: post-registration bets with observed results decided before the protocol; '
                               'control bets counted once per (hypothesis, control) pair; no test, no conclusion'}

    group_of = {hid: i for i, g in enumerate(grp) for hid in g}
    table = []
    for hid in order:
        h = registry[hid]
        prot = games_of(hid, starts[hid])
        seen = [v[1] for v in prot.values() if v[1] is not None]
        pre = observed_before_p(games_of(hid, born_eff[hid]))
        table.append({'id': hid, 'group': group_of[hid], 'representative': grp[group_of[hid]][0] == hid, 'mode': h.get('mode'),
                      'expect': expectation(h), 'born_utc': h.get('born_utc'), 'born_eff': chronology.iso(born_eff[hid]),
                      'membership_games_at_birth': len(games_at(hid, born_eff[hid])),
                      'membership_games_now': len(index.games(h['family'], h['conditions'])), 'controls': len(ctrl_of.get(hid) or ()), 'parents': len(parents(hid)),
                      'controls_sha256': hashlib.sha256('\n'.join(ctrl_of.get(hid) or ()).encode('utf-8')).hexdigest()[:16],
                      'protocol_games': len(prot), 'protocol_results_observed': len(seen),
                      'protocol_mean_profit_observed': _r(sum(seen) / len(seen)) if seen else None,
                      'pre_protocol_games': len(pre), 'pre_protocol_mean_profit': _r(sum(pre) / len(pre)) if pre else None})

    return {'protocol': PROTOCOL, 'protocol_sha256': PROTOCOL_SHA256, 'protocol_time_utc': chronology.iso(protocol_time),
            'as_of_utc': chronology.iso(as_of), 'verified': not problems, 'proof_problems': list(problems),
            'phase': 'final' if final else 'running', 'horizon_utc': chronology.iso(horizon),
            'final_utc': chronology.iso(confirm), 'verdict': verdict, 'concluded_on': primary['crossed_day'],
            'insufficient': fails, 'assumption_a_sel': A_SEL, 'claim': claim_scope(verdict),
            'parameters': {'tau': TAU, 'sensitivity': list(SENSITIVITY), 'group_min_games': GROUP_MIN_GAMES, 'k': K, 'k_min': K_MIN,
                           'test': 'betting e-process (Ville), predictable plug-in lambda', 'threshold': round(1 / ALPHA, 6),
                           'lambda_start': LAMBDA_START, 'lambda_cap': LAMBDA_CAP, 'alpha': ALPHA, 'seed': SEED,
                           'diagnostic': 'bootstrap-t over days, %d replicates (no effect on the verdict)' % BOOT_REPLICATES,
                           'min_groups': MIN_GROUPS, 'min_group_games': MIN_GROUP_GAMES, 'min_games': MIN_GAMES, 'min_days': MIN_DAYS,
                           'max_day_share': MAX_DAY_SHARE, 'horizon_days': HORIZON_DAYS, 'settle_days': SETTLE_DAYS,
                           'odds_loss': insights.ODDS_LOSS, 'holm_m': len(SECONDARY),
                           'imputation': 'missing result after 3 days: -1 on the tested side, maximum win on the comparison side'},
            'population': {'eligible_signals': len(view), 'llm_hypotheses': len(llm_ids), 'evaluated': len(valid), 'excluded': excluded,
                           'groups': len(grp), 'groups_sensitivity': {str(t): len(grouping(t)) for t in SENSITIVITY},
                           'primary_representatives': len(primary_reps),
                           'without_controls': sum(1 for hid in ctrl_of if len(ctrl_of[hid]) < K_MIN),
                           'duplicates_at_birth': duplicates,
                           'by_mode': {m: sum(1 for hid in valid if (registry[hid].get('mode') or 'novas') == m) for m in ('novas', 'refinar', 'evitar')}},
            'primary': primary, 'secondary': sec, 'pre_protocol': pre_protocol, 'hypotheses': table,
            'notes': ['hypotheses and controls with dimensions unavailable at the decision are excluded',
                      'hit rates and prob_above are not used; no criterion reads them',
                      'pre-protocol bets are post-registration evidence gathered before the protocol: descriptive only',
                      'the bootstrap-t p is a diagnostic only and never changes the verdict']}


def page_summary(signals, registry):
    """Resumo para a página (sem git: nunca há conclusão científica no relatório de produção). Grupos como nos protocolos v2/v3."""
    llm = sorted(hid for hid, h in registry.items() if h.get('source') == 'llm' and chronology.born(h) is not None)
    view, snaps = [b for b in signals if eligible(b)], {}

    def games_at(hid, t):
        if t not in snaps:
            snaps[t] = Index(chronology.as_of_view(view, t))
        return snaps[t].games(registry[hid]['family'], registry[hid]['conditions'])
    order = sorted(llm, key=lambda hid: (chronology.born(registry[hid]), hid))
    grp = groups_at_birth([(hid, registry[hid], chronology.born(registry[hid])) for hid in order], TAU, games_at, GROUP_MIN_GAMES)
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
    if res['concluded_on']:
        lines += ['Conclusão no dia fechado %s (riqueza >= 20 e mínimos cumpridos); mantém-se em todas as avaliações posteriores.' % res['concluded_on'], '']
    lines += ['Fase: **%s**. Horizonte H = %s; fim do v4 C = %s. O e-processo é válido a qualquer momento (Ville): olhar repetidamente'
              % ('final' if res['phase'] == 'final' else 'em curso', res['horizon_utc'], res['final_utc']),
              'não aumenta o erro de tipo I acima de α = 0,05 para H0 (§6-A do protocolo). Só dias fechados entram.', '',
              '**Suposição A-sel:** ' + res['assumption_a_sel'], '',
              '**Alcance da conclusão (§8 do protocolo v5):**', '',
              '- H0 condicional (§6-A), garantida pelo teorema: %s' % res['claim']['conditional_h0'],
              '- Utilidade incremental por aposta: %s' % res['claim']['per_bet_utility'], '']
    if res['proof_problems']:
        lines += ['Falhas de prova (nenhuma conclusão científica):', ''] + ['- %s' % x for x in res['proof_problems']] + ['']
    if res['insufficient']:
        lines += ['Mínimos do protocolo ainda não cumpridos (sobre os dias fechados):', ''] + ['- %s' % x for x in res['insufficient']] + ['']
    lines += ['## Pergunta', '',
              'As hipóteses do modelo de linguagem escolhem jogos com mais lucro por jogo (odd anunciada −2 %), depois do seu registo,',
              'do que hipóteses comparáveis do gerador que já existiam, no mesmo intervalo? Não é uma medida de vantagem contra o',
              'mercado e não certifica nenhuma tática. Taxas de acerto, incluindo as superiores a 50 %, não são usadas.', '',
              '## Entradas', '',
              '- Protocolo: `%s`, sha256 `%s`, início P = %s' % (res['protocol'], res['protocol_sha256'], res['protocol_time_utc']),
              '- Relógio efetivo da avaliação: %s (min(as_of pedido, C))' % res['as_of_utc'],
              '- P = max(hora do commit do protocolo, primeira publicação verificável no GitHub); ver `protocol_published_utc` e `protocol_publication_evidence` nas entradas.']
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
              '', '## Comparação primária: e-processo', '',
              'Por dia fechado com jogos dos dois lados: X = (lucro médio por jogo LLM − lucro médio por jogo dos controlos) / (1 + ganho',
              'máximo médio dos controlos), sempre >= −1. Riqueza K = Π (1 + λ X), λ previsível em [0, 1/2]. Conclusão quando K >= 20.',
              'Jogos sem resultado em 3 dias: −1 no lado LLM, ganho máximo no lado dos controlos (desfavorável à hipótese em teste).', '',
              '| Jogos LLM | Jogos controlo | Dias LLM | Dias emparelhados | Maior fração num dia | K final | K máximo | p_e = 1/K máx. | Diferença diária média (descritiva) | Bootstrap-t (diagnóstico) |',
              '|---|---|---|---|---|---|---|---|---|---|',
              '| %d | %d | %d | %d | %s | %s | %s | %s | %s | %s |' % (prim['games_test'], prim['games_control'], prim['days_test'], prim['days_paired'],
                                                            fmt(prim['max_day_share_test']), fmt(prim['k_final']), fmt(prim['k_sup']), fmt(prim['p_e']),
                                                            fmt(prim['mean_daily_diff_descriptive']), fmt(prim['diagnostic_bootstrap_t_p'])),
              '', 'O p do bootstrap-t é só diagnóstico: nunca muda o veredicto.', '',
              '## Janela pós-registo e pré-protocolo (só descrição)', '',
              'Mesmos representantes e controlos, apostas com resultado observado, decididas antes de P. Não é um teste.', '',
              '| Apostas LLM | Lucro médio LLM | Apostas de controlo | Lucro médio controlo |', '|---|---|---|---|',
              '| %d | %s | %d | %s |' % (res['pre_protocol']['bets_test'], fmt(res['pre_protocol']['mean_profit_test']),
                                       res['pre_protocol']['bets_control'], fmt(res['pre_protocol']['mean_profit_control'])),
              '', '## Análises adicionais (exploratórias; p_e = 1/K máx. com Holm, m = %d)' % p['holm_m'], '',
              '| Análise | Jogos em teste | Jogos de comparação | Dias emparelhados | K máximo | p_e | p Holm |', '|---|---|---|---|---|---|---|']
    for name in SECONDARY:
        s = res['secondary'][name]
        lines.append('| %s | %d | %d | %d | %s | %s | %s |' % (name, s['games_test'], s['games_control'], s['days_paired'], fmt(s['k_sup']),
                                                            fmt(s['p_e']), fmt(s['p_holm'])))
    lines += ['', 'Nenhuma análise adicional sustenta sozinha um veredicto.', '',
              '## Hipóteses (descrição)', '',
              '| Hipótese | Grupo | Modo | Jogos no protocolo | Com resultado | Lucro/jogo observado | Apostas pré-protocolo | Lucro/aposta | Controlos |',
              '|---|---|---|---|---|---|---|---|---|']
    for h in res['hypotheses']:
        lines.append('| %s%s | %d | %s | %d | %d | %s | %d | %s | %d |' % (h['id'].replace('|', '\\|'), ' (repr.)' if h['representative'] else '',
                                                                   h['group'], h['mode'] or '—', h['protocol_games'], h['protocol_results_observed'],
                                                                   fmt(h['protocol_mean_profit_observed']), h['pre_protocol_games'],
                                                                   fmt(h['pre_protocol_mean_profit']), h['controls']))
    if pop['duplicates_at_birth']:
        lines += ['', '## Duplicados extensionais à nascença', '']
        lines += ['- %s — %s — %s' % (d['id'], d['reason'], d['with']) for d in pop['duplicates_at_birth']]
    lines += ['', '## Ameaças à validade e limitações', '',
              '- A garantia do e-processo é para H0 (§6-A): esperança condicional da diferença diária normalizada <= 0. A leitura',
              '  "cada aposta da LLM não é melhor" exige a suposição A-sel (composição de cada dia sem dependência dos resultados desse dia).',
              '- Poder baixo: em simulação, com efeito de +20 % na probabilidade de ganhar, ≈ 25 % a 90 dias e ≈ 84 % a 365 dias.',
              '- A hora de publicação das observações é a hora do commit (committer date) na história de primeiro-pai.',
              '- A hora de publicação do protocolo vem de um registo do servidor GitHub indicado ao avaliador; a auditoria independente confirma-a.',
              '- Os controlos são hipóteses do gerador com tamanho e apoio comparáveis; não esgotam o que um analista humano escolheria.',
              '- A janela pré-protocolo é pós-registo, mas foi observada antes de o protocolo existir: só descrição.',
              '- O resultado não altera estados, certificação, avisos Telegram ou o registo de hipóteses.', '']
    return '\n'.join(lines)


def dumps(res):
    return json.dumps(res, indent=1, ensure_ascii=False, sort_keys=True) + '\n'

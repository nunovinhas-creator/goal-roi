"""Verificação por mutação da PR 4: desliga uma guarda de cada vez e exige que pelo menos um teste falhe.

Uso (raiz do repositório): python3.11 audits/pr4-evidence/mutation_check.py
Cada mutante é escrito numa cópia temporária do repositório (sem .git nem data/); os ficheiros reais não são tocados.
Os testes sem mutação têm de passar primeiro (linha de base); senão nenhum resultado de mutante significa nada.
Código de saída: 0 = todos os mutantes apanhados; 1 = algum sobreviveu ou a linha de base falhou.
"""
import os
import shutil
import subprocess
import sys
import tempfile

E, Q, B, L = 'scangoal/llm_eval.py', 'scangoal/equivalence.py', 'scangoal/brain.py', 'scangoal/llm.py'
MUTATIONS = {
    'birth: max(born_utc, publication)': (E, "            out[hid] = max(born, pub) if pub is not None else None\n", "            out[hid] = born\n"),
    'birth: unpublished has no birth': (E, "            out[hid] = max(born, pub) if pub is not None else None\n", "            out[hid] = max(born, pub) if pub is not None else born\n"),
    'history: shallow refused': (E, "        if _git(root, 'rev-parse', '--is-shallow-repository').decode().strip() != 'false':\n", "        if False:\n"),
    'history: rewritten entry': (E, "                elif cur[hid] != entry:\n", "                elif False:\n"),
    'history: removed entry': (E, "                if hid not in cur:\n                    problems.append('entry removed", "                if False:\n                    problems.append('entry removed"),
    'history: deleted registry': (E, "            if body is None:\n                problems.append('registry deleted", "            if body is None and False:\n                problems.append('registry deleted"),
    'history: monotonic commit time': (E, "            if t is None or (last_time is not None and t < last_time):\n", "            if False:\n"),
    'protocol: content hash': (E, "    if hashlib.sha256(body).hexdigest() != PROTOCOL_SHA256:\n", "    if False:\n"),
    'protocol: introducing commit': (E, "    if not intro or intro[0][0] != sha:\n", "    if False:\n"),
    'evidence: only prospective games': (E, "        if brain.game_category(rows, start) != 'prospective':\n", "        if brain.game_category(rows, start) in ('result_time_unproven',):\n"),
    'evidence: as_of view': (E, "    view = [b for b in chronology.as_of_view(signals, as_of) if eligible(b) and chronology.decision_time(b) <= cutoff]\n",
                             "    view = [b for b in signals if eligible(b) and (chronology.decision_time(b) or cutoff) <= cutoff]\n"),
    'P1-D: view limited to signals read by the window close': (E, " if eligible(b) and chronology.decision_time(b) <= cutoff]\n", " if eligible(b)]\n"),
    'P1-D: exploratory before C': (E, "    elif not confirmatory:\n        verdict = VERDICTS[1]\n", "    elif False:\n        verdict = VERDICTS[1]\n"),
    'P1-D: clock fixed at C': (E, "    clock = confirm if confirmatory else as_of\n", "    clock = as_of\n"),
    'P1-D: window ends at H': (E, "                          if horizon is None or v[2] <= horizon}", "                          if True}"),
    'P1-D: descriptive table inside the window': (E, "                if horizon is not None and (bet is None or chronology.decision_time(bet) > horizon):\n", "                if False:\n"),
    'P1-D: born after the window not evaluated': (E, "                     and (born_eff.get(hid) is None or born_eff[hid] <= cutoff))\n", "                     )\n"),
    'P1-D: secondary untested before C': (E, "        sec[name]['significant_holm'] = (a <= ALPHA) if confirmatory else None\n", "        sec[name]['significant_holm'] = a <= ALPHA\n"),
    'P1-D: horizon of 30 days': (E, "HORIZON_DAYS, SETTLE_DAYS = 30, brain.OPEN_TIMEOUT_DAYS", "HORIZON_DAYS, SETTLE_DAYS = 45, brain.OPEN_TIMEOUT_DAYS"),
    'test: within-day pairing': (E, "    diff = [tx[d][0] / tx[d][1] - cx[d][0] / cx[d][1] for d in paired]\n",
                                 "    diff = [tx[d][0] / tx[d][1] - sum(v[0] for v in cx.values()) / nc for d in paired]\n"),
    'minimum: paired days': (E, "    if primary['days_paired'] < MIN_DAYS:\n", "    if False:\n"),
    'evidence: start = max(birth, protocol)': (E, "    starts = {hid: max(born_eff[hid], protocol_time) if protocol_time is not None else None for hid in valid}\n",
                                               "    starts = {hid: born_eff[hid] for hid in valid}\n"),
    'verdict: proof problems first': (E, "    if problems:\n        verdict = VERDICTS[0]\n", "    if False:\n        verdict = VERDICTS[0]\n"),
    'minimum: groups': (E, "    if group_games is not None and sum(1 for n in group_games if n >= MIN_GROUP_GAMES) < MIN_GROUPS:\n", "    if False:\n"),
    'minimum: days': (E, "    if primary['days_test'] < MIN_DAYS:\n", "    if False:\n"),
    'minimum: day share': (E, "    if primary['max_day_share_test'] is None or primary['max_day_share_test'] > MAX_DAY_SHARE:\n", "    if False:\n"),
    'minimum: llm games': (E, "    if primary['games_test'] < MIN_GAMES:\n", "    if False:\n"),
    'test: one-sided p': (E, "        if sum(x if (bits >> i) & 1 else -x for i, x in enumerate(terms)) >= d - 1e-12:\n",
                          "        if sum(x if (bits >> i) & 1 else -x for i, x in enumerate(terms)) <= d + 1e-12:\n"),
    'holm: multiply by remaining tests': (E, "        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))\n", "        running = max(running, min(1.0, ps[i]))\n"),
    'primary: groups count once': (E, "    grp = grouping(TAU)\n", "    grp = [[h] for h in order]\n"),
    'primary: evitar excluded': (E, "            if expectation(h) != expect or (modes is not None", "            if False or (modes is not None"),
    'controls: born before the proposal': (E, " or born_eff[c] >= t:\n                continue\n            m = ix.mask", ":\n                continue\n            m = ix.mask"),
    'controls: comparable support': (E, "            if s and s / 2 <= m.bit_count() <= 2 * s:\n", "            if s:\n"),
    'P1-A: controls see only signals read at birth': (E, "        ix = snap(t)\n", "        ix = index\n"),
    'P1-B: groups decided with data read at birth': (E, "        return snap(t).games(registry[hid]['family'], registry[hid]['conditions'])\n",
                                                     "        return index.games(registry[hid]['family'], registry[hid]['conditions'])\n"),
    'P1-B: snapshot is the read-time view': (E, "            snaps[t] = Index(chronology.as_of_view(view, t))\n        return snaps[t]\n",
                                             "            snaps[t] = Index([b for b in view if (chronology.signal_time(b) or t) <= t])\n        return snaps[t]\n"),
    'P2-C: publication required': (E, "    if pub is None or not (isinstance(evidence, str) and evidence.startswith('https://github.com/')):\n", "    if pub is None:\n"),
    'P2-C: publication not before commit': (E, "    if committed is not None and pub < committed:\n", "    if False:\n"),
    'P2-C: start is the publication': (E, "    return (max(committed, pub) if committed is not None else pub), problems\n", "    return committed, problems\n"),
    'groups: complete linkage': (Q, "                if all(compatible(h, mh) and len(games_at(mid, t)) >= min_games and jaccard(gs, games_at(mid, t)) >= tau for mid, mh, _ in g):\n",
                                 "                if any(compatible(h, mh) and len(games_at(mid, t)) >= min_games and jaccard(gs, games_at(mid, t)) >= tau for mid, mh, _ in g):\n"),
    'groups: same expectation': (Q, "    if a['family'] != b['family'] or expectation(a) != expectation(b):\n", "    if a['family'] != b['family']:\n"),
    'groups: no contradictory condition': (Q, "    return all(db[d] == v for d, v in da.items() if d in db)\n", "    return True\n"),
    'redundancy: sub-combinations': (Q, "                if self.index.mask(family, sub) == m:\n", "                if False:\n"),
    'redundancy: same signals as existing': (Q, "        if other is not None and other != brain.hyp_id(family, conds):\n", "        if False:\n"),
    'redundancy: same reply learns accepted': (Q, "        self.by_family.setdefault(family, {}).setdefault(self.index.mask(family, conds), hid)\n", "        pass\n"),
    'llm: rejects redundant proposals': (L, "        if ok and redundancy is not None and redundancy.reason(fam, conds):\n", "        if False:\n"),
    'track: prospective games only': (B, "        if (p.get('games') or 0) < TRACK_MIN:\n", "        if (r.get('games') or 0) < TRACK_MIN:\n"),
}
TESTS = ['test_llm_evaluation.py', 'test_scangoal.py']


def run(mutation, work):
    copy = os.path.join(work, 'repo')
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree('.', copy, ignore=shutil.ignore_patterns('.git', 'data', '__pycache__'))
    if mutation:
        path, old, new = mutation
        with open(path, encoding='utf-8') as f:
            src = f.read()
        if src.count(old) != 1:
            return 'pattern not found once'
        with open(os.path.join(copy, path), 'w', encoding='utf-8') as f:
            f.write(src.replace(old, new))
    codes = [subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', t], cwd=copy, capture_output=True, text=True).returncode
             for t in TESTS]
    return 'fail' if any(codes) else 'pass'


def main():
    out, ok = [], True
    with tempfile.TemporaryDirectory() as work:
        base = run(None, work)
        out.append('baseline: %s' % base)
        ok = base == 'pass'
        for name, mutation in MUTATIONS.items():
            res = run(mutation, work)
            caught = res == 'fail'
            ok = ok and caught
            out.append('%-45s %s' % (name, 'CAUGHT' if caught else 'SURVIVED (%s)' % res))
    out.append('ALL CAUGHT' if ok else 'NOT ALL CAUGHT')
    print('\n'.join(out))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

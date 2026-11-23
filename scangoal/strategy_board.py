"""Quadro de estratégias (issue #56): poucas candidatas, quatro estados, critérios fixados ANTES de ler os resultados.

A. em observação          — registada, ainda sem resultados prospetivos suficientes para qualquer leitura.
B. evidência insuficiente — tem resultados prospetivos, mas falta pelo menos um requisito científico ou económico.
C. rejeitada              — falhou um critério científico, económico ou operacional (motivo sempre explícito).
D. validada               — todos os requisitos demonstrados. Exige certificação ativa, preços executáveis e custos
                            conhecidos; nenhum destes existe hoje, por isso D fica vazio por construção.

Só lê agregados já calculados pelo cérebro (uma unidade comprometida por encontro, jogos posteriores ao registo de cada
hipótese, decisão anterior ao resultado). Não escreve dados, não envia avisos, não certifica e não altera o registo.
Os limites estatísticos são aproximações normais descritivas; nunca substituem o e-processo da certificação.
"""
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

SCHEMA = 'goal_roi_strategy_board_v1'

# Critérios congelados. Registados a 2026-11-23T14:01:25Z, com os valores já existentes no código sempre que havia um
# (TRACK_MIN=10, HINT_AT=30, MIN_SETTLED=300, FWER_ALPHA=0.05, MIN_RANK_DAYS=10, MAX_DAY_SHARE=0.20, odd ×0,98).
# Qualquer alteração muda CRITERIA_SHA256 e faz falhar tests/test_strategy_board.py: um critério novo exige versão nova,
# data nova e só vale para jogos posteriores a essa data.
CRITERIA = {
    'version': 'strategy_board_criteria_v1',
    'registered_utc': '2026-11-23T14:01:25Z',
    'unit': 'one committed unit per independent fixture, decided after registration and before any result',
    'return': 'profit at announced odds x 0.98',
    'observe_min_results': 10,
    'decision_min_results': 30,
    'confirmation_min_results': 300,
    'min_decision_days': 10,
    'max_day_share': 0.20,
    'max_unresolved_share': 0.20,
    'family_alpha': 0.05,
    'reject_upper_z': 1.6449,
    'bounds_require_min_decision_days': True,
    'multiplicity': 'bonferroni over every candidate with >= observe_min_results confirmed results in this run',
    'phases': 'pre and live never pooled; a candidate whose committed bets mix them is rejected',
    'requires_for_validated': ['brain certification active and certified', 'executable price evidence',
                               'known costs and commissions', 'bonferroni lower bound > 0',
                               '>= confirmation_min_results', 'single phase'],
    'listing_limit': 10,
    'listing_per_family': 3,
    'listing_excludes_non_positive_return': True,
}
CRITERIA_SHA256 = 'e56167cc53eb86e8826cab1d28473244560a533168dbb567553fc9dc494fedd4'

# Lista curta congelada (descoberta -> registo -> jogos futuros). Só apostas decididas depois de registered_utc contam.
SHORTLIST_PATH = Path(__file__).resolve().parents[1] / 'audits/strategy-lab/shortlist-v1.json'
SHORTLIST_SHA256 = '2a9b648f3235a8fd95b6b3dcf8179a6963ea15c4a9b33f4de4cc82a190e3e0ee'
RULE_PR55_ID = 'polymarket_pre_over25_share_lt80_total_lt10000_v1'

# Não há no repositório nenhuma prova de execução (fills, profundidade do livro, apostas aceites) nem de comissões.
# Não inventar: enquanto não existir uma fonte auditável, estes dois requisitos ficam por cumprir.
EXECUTABLE_PRICE_EVIDENCE = False
COSTS_KNOWN = False

A, B, C, D = 'em_observacao', 'evidencia_insuficiente', 'rejeitada', 'validada'
STATES = (A, B, C, D)
LABELS = {A: 'Em observação', B: 'Evidência insuficiente', C: 'Rejeitada', D: 'Validada'}

REASONS = {
    'avoid_hypothesis': 'hipótese de "evitar": não é uma estratégia de aposta',
    'unverified_registration': 'sem instante de registo verificável',
    'post_signal_condition': 'usa informação posterior ao sinal (reforço, odd/quota depois do sinal, outros sinais do jogo)',
    'not_known_at_decision': 'usa uma condição que não se prova conhecida no instante da decisão',
    'no_prospective_evidence_possible': 'as condições não permitem avaliação prospetiva',
    'mixed_pre_live': 'mistura sinais pré-jogo e LIVE na mesma amostra',
    'abandoned_by_brain': 'abandonada pelo cérebro (acerto muito abaixo do necessário)',
    'result_coverage': 'demasiados jogos sem resultado comprovado ou em quarentena',
    'negative_return': 'retorno prospetivo negativo com limite superior a 95 % abaixo de zero',
    'redundant_game_set': 'mesmos jogos que uma candidata já listada',
    'no_prospective_results': 'ainda sem resultados prospetivos comprovados',
    'below_observe_minimum': 'menos de 10 resultados prospetivos comprovados',
    'below_decision_minimum': 'menos de 30 resultados prospetivos comprovados',
    'below_confirmation_sample': 'menos de 300 resultados prospetivos comprovados',
    'concentrated_in_few_days': 'resultados concentrados em poucos dias (correlação provável)',
    'uncertainty_unavailable': 'incerteza não calculável (menos de dois dias de decisão)',
    'not_significant_after_multiplicity': 'limite inferior corrigido para múltiplas hipóteses não é positivo',
    'non_positive_return': 'retorno prospetivo não positivo',
    'experimental_profile': 'perfil experimental: nunca certificável pelas regras atuais',
    'not_certified': 'sem certificação científica ativa',
    'no_executable_prices': 'sem prova de odds executáveis (só odds anunciadas)',
    'costs_unknown': 'custos e comissões desconhecidos',
    'unknown_phase': 'fase (pré-jogo/LIVE) desconhecida em parte da amostra',
}


def load_shortlist(path=None):
    """Lista curta congelada; ValueError se o ficheiro mudou (um registo novo exige versão nova)."""
    data = json.loads(Path(path or SHORTLIST_PATH).read_text(encoding='utf-8'))
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    if digest != SHORTLIST_SHA256:
        raise ValueError('changed shortlist registration')
    return data


def select_shortlist(cards, limit=4):
    """Regra de seleção fixada no registo da lista curta. Só escolhe o que testar no futuro; nunca é evidência."""
    k = CRITERIA
    pool = [c for c in cards if c['state'] == B and len(c['phases']) == 1
            and 'experimental_profile' not in c['reasons'] and c['confirmed'] >= k['decision_min_results']
            and c['decision_days'] >= k['min_decision_days'] and _finite(c['roi_odds_minus_2pct'])
            and c['roi_odds_minus_2pct'] > 0 and _finite(c['se_day_clustered'])]
    pool.sort(key=lambda c: (-(c['roi_odds_minus_2pct'] - k['reject_upper_z'] * c['se_day_clustered']), c['id']))
    out, seen = [], set()
    for c in pool:
        key = (c['family'], next(iter(c['phases'])))
        if key not in seen:
            seen.add(key)
            out.append(c)
        if len(out) >= limit:
            break
    return out


def shortlist_section(shortlist, rows_by_id, rule_card, validation, as_of_dimensions, post_dimensions):
    """Avalia as entradas congeladas com correção de Bonferroni só sobre o tamanho da lista."""
    if not isinstance(shortlist, dict):
        return None
    entries = shortlist.get('entries') or []
    z = bonferroni_z(len(entries))
    active = (validation or {}).get('state') == 'active'
    cards = []
    for entry in entries:
        if entry.get('id') == RULE_PR55_ID:
            if rule_card is None:
                cards.append({'id': entry['id'], 'family': entry.get('family'), 'state': A, 'label': LABELS[A],
                              'reasons': ['no_prospective_results'], 'confirmed': 0})
                continue
            card = {key: val for key, val in rule_card.items() if key != '_ev'}
            state, why, bounds = classify(rule_card['_ev'], gates=[], z_corrected=z)
        else:
            row = rows_by_id.get(entry.get('id'))
            if row is None:
                cards.append({'id': entry.get('id'), 'family': entry.get('family'), 'state': A, 'label': LABELS[A],
                              'reasons': ['no_prospective_results'], 'confirmed': 0})
                continue
            ev = evidence_of(row)
            certified = active and bool((row.get('confirmation') or {}).get('certified_utc'))
            state, why, bounds = classify(ev, gates=gate_reasons(row, as_of_dimensions, post_dimensions),
                                          z_corrected=z, certified=certified)
            card = _card(row, ev, state, why, bounds)
        card.update(state=state, label=LABELS[state], reasons=why, **bounds)
        cards.append(card)
    return {'registered_utc': shortlist.get('registered_utc'), 'sha256': SHORTLIST_SHA256,
            'size': len(entries), 'z_corrected': round(z, 4), 'entries': cards,
            'validation_window': shortlist.get('validation_window')}


def criteria_sha256(criteria=None):
    text = json.dumps(CRITERIA if criteria is None else criteria, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def bonferroni_z(m, alpha=None):
    alpha = CRITERIA['family_alpha'] if alpha is None else alpha
    return NormalDist().inv_cdf(1 - alpha / max(1, m))


def evidence_of(row):
    """Normaliza uma linha do cérebro: só a evidência PROSPETIVA (economics), nunca a histórica."""
    econ = ((row.get('prospective') or {}).get('economics')) or {}
    return {
        'admitted': econ.get('admitted', 0) or 0,
        'confirmed': econ.get('results_confirmed', 0) or 0,
        'wins': econ.get('wins', 0) or 0, 'losses': econ.get('losses', 0) or 0, 'voids': econ.get('voids', 0) or 0,
        'pending': econ.get('pending', 0) or 0, 'quarantined': econ.get('quarantined', 0) or 0,
        'imputed_losses': econ.get('imputed_losses', 0) or 0,
        'roi_advertised': econ.get('roi_advertised'), 'roi': econ.get('roi_odds_minus_2pct'),
        'se': econ.get('se_day_clustered_odds_minus_2pct'), 'days': econ.get('decision_days', 0) or 0,
        'largest_day_share': econ.get('largest_day_share'), 'phases': econ.get('phases') or {},
        'hit_rate': econ.get('confirmed_hit_rate'), 'mean_odds': econ.get('mean_advertised_odds'),
    }


def gate_reasons(row, as_of_dimensions, post_dimensions):
    """Motivos de rejeição que não dependem do retorno (operacionais e cronológicos)."""
    if row.get('expect') == 'abaixo':
        return ['avoid_hypothesis']
    dims = {c[0] for c in row.get('conditions') or () if isinstance(c, (list, tuple)) and c}
    out = []
    if not row.get('born_verified'):
        out.append('unverified_registration')
    if dims & post_dimensions:
        out.append('post_signal_condition')
    elif not row.get('experimental') and not dims <= as_of_dimensions:
        out.append('not_known_at_decision')
    if row.get('prospective') is None:
        out.append('no_prospective_evidence_possible')
    phases = evidence_of(row)['phases']
    if phases.get('pre') and phases.get('live'):
        out.append('mixed_pre_live')
    if row.get('status') == 'abandonada':
        out.append('abandoned_by_brain')
    return out


def classify(ev, *, gates=(), z_corrected, certified=False, experimental=False):
    """-> (estado, motivos, limites). Determinístico; usa só a evidência prospetiva normalizada."""
    k = CRITERIA
    n, roi, se = ev['confirmed'], ev['roi'], ev['se']
    bounds = {'upper_95': None, 'lower_corrected': None}
    # Com menos de min_decision_days dias, o erro-padrão agrupado não é fiável: sem limites, sem decisão pelo retorno.
    if _finite(roi) and _finite(se) and ev['days'] >= k['min_decision_days']:
        bounds = {'upper_95': round(roi + k['reject_upper_z'] * se, 4), 'lower_corrected': round(roi - z_corrected * se, 4)}
    reasons = list(gates)
    unresolved = ev['imputed_losses'] + ev['quarantined']
    if ev['admitted'] >= k['decision_min_results'] and unresolved > k['max_unresolved_share'] * ev['admitted']:
        reasons.append('result_coverage')
    if n >= k['decision_min_results'] and bounds['upper_95'] is not None and bounds['upper_95'] < 0:
        reasons.append('negative_return')
    if reasons:
        return C, reasons, bounds
    if n < k['observe_min_results']:
        return A, ['no_prospective_results' if n == 0 else 'below_observe_minimum'], bounds
    blocking = []
    if n < k['decision_min_results']:
        blocking.append('below_decision_minimum')
    if ev['days'] < k['min_decision_days'] or (ev['largest_day_share'] or 0) > k['max_day_share']:
        blocking.append('concentrated_in_few_days')
    if bounds['lower_corrected'] is None:
        if ev['days'] >= k['min_decision_days']:
            blocking.append('uncertainty_unavailable')
    elif bounds['lower_corrected'] <= 0:
        blocking.append('not_significant_after_multiplicity')
    if not _finite(roi) or roi <= 0:
        blocking.append('non_positive_return')
    if n < k['confirmation_min_results']:
        blocking.append('below_confirmation_sample')
    if ev['phases'].get('unknown'):
        blocking.append('unknown_phase')
    if experimental:
        blocking.append('experimental_profile')
    if not certified:
        blocking.append('not_certified')
    if not EXECUTABLE_PRICE_EVIDENCE:
        blocking.append('no_executable_prices')
    if not COSTS_KNOWN:
        blocking.append('costs_unknown')
    return (B, blocking, bounds) if blocking else (D, [], bounds)


def _card(row, ev, state, reasons, bounds, baseline=None):
    card = {'id': row['id'], 'family': row.get('family'), 'conditions': row.get('conditions'), 'source': row.get('source'),
            'born_utc': row.get('born_utc'), 'state': state, 'label': LABELS[state], 'reasons': reasons,
            'admitted': ev['admitted'], 'confirmed': ev['confirmed'], 'wins': ev['wins'], 'losses': ev['losses'],
            'voids': ev['voids'], 'pending': ev['pending'], 'quarantined': ev['quarantined'],
            'imputed_losses': ev['imputed_losses'], 'hit_rate': ev['hit_rate'], 'mean_odds': ev['mean_odds'],
            'roi_advertised': ev['roi_advertised'], 'roi_odds_minus_2pct': ev['roi'],
            'se_day_clustered': ev['se'], 'decision_days': ev['days'], 'phases': ev['phases'], **bounds,
            'missing_for_decision': max(0, CRITERIA['decision_min_results'] - ev['confirmed']),
            'missing_for_confirmation': max(0, CRITERIA['confirmation_min_results'] - ev['confirmed']),
            'certified': False if state != D else True}
    if baseline is not None:
        card['baseline'] = baseline
    return card


def build(rows, validation, *, as_of_dimensions, post_dimensions, baseline=None, rule=None, full=None,
          shortlist=None, shortlist_rows=None):
    """rows: linhas do cérebro (brain.evaluate). validation: brain.validation(). baseline(família, fase, born_utc) -> dict
    descritivo opcional. rule: resultado de opportunity_ranking.build (regra PR #55), opcional."""
    active = (validation or {}).get('state') == 'active'
    prepared = []
    for row in rows:
        ev = evidence_of(row)
        gates = gate_reasons(row, as_of_dimensions, post_dimensions)
        prepared.append((row, ev, gates))
    m = sum(1 for _, ev, gates in prepared if not gates and ev['confirmed'] >= CRITERIA['observe_min_results'])
    rule_card = rule_entry(rule)
    if rule_card is not None and rule_card['confirmed'] >= CRITERIA['observe_min_results']:
        m += 1
    z = bonferroni_z(m)
    counts, reasons_seen, listed = dict.fromkeys(STATES, 0), {}, {A: [], B: [], D: []}
    llm_verdicts = []
    game_sets = set()
    order = sorted(prepared, key=lambda t: (-t[1]['confirmed'], -(t[1]['roi'] if _finite(t[1]['roi']) else -9), t[0]['id']))
    for row, ev, gates in order:
        if gates == ['avoid_hypothesis']:
            reasons_seen['avoid_hypothesis'] = reasons_seen.get('avoid_hypothesis', 0) + 1
            continue
        certified = active and bool((row.get('confirmation') or {}).get('certified_utc'))
        state, why, bounds = classify(ev, gates=gates, z_corrected=z, certified=certified,
                                      experimental=bool(row.get('experimental')))
        if state != C and row.get('game_set') is not None:
            if row['game_set'] in game_sets:
                state, why = C, ['redundant_game_set']
            else:
                game_sets.add(row['game_set'])
        counts[state] += 1
        for code in why:
            reasons_seen[code] = reasons_seen.get(code, 0) + 1
        if state in listed:
            listed[state].append((row, ev, state, why, bounds))
        if row.get('source') == 'llm':
            llm_verdicts.append({'id': row['id'], 'state': state, 'label': LABELS[state], 'confirmed': ev['confirmed'],
                                 'reasons': why})
        if full is not None:
            full.append(_card(row, ev, state, why, bounds))
    if rule_card is not None:
        state, why, bounds = classify(rule_card['_ev'], gates=[], z_corrected=z)
        rule_card.update(state=state, label=LABELS[state], reasons=why, **bounds)
        counts[state] += 1
    out = {}
    for state, items in listed.items():
        if state == B:
            items.sort(key=lambda t: (t[4]['lower_corrected'] is None,
                                      -(t[4]['lower_corrected'] if t[4]['lower_corrected'] is not None else 0),
                                      -t[1]['confirmed'], t[0]['id']))
        chosen, per_family = [], {}
        for row, ev, st, why, bounds in items:
            if st == B and 'non_positive_return' in why:
                continue
            fam = row.get('family')
            if per_family.get(fam, 0) >= CRITERIA['listing_per_family']:
                continue
            per_family[fam] = per_family.get(fam, 0) + 1
            phase = next(iter(ev['phases'])) if len(ev['phases']) == 1 else None
            base = baseline(fam, phase, row.get('born_utc')) if baseline is not None and st == B and phase else None
            chosen.append(_card(row, ev, st, why, bounds, base))
            if len(chosen) >= CRITERIA['listing_limit']:
                break
        out[state] = chosen
    short = shortlist_section(shortlist, {r['id']: r for r in shortlist_rows or ()}, rule_card, validation,
                             as_of_dimensions, post_dimensions)
    if rule_card is not None:
        rule_card.pop('_ev')
    validated = out.get(D, [])
    if short is not None:
        validated += [c for c in short['entries'] if c['state'] == D]
    return {
        'schema': SCHEMA, 'criteria': CRITERIA, 'criteria_sha256': criteria_sha256(),
        'counts': counts, 'reasons': dict(sorted(reasons_seen.items(), key=lambda kv: (-kv[1], kv[0]))),
        'reason_labels': REASONS, 'multiplicity': {'candidates_tested': m, 'alpha': CRITERIA['family_alpha'],
                                                    'z_corrected': round(z, 4), 'method': 'bonferroni'},
        'observation': out[A], 'insufficient': out[B], 'validated': validated,
        'rule_pr55': rule_card, 'shortlist': short,
        'llm_verdicts': sorted(llm_verdicts, key=lambda v: (-v['confirmed'], v['id'])),
        'executable_price_evidence': EXECUTABLE_PRICE_EVIDENCE, 'costs_known': COSTS_KNOWN,
        'certification_active': active,
        'verdict': ('RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA' if not validated
                    else 'ESTRATÉGIAS VALIDADAS: %d' % len(validated)),
        'statistical_claim': 'none' if not validated else 'validated',
        'automatic_alerts_changed': False,
    }


def rule_entry(rule):
    """Converte o ranking da regra PR #55 (opportunity_ranking.build) para a mesma evidência normalizada. None sem dados."""
    if not isinstance(rule, dict) or rule.get('status') != 'exploratory_only':
        return None
    rows = rule.get('ranking')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    r = rows[0]
    days = (r.get('liquidated_by_decision_day_utc') or {})
    timeouts = r.get('timeouts_counted_as_losses', 0) or 0
    settled = r.get('settled', 0) or 0
    ev = {'admitted': r.get('forward_games', 0) or 0, 'confirmed': r.get('ft_confirmed', 0) or 0,
          'wins': r.get('wins', 0) or 0, 'losses': max(0, (r.get('losses', 0) or 0) - timeouts), 'voids': 0,
          'pending': r.get('pending', 0) or 0, 'quarantined': r.get('quarantined', 0) or 0, 'imputed_losses': timeouts,
          'roi_advertised': r.get('roi_advertised'), 'roi': r.get('roi_odds_minus_2pct'),
          'se': r.get('se_day_clustered_odds_minus_2pct'), 'days': days.get('groups', 0) or 0,
          'largest_day_share': days.get('largest_share'), 'phases': {'pre': settled} if settled else {},
          'hit_rate': r.get('confirmed_hit_rate'), 'mean_odds': None}
    baseline = rule.get('baseline') or {}
    return {'id': r.get('rule_id'), 'family': 'Polymarket', 'source': 'PR #55 (regra fixa congelada)',
            'admitted': ev['admitted'], 'confirmed': ev['confirmed'], 'wins': ev['wins'], 'losses': ev['losses'],
            'pending': ev['pending'], 'quarantined': ev['quarantined'], 'imputed_losses': ev['imputed_losses'],
            'hit_rate': ev['hit_rate'], 'roi_advertised': ev['roi_advertised'], 'roi_odds_minus_2pct': ev['roi'],
            'se_day_clustered': ev['se'], 'decision_days': ev['days'],
            'missing_for_decision': max(0, CRITERIA['decision_min_results'] - ev['confirmed']),
            'missing_for_confirmation': max(0, CRITERIA['confirmation_min_results'] - ev['confirmed']),
            'baseline': {'description': baseline.get('description'), 'confirmed': baseline.get('ft_confirmed'),
                         'roi_odds_minus_2pct': baseline.get('roi_odds_minus_2pct'),
                         'overlapping': True},
            'certified': False, '_ev': ev}


def llm_feedback(board, limit=8):
    """Linhas curtas para a LLM: o veredicto do motor sobre as propostas da própria LLM e porquê. Só agregados."""
    if not isinstance(board, dict) or board.get('schema') != SCHEMA:
        return []
    lines = ['Quadro de estratégias (critérios %s, fixados antes dos resultados): em observação %d, evidência insuficiente %d, '
             'rejeitadas %d, validadas %d.' % (CRITERIA['version'], *(board['counts'].get(s, 0) for s in STATES)),
             'Veredito do motor: %s. A LLM não decide a certificação.' % board['verdict']]
    labels = board.get('reason_labels') or REASONS
    verdicts = [v for v in board.get('llm_verdicts') or () if isinstance(v, dict) and v.get('state') in LABELS]
    if verdicts:
        lines.append('As tuas propostas (resultados prospetivos comprovados; motivo do estado):')
    for v in verdicts[:limit]:
        lines.append('- %s -> %s, %d; %s' % (v['id'], LABELS[v['state']], v.get('confirmed', 0),
                                              '; '.join(labels.get(code, code) for code in (v.get('reasons') or [])[:3])))
    top = [code for code in board.get('reasons') or {} if code not in ('avoid_hypothesis',)][:4]
    if top:
        lines.append('Motivos mais frequentes de rejeição ou bloqueio: ' + '; '.join(
            '%s (%d)' % (labels.get(code, code), board['reasons'][code]) for code in top) + '.')
    lines.append('Não proponhas condições posteriores ao sinal, nem hipóteses que misturem pré-jogo e LIVE: serão rejeitadas.')
    return lines

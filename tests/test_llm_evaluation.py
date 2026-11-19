"""PR 4: avaliação incremental da LLM — testes adversariais (offline, determinísticos, sem dados de produção).

Cada teste ataca uma garantia do protocolo audits/pr4-llm-protocol.md: fuga de informação futura, nascimento recuado,
duplicados, equivalências, taxas de acerto acima de 50 %, amostras pequenas ou concentradas no tempo, e reprodutibilidade.
"""
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scangoal import brain, equivalence, llm, llm_eval
from test_chronological_validity import T0, sig

DAY = 1440
P = T0                                   # instante do protocolo nos cenários
LLM_BORN = T0 - timedelta(days=2)
GEN_BORN = T0 - timedelta(days=5)


def iso(t):
    return t.isoformat(timespec='seconds')


def league_signals(league, slot, outcomes, days, odds=2.0, pre=5):
    """`pre` sinais antes do nascimento das LLM (apoio) e um jogo por dia depois de P, com os resultados dados (ciclo)."""
    out = [sig('%s-pre%d' % (league, i), outcomes[i % len(outcomes)], -4 * DAY + slot * 30 + i * 150, league=league, odds=odds) for i in range(pre)]
    out += [sig('%s-d%d' % (league, d), outcomes[d % len(outcomes)], d * DAY + slot * 30, league=league, odds=odds) for d in range(1, days + 1)]
    return out


def hyp(league, source, born, mode=None, expect=None, extra=()):
    h = {'family': 'Jogo aberto', 'conditions': sorted([['Liga', league]] + [list(c) for c in extra]), 'born_utc': iso(born), 'source': source}
    if source == 'llm':
        h.update(mode=mode or 'novas', expect=expect or 'acima', model='m')
    return h


def world(llm_outcomes, ctrl_outcomes, *, days=21, n_llm=10, n_ctrl=10, llm_odds=2.0, ctrl_odds=2.0):
    signals, registry = [], {}
    for i in range(n_llm):
        lg = 'A%d' % i
        signals += league_signals(lg, i, llm_outcomes, days, llm_odds)
        h = hyp(lg, 'llm', LLM_BORN)
        registry[brain.hyp_id(h['family'], [tuple(c) for c in h['conditions']])] = h
    for i in range(n_ctrl):
        lg = 'B%d' % i
        signals += league_signals(lg, n_llm + i, ctrl_outcomes, days, ctrl_odds)
        h = hyp(lg, 'gerador', GEN_BORN)
        registry[brain.hyp_id(h['family'], [tuple(c) for c in h['conditions']])] = h
    return signals, registry


def run(signals, registry, *, as_of=None, problems=(), born=None, protocol=P):
    as_of = as_of or T0 + timedelta(days=40)   # depois de C: a avaliação confirmatória (relógio fixado em C)
    born_eff = born or {hid: brain.chronology.born(h) for hid, h in registry.items()}
    return llm_eval.evaluate(signals, registry, as_of=as_of, born_eff=born_eff, protocol_time=protocol, problems=list(problems))


FAST = dict(BOOT_REPLICATES=99)
FINAL = T0 + timedelta(days=368)           # C = P + 365 + 3 dias com P = T0 (protocolo v4, §0-B)
V = llm_eval.VERDICTS


class Leakage(unittest.TestCase):
    def test_T1_future_data_cannot_change_an_evaluation_in_the_past(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=8)
        as_of = T0 + timedelta(days=5, hours=12)
        future = [sig('A0-late%d' % i, 1.0, 5 * DAY + 800 + i * 200, league='A0') for i in range(6)]   # lidos depois de as_of
        # lidos antes de as_of, resultado só observado depois: ganhar ou perder não pode mudar nada em as_of
        pending = lambda w: [sig('A%d-open' % i, w, 5 * DAY + 720 - 30 + i, league='A%d' % i) for i in range(10)]
        with patch.multiple(llm_eval, **FAST):
            base = llm_eval.dumps(run(signals + pending(-1.0), registry, as_of=as_of))
            self.assertEqual(llm_eval.dumps(run(signals + pending(1.0) + future, registry, as_of=as_of)), base)
            self.assertNotEqual(llm_eval.dumps(run(signals + pending(1.0), registry, as_of=as_of + timedelta(days=1))),
                                llm_eval.dumps(run(signals + pending(-1.0), registry, as_of=as_of + timedelta(days=1))))   # e depois muda

    def test_T2_backdated_birth_is_corrected_by_the_first_publication(self):
        registry = {'x': hyp('A0', 'llm', T0 - timedelta(days=10))}
        published = T0 - timedelta(days=1)
        self.assertEqual(llm_eval.births(registry, {'x': published}, True)['x'], published)
        self.assertIsNone(llm_eval.births(registry, {}, True)['x'])                        # nunca publicada: sem nascimento
        self.assertEqual(llm_eval.births(registry, {}, False)['x'], T0 - timedelta(days=10))  # só descrição (não verificada)
        games = [sig('g%d' % i, 1.0, -5 * DAY + i * 200, league='A0') for i in range(5)]   # entre o born_utc recuado e a publicação
        self.assertEqual(llm_eval.admitted(games, published), {})
        self.assertEqual(len(llm_eval.admitted(games, T0 - timedelta(days=10))), 5)

    def test_T3_result_known_before_the_start_is_never_evidence(self):
        g = [sig('g', 1.0, 10, known=30)]
        self.assertEqual(llm_eval.admitted(g, T0 + timedelta(minutes=20)), {})              # resultado visto 10 min depois do início
        self.assertEqual(llm_eval.admitted(g, T0 + timedelta(minutes=50)), {})              # sinal anterior ao início

    def test_a_result_of_the_game_seen_before_the_decision_excludes_it(self):
        # aposta decidível e lida a tempo, mas o sistema já tinha visto um resultado desse jogo (outro mercado ou outro tipo de
        # sinal) antes da decisão: não é uma decisão válida (com a margem de 15 min do relatório)
        self.assertEqual(llm_eval.admitted([sig('g', 1.0, 60, first=-10)], T0), {})
        self.assertEqual(llm_eval.admitted([sig('g', 1.0, 60, first=10)], T0), {})
        self.assertEqual(len(llm_eval.admitted([sig('g', 1.0, 60, first=20)], T0)), 1)

    def test_T4_signal_read_after_its_result_is_never_evidence(self):
        self.assertEqual(llm_eval.admitted([sig('g', 1.0, 60, seen=200, known=145)], T0), {})
        self.assertEqual(len(llm_eval.admitted([sig('g', 1.0, 60)], T0)), 1)

    def test_born_after_as_of_and_missing_birth_are_excluded(self):
        signals, registry = world([1.0], [1.0], days=3, n_llm=2, n_ctrl=5)
        registry['bad'] = dict(hyp('A9', 'llm', LLM_BORN), born_utc='ontem')
        born = {hid: brain.chronology.born(h) for hid, h in registry.items()}
        born['Jogo aberto | Liga = A1'] = T0 + timedelta(days=99)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, born=born)
        self.assertEqual(res['population']['excluded'], {'no_proven_birth': 1, 'unavailable_at_decision': 0})
        self.assertEqual((res['population']['evaluated'], res['population']['llm_hypotheses']), (1, 2))   # a futura não existe para a avaliação

    def test_late_earlier_signal_cannot_define_candidate_or_control(self):
        dimension = 'Sinal anterior de outro tipo no mesmo jogo'
        candidate = hyp('A0', 'llm', LLM_BORN, extra=[(dimension, 'sim')])
        safe = hyp('A0', 'llm', LLM_BORN)
        bad_control = hyp('A0', 'gerador', GEN_BORN, extra=[(dimension, 'sim')])
        old = sig('fixture', 1.0, 60, league='A0')
        late = dict(sig('fixture', 1.0, 50, league='A0'), type='TOTALS_FORM',
                    signal_seen_utc=iso(T0 + timedelta(minutes=90)))
        registry = {'candidate': candidate, 'safe': safe, 'control': bad_control}
        before = run([old], registry, as_of=T0 + timedelta(days=5))
        after = run([old, late], registry, as_of=T0 + timedelta(days=5))
        for output in (before, after):
            self.assertEqual(output['population']['excluded']['unavailable_at_decision'], 1)
            self.assertEqual([h['id'] for h in output['hypotheses']], ['safe'])
            self.assertEqual(output['hypotheses'][0]['controls'], 0)
            self.assertEqual(output['hypotheses'][0]['protocol_games'], 1)



class HitRateIsNotEvidence(unittest.TestCase):
    def test_T5_T6_high_hit_rate_with_the_same_profit_as_the_controls_is_not_utility(self):
        sixty = [1.0, 1.0, 1.0, -1.0, -1.0]                     # 60 % de acerto à odd 1,50: perde dinheiro (necessário 68 %)
        signals, registry = world(sixty, sixty, llm_odds=1.5, ctrl_odds=1.5)
        res = run(signals, registry, as_of=FINAL)
        self.assertEqual((res['primary']['k_sup'], res['verdict']), (1.0, V[4]))
        self.assertTrue(all(h['protocol_mean_profit_observed'] < 0 for h in res['hypotheses']))
        seventy = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0]
        res = run(*world(seventy, seventy), as_of=FINAL)
        self.assertEqual((res['primary']['k_sup'], res['verdict']), (1.0, V[4]))

    def test_no_criterion_reads_hit_rates(self):
        # mesma evidência de lucro, acertos descritivos diferentes: o veredicto e a comparação não mudam
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=16)
        with patch.multiple(llm_eval, **FAST):
            a = run(signals, registry)
        tampered = [dict(b, w=0.5) if b['w'] == 1.0 and b['league'].startswith('A') else b for b in signals]   # meia vitória: menos lucro
        with patch.multiple(llm_eval, **FAST):
            b = run(tampered, registry)
        self.assertLess(b['primary']['mean_daily_diff_descriptive'], a['primary']['mean_daily_diff_descriptive'])
        self.assertLessEqual(b['primary']['k_final'], a['primary']['k_final'])
        self.assertNotIn('"prob_above":', json.dumps(a))

class Duplicates(unittest.TestCase):
    VOCAB = {'Jogo aberto': {'Liga': {'A0': 9, 'A1': 9}, 'Minuto': {'31 a 60': 18}, 'Faixa de odd': {'odd 2,00 a 3,00': 18},
                             'Outros sinais no mesmo jogo': {'jogo com um só tipo de sinal': 18}}}

    def signals(self):
        return [sig('a%d' % i, None, i * 60, league='A0') for i in range(9)] + [sig('b%d' % i, None, i * 60, league='A1') for i in range(9)]

    def accept(self, items, registry=None):
        registry = registry or {}
        why = {}
        good, bad = llm.accept(json.dumps({'hypotheses': items}), self.VOCAB, registry, None, 'acima',
                               equivalence.Redundancy(self.signals(), registry), why)
        return [hid for hid, _ in good], why

    def test_T7_permuted_conditions_are_the_same_hypothesis(self):
        reg = {'Jogo aberto | Liga = A0 & Minuto = 31 a 60': hyp('A0', 'gerador', GEN_BORN, extra=[('Minuto', '31 a 60')])}
        good, why = self.accept([{'family': 'Jogo aberto', 'conditions': [['Minuto', '31 a 60'], ['Liga', 'A0']]}], reg)
        self.assertEqual((good, why), ([], {'duplicate': 1}))

    def test_T8_redundant_condition_is_rejected_but_similarity_alone_is_not(self):
        # todos os sinais estão no minuto 50: "Minuto = 31 a 60" não acrescenta nada a "Liga = A0"
        good, why = self.accept([{'family': 'Jogo aberto', 'conditions': [['Liga', 'A0'], ['Minuto', '31 a 60']]},
                                 {'family': 'Jogo aberto', 'conditions': [['Minuto', '31 a 60']]},
                                 {'family': 'Jogo aberto', 'conditions': [['Liga', 'A0']]},
                                 {'family': 'Jogo aberto', 'conditions': [['Liga', 'A0'], ['Faixa de odd', 'odd 2,00 a 3,00']]}])
        self.assertEqual((good, why), (['Jogo aberto | Liga = A0'], {'equivalent': 3}))
        # mesmos sinais que uma hipótese já existente, com outro nome
        reg = {'Jogo aberto | Liga = A1': hyp('A1', 'gerador', GEN_BORN)}
        sigs = self.signals() + [sig('c%d' % i, None, i * 60, league='A0', odds=1.6) for i in range(9)]
        r = equivalence.Redundancy(sigs, reg)
        self.assertEqual(r.reason('Jogo aberto', [('Liga', 'A1'), ('Faixa de odd', 'odd 2,00 a 3,00')])[0], 'condition_adds_nothing')
        self.assertIsNone(r.reason('Jogo aberto', [('Liga', 'A0'), ('Faixa de odd', 'odd 2,00 a 3,00')]))   # 9 de 18: distinta
        # Jaccard muito alto (17/18) mas conjunto diferente: nunca rejeitada só por semelhança (decisão de 15/11/2026)
        near = self.signals() + [sig('z', None, 999, league='A0', odds=1.6)]
        self.assertIsNone(equivalence.Redundancy(near, {}).reason('Jogo aberto', [('Liga', 'A0'), ('Faixa de odd', 'odd 2,00 a 3,00')]))

    def test_equivalent_twins_in_the_same_reply_count_once(self):
        # A0 só de madrugada, A1 só de manhã: "Hora = madrugada" apanha os mesmos sinais que "Liga = A0", aceite antes na mesma resposta
        sigs = [sig('a%d' % i, None, i * 30, league='A0') for i in range(9)] + [sig('b%d' % i, None, 420 + i * 30, league='A1') for i in range(9)]
        reg = {}
        r = equivalence.Redundancy(sigs, reg)
        vocab = dict(self.VOCAB, **{'Jogo aberto': dict(self.VOCAB['Jogo aberto'], **{'Hora (UTC)': {'madrugada': 9, 'manhã': 9}})})
        items = [{'family': 'Jogo aberto', 'conditions': [['Liga', 'A0']]}, {'family': 'Jogo aberto', 'conditions': [['Hora (UTC)', 'madrugada']]}]
        good, bad = llm.accept(json.dumps({'hypotheses': items}), vocab, reg, None, 'acima', r)
        self.assertEqual(([h for h, _ in good], bad), (['Jogo aberto | Liga = A0'], 1))

    def test_T17_hostile_model_output(self):
        items = ([{'family': 'Jogo aberto', 'conditions': [['Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal']]}]   # POST: fora do vocabulário enviado
                 + [{'family': 'Jogo aberto', 'conditions': [['Liga', 'inventada']]}, {'family': 'X; rm -rf /', 'conditions': [['Liga', 'A0']]},
                    {'family': 'Jogo aberto', 'conditions': 'Liga'}, {'family': 'Jogo aberto', 'conditions': [['Liga', 'A0'], ['Liga', 'A1']]}]
                 + [{'family': 'Jogo aberto', 'conditions': [['Liga', 'A%d' % (i % 2)]], 'reason': '<script>%s</script>' % ('x' * 5000)} for i in range(50)])
        vocab = {'Jogo aberto': {k: v for k, v in self.VOCAB['Jogo aberto'].items() if k not in brain.POST}}
        good, bad = llm.accept('lixo antes ' + json.dumps({'hypotheses': items}) + ' lixo depois', vocab, {}, None, 'acima', equivalence.Redundancy(self.signals(), {}))
        self.assertEqual(sorted(h for h, _ in good), ['Jogo aberto | Liga = A0', 'Jogo aberto | Liga = A1'])
        self.assertTrue(all(len(e['reason']) <= 200 and '<' not in e['reason'] for _, e in good))
        self.assertEqual(llm.accept('x' * 3_000_000, vocab, {}), ([], 0))


class Grouping(unittest.TestCase):
    def h(self, conds, expect='acima', family='Jogo aberto'):
        return {'family': family, 'conditions': [list(c) for c in conds], 'expect': expect}

    def test_no_transitive_chains_and_no_incompatible_groups(self):
        a, b, c = frozenset(range(0, 100)), frozenset(range(7, 107)), frozenset(range(14, 114))   # a~b 0,87; b~c 0,87; a~c 0,75
        g = equivalence.groups([('a', self.h([('Liga', '1')]), a), ('b', self.h([('Minuto', 'x')]), b), ('c', self.h([('Dia', 'y')]), c)], 0.85)
        self.assertEqual(g, [['a', 'b'], ['c']])
        same = frozenset(range(50))
        g = equivalence.groups([('a', self.h([('Liga', '1')]), same), ('b', self.h([('Liga', '2')]), same),       # condição contraditória
                                ('c', self.h([('Dia', 'y')], expect='abaixo'), same),                             # expectativa oposta
                                ('d', self.h([('Dia', 'y')], family='Betfair'), same),                            # outra família
                                ('e', self.h([('Minuto', 'x')]), same)], 0.85)
        self.assertEqual(g, [['a', 'e'], ['b'], ['c'], ['d']])
        tiny = frozenset(range(5))
        self.assertEqual(equivalence.groups([('a', self.h([('Liga', '1')]), tiny), ('b', self.h([('Dia', 'y')]), tiny)], 0.85), [['a'], ['b']])

    def test_sensitivity_thresholds(self):
        a, b = frozenset(range(100)), frozenset(range(12, 112))                                                  # Jaccard 0,79
        items = [('a', self.h([('Liga', '1')]), a), ('b', self.h([('Dia', 'y')]), b)]
        self.assertEqual([len(equivalence.groups(items, t)) for t in (0.75, 0.80, 0.85)], [1, 2, 2])

    def test_T9_a_flood_of_equivalent_clones_counts_once(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=16, n_llm=3, n_ctrl=6)
        signals += [sig('A0-early%d' % i, None, -4 * DAY + 1000 + i, league='A0') for i in range(3)]   # 8 jogos de A0 lidos antes do nascimento
        with patch.multiple(llm_eval, **FAST):
            base = run(signals, registry)
            flood = dict(registry)
            for i in range(100):    # a mesma liga com uma condição sempre verdadeira: o mesmo conjunto de jogos (clones com sorte)
                flood['clone%03d' % i] = hyp('A0', 'llm', LLM_BORN + timedelta(minutes=i + 1), extra=[('Minuto', '31 a 60')])
            res = run(signals, flood)
        self.assertEqual(res['population']['groups'], base['population']['groups'])
        self.assertEqual(res['population']['evaluated'], 103)
        self.assertEqual(res['primary'], base['primary'])


class Comparisons(unittest.TestCase):
    def test_T10_refinement_is_measured_against_its_parent(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=16, n_llm=0, n_ctrl=6)
        signals += league_signals('A0', 9, [1.0, 1.0, 1.0, -1.0], 16)
        parent = hyp('A0', 'gerador', GEN_BORN)
        child = hyp('A0', 'llm', LLM_BORN, mode='refinar', extra=[('Minuto', '31 a 60')])
        registry['Jogo aberto | Liga = A0'] = parent
        registry['Jogo aberto | Liga = A0 & Minuto = 31 a 60'] = child
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry)
        s4 = res['secondary']['S4_refinar_vs_parents']
        self.assertEqual((s4['games_test'], s4['games_control'], s4['k_sup'], s4['p_e']), (16, 16, 1.0, 1.0))   # não herda crédito da mãe
        child_row = next(h for h in res['hypotheses'] if h['mode'] == 'refinar')
        self.assertEqual((child_row['parents'], child_row['controls']), (1, 0))             # sem controlos de 2 condições: fora da primária
        self.assertEqual(res['primary']['games_test'], 0)

    def test_T11_avoid_mode_is_judged_against_the_family_baseline_never_the_break_even(self):
        signals, registry = world([], [-1.0, -1.0, 1.0], days=16, n_llm=0, n_ctrl=6)
        signals += league_signals('A0', 9, [-1.0, -1.0, 1.0], 16)
        registry['Jogo aberto | Liga = A0'] = hyp('A0', 'llm', LLM_BORN, mode='evitar', expect='abaixo')
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry)
        s5 = res['secondary']['S5_evitar_vs_baseline']
        self.assertTrue(res['hypotheses'][0]['protocol_mean_profit_observed'] < 0)          # "acertou abaixo do necessário"...
        self.assertEqual((s5['k_sup'], s5['significant_holm_exploratory']), (1.0, False))   # ...mas igual à família: nada
        self.assertEqual(res['hypotheses'][0]['controls'], 6)                                # tem controlos (escolhidos sem resultados)...
        self.assertEqual(res['primary']['games_test'], 0)                                    # ...mas evitar nunca entra na primária

    def test_T12_control_choice_never_reads_results(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0, -1.0], days=4)
        flipped = [dict(b, w=-b['w']) if b['w'] else b for b in signals]
        with patch.multiple(llm_eval, **FAST):
            a, b = run(signals, registry), run(flipped, registry)
        self.assertEqual([h['controls_sha256'] for h in a['hypotheses']], [h['controls_sha256'] for h in b['hypotheses']])
        self.assertTrue(all(h['controls'] == 10 for h in a['hypotheses']))

    def test_controls_must_exist_before_the_proposal_and_have_comparable_support(self):
        signals, registry = world([1.0], [1.0], days=2, n_llm=1, n_ctrl=6)
        registry['Jogo aberto | Liga = B0']['born_utc'] = iso(LLM_BORN + timedelta(minutes=1))   # nasceu depois: não serve
        signals += [sig('B1-big%d' % i, 1.0, -4 * DAY + 900 + i, league='B1') for i in range(20)]   # apoio 5x maior: não serve
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry)
        self.assertEqual(res['hypotheses'][0]['controls'], 4)
        self.assertEqual(res['population']['without_controls'], 1)                            # 4 < K_MIN: fora da primária


class Verdicts(unittest.TestCase):
    def test_positive_verdict_needs_every_minimum_and_proofs(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=60)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, as_of=FINAL)
            unproven = run(signals, registry, as_of=FINAL, problems=['history unavailable: shallow repository'])
        self.assertEqual(res['insufficient'], [])
        self.assertGreaterEqual(res['primary']['k_sup'], 20)
        self.assertEqual(res['verdict'], V[1])
        self.assertIn('não é prova de vantagem', res['verdict'])
        self.assertIsNotNone(res['concluded_on'])
        self.assertEqual((unproven['verdict'], unproven['verified']), ('não verificada', False))

    def test_T18_small_or_concentrated_samples_are_insufficient(self):
        with patch.multiple(llm_eval, **FAST):
            small = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=5), as_of=FINAL)
            self.assertEqual(small['verdict'], V[3])
            # 10 grupos x 24 jogos, mas tudo em 6 dias (4 jogos por dia e liga): falta diversidade temporal
            signals, registry = world([1.0], [1.0], n_llm=10, n_ctrl=10, days=0)
            for i in range(10):
                for lg, out in (('A%d' % i, [1.0, 1.0, 1.0, -1.0]), ('B%d' % i, [1.0, -1.0])):
                    signals += [sig('%s-x%d' % (lg, k), out[k % len(out)], (k // 4 + 1) * DAY + (i * 4 + k % 4) * 20 + (300 if lg[0] == 'B' else 0), league=lg) for k in range(24)]
            dense = run(signals, registry, as_of=FINAL)
        self.assertEqual(dense['primary']['games_test'], 240)
        self.assertEqual(dense['verdict'], V[3])
        self.assertIn('days with llm games < 14', dense['insufficient'])

    def test_each_minimum_fails_on_its_own(self):
        with patch.multiple(llm_eval, **FAST):
            few = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=30, n_llm=8), as_of=FINAL)   # 240 jogos em 30 dias, mas só 8 grupos
            signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=14, n_ctrl=15)      # 14 dias, 10 grupos, 140 + 75 jogos LLM...
            signals += [sig('A%d-burst%d' % (i, k), 1.0, DAY + 600 + k * 15 + i, league='A%d' % i) for i in range(5) for k in range(15)]
            burst = run(signals, registry, as_of=FINAL)                                            # ...com 75 + 10 no 1.º dia (40 %)
            short = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=15, n_ctrl=15))              # 150 jogos LLM em 15 dias, 10 grupos
        self.assertEqual(few['insufficient'], ['groups with >= 10 games < 10'])
        self.assertEqual(burst['insufficient'], ['one day holds more than 20 % of llm games'])
        self.assertEqual(short['insufficient'], ['llm games < 200'])
        # 210 jogos LLM em 21 dias, 200 de controlo mas só em 10 dias: faltam dias emparelhados
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=21, n_ctrl=0)
        for i in range(20):
            signals += league_signals('B%d' % i, 10 + i, [1.0, -1.0], 10)
            registry['Jogo aberto | Liga = B%d' % i] = hyp('B%d' % i, 'gerador', GEN_BORN)
        with patch.multiple(llm_eval, **FAST):
            unpaired = run(signals, registry)
        self.assertEqual(unpaired['insufficient'], ['paired days < 14'])
        self.assertEqual({few['verdict'], burst['verdict']}, {'evidência insuficiente'})

    def test_pre_protocol_evidence_never_supports_conclusions(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=21)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, protocol=T0 + timedelta(days=40))                   # tudo anterior ao protocolo
        self.assertEqual((res['primary']['games_test'], res['primary']['k_sup'], res['verdict']), (0, 1.0, V[2]))
        self.assertEqual(sum(h['pre_protocol_games'] for h in res['hypotheses']), 210)

    def test_T13_T20_deterministic_and_never_claims_validation(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=15)
        with patch.multiple(llm_eval, **FAST):
            a, b = llm_eval.dumps(run(signals, registry)), llm_eval.dumps(run(list(reversed(signals)), dict(reversed(list(registry.items())))))
        self.assertEqual(a, b)
        text = llm_eval.render(json.loads(a)).lower()
        for word in ('validada', 'confirmada', 'certificada', 'tem vantagem'):
            self.assertNotIn(word, text)

    def test_T14_no_network(self):
        def blocked(*a, **k):
            raise AssertionError('network')
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=3)
        with patch.object(socket.socket, 'connect', blocked), patch.object(socket, 'create_connection', blocked), patch.multiple(llm_eval, **FAST):
            run(signals, registry)
            llm_eval.page_summary(signals, registry)

    def test_T19_page_summary_never_concludes(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0])
        s = llm_eval.page_summary(signals, registry)
        self.assertEqual((s['verdict'], s['llm_hypotheses'], s['groups']), ('não verificada', 10, 10))

    def test_holm(self):
        self.assertEqual(llm_eval.holm([0.01, 0.04, None, 0.03]), [0.04, 0.09, 1.0, 0.09])


class GitRepo(unittest.TestCase):
    """Nascimento e protocolo provados pelo histórico git (repositórios temporários; nada de produção)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, when=None, cwd=None):
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        return subprocess.run(['git', *args], cwd=cwd or self.root, env=env, capture_output=True, check=True).stdout.decode()

    def commit(self, rel, content, when):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            path.unlink()
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content, sort_keys=True), encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'c%d' % self.n, when=when)
        self.n += 1
        return self.git('rev-parse', 'HEAD').strip()


class GitProofs(GitRepo):
    def test_first_publication_and_append_only(self):
        a = hyp('A0', 'llm', T0 - timedelta(days=3))
        self.commit('data/hypotheses.json', {'a': a}, '2026-11-20T10:00:00+00:00')
        self.commit('data/hypotheses.json', {'a': a, 'b': hyp('A1', 'llm', T0)}, '2026-11-20T11:00:00+00:00')
        first, problems = llm_eval.registry_history(self.root, 'HEAD')
        self.assertEqual(problems, [])
        self.assertEqual({k: iso(v) for k, v in first.items()}, {'a': '2026-11-20T10:00:00+00:00', 'b': '2026-11-20T11:00:00+00:00'})
        born = llm_eval.births({'a': a}, first, True)
        self.assertEqual(iso(born['a']), '2026-11-20T10:00:00+00:00')                     # born_utc recuado 3 dias: corrigido

    def test_rewritten_removed_or_deleted_registry_is_unverified(self):
        a = hyp('A0', 'llm', T0)
        self.commit('data/hypotheses.json', {'a': a, 'b': hyp('A1', 'llm', T0)}, '2026-11-20T10:00:00+00:00')
        self.commit('data/hypotheses.json', {'a': dict(a, born_utc=iso(T0 - timedelta(days=9))), 'b': hyp('A1', 'llm', T0)}, '2026-11-20T11:00:00+00:00')
        self.commit('data/hypotheses.json', {'a': a}, '2026-11-20T12:00:00+00:00')
        self.commit('data/hypotheses.json', None, '2026-11-20T13:00:00+00:00')
        _, problems = llm_eval.registry_history(self.root, 'HEAD')
        self.assertTrue(any(p.startswith('entry rewritten') for p in problems))
        self.assertTrue(any(p.startswith('entry removed') for p in problems))
        self.assertTrue(any(p.startswith('registry deleted') for p in problems))

    def test_non_monotonic_commit_times_and_shallow_clones_are_unverified(self):
        self.commit('data/hypotheses.json', {}, '2026-11-20T10:00:00+00:00')
        self.commit('data/hypotheses.json', {'a': hyp('A0', 'llm', T0)}, '2026-11-19T10:00:00+00:00')
        self.assertTrue(any('not monotonic' in p for p in llm_eval.registry_history(self.root, 'HEAD')[1]))
        clone = Path(self.tmp.name) / 'shallow'
        self.git('clone', '-q', '--depth', '1', 'file://%s' % self.root, str(clone), cwd=self.tmp.name)
        self.assertEqual(llm_eval.registry_history(clone, 'HEAD'), ({}, ['history unavailable: shallow repository']))
        self.assertEqual(llm_eval.registry_history(Path(self.tmp.name), 'HEAD')[1], ['history unavailable: no git repository'])

    def test_protocol_must_be_the_registered_text_in_its_introducing_commit(self):
        real = (Path(__file__).resolve().parents[1] / llm_eval.PROTOCOL_FILE).read_bytes()
        self.assertEqual(hashlib.sha256(real).hexdigest(), llm_eval.PROTOCOL_SHA256)       # o ficheiro do repositório é o registado
        sha = self.commit(llm_eval.PROTOCOL_FILE, real.decode('utf-8'), '2026-11-20T10:00:00+00:00')
        later = self.commit('x.txt', 'x', '2026-11-20T11:00:00+00:00')
        ev = 'https://github.com/o/r/actions/runs/1'
        self.assertEqual(llm_eval.protocol_proof(self.root, sha, '2026-11-20T10:30:00+00:00', ev), (T0 + timedelta(hours=10, minutes=30), []))
        self.assertIn('protocol commit is not the one that introduced the file', llm_eval.protocol_proof(self.root, later, '2026-11-20T12:00:00+00:00', ev)[1])
        edited = self.commit(llm_eval.PROTOCOL_FILE, real.decode('utf-8') + '\nalterado\n', '2026-11-20T12:00:00+00:00')
        self.assertIn('protocol content differs from the registered one', llm_eval.protocol_proof(self.root, edited, '2026-11-20T13:00:00+00:00', ev)[1])
        self.assertEqual(llm_eval.protocol_proof(self.root, 'deadbeef')[1], ['protocol unavailable at deadbeef'])

    def test_P2C_protocol_start_is_the_first_verifiable_publication(self):
        real = (Path(__file__).resolve().parents[1] / llm_eval.PROTOCOL_FILE).read_bytes()
        sha = self.commit(llm_eval.PROTOCOL_FILE, real.decode('utf-8'), '2026-11-20T10:00:00+00:00')
        ev = 'https://github.com/o/r/actions/runs/1'
        # hora do commit escrita pelo autor: sozinha não prova nada
        self.assertEqual(llm_eval.protocol_proof(self.root, sha), (T0 + timedelta(hours=10), ['protocol publication not proven']))
        self.assertIn('protocol publication not proven', llm_eval.protocol_proof(self.root, sha, '2026-11-20T11:00:00+00:00', None)[1])
        self.assertIn('protocol publication not proven', llm_eval.protocol_proof(self.root, sha, '2026-11-20T11:00:00+00:00', 'https://exemplo.pt/x')[1])
        self.assertIn('protocol publication before its commit', llm_eval.protocol_proof(self.root, sha, '2026-11-20T09:00:00+00:00', ev)[1])
        # commit com data recuada, publicação real 3 dias depois: P é a publicação
        self.assertEqual(llm_eval.protocol_proof(self.root, sha, '2026-11-23T10:00:00+00:00', ev), (T0 + timedelta(days=3, hours=10), []))
        # os protocolos v1 (9f7b2aa) e v2 (25cd2d2) ficam no repositório mas deixaram de ser aceites
        for old in ('audits/pr4-llm-protocol.md', 'audits/pr4-llm-protocol-v2.md'):
            text = (Path(__file__).resolve().parents[1] / old).read_bytes()
            self.assertNotEqual(hashlib.sha256(text).hexdigest(), llm_eval.PROTOCOL_SHA256)
            sha_old = self.commit(llm_eval.PROTOCOL_FILE, text.decode('utf-8'), '2026-11-21T10:00:00+00:00')
            self.assertIn('protocol content differs from the registered one', llm_eval.protocol_proof(self.root, sha_old, '2026-11-21T11:00:00+00:00', ev)[1])

    def test_P2C_data_between_commit_and_publication_is_not_prospective(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=21)
        with patch.multiple(llm_eval, **FAST):
            at_commit = run(signals, registry, protocol=T0)
            at_publication = run(signals, registry, protocol=T0 + timedelta(days=10, hours=12))   # publicada 10,5 dias depois do commit
        self.assertEqual(at_commit['primary']['games_test'], 210)
        self.assertEqual(at_publication['primary']['games_test'], 110)                   # só os 11 dias depois da publicação
        self.assertEqual(sum(h['pre_protocol_games'] for h in at_publication['hypotheses']), 100)


class RetroactiveRevisions(GitRepo):
    """Ponto 4 da auditoria #5916502096: a avaliação não pode ser mudada por revisões retroativas dos dados."""

    SINCE = T0

    def msg(self, mid, collected):
        return json.dumps({'collected_at_utc': collected, 'date_utc': collected, 'message_id': mid, 'sha256': 'x%d' % mid, 'text': 't'}, sort_keys=True)

    def write_messages(self, lines, when):
        return self.commit('data/messages.jsonl', ''.join(l + '\n' for l in lines), when)

    def results(self, entries, when):
        return self.commit('data/results_hl.json', {'results': entries}, when)

    def problems(self):
        return llm_eval.observation_proof(self.root, 'HEAD', self.SINCE)

    def test_clean_history_is_proven(self):
        m1 = self.msg(1, '2026-11-20T10:00:00+00:00')
        self.write_messages([m1], '2026-11-20T10:04:00+00:00')
        self.write_messages([m1, self.msg(2, '2026-11-20T11:00:00+00:00')], '2026-11-20T11:06:00+00:00')
        self.results({'g1': {'ft': [1, 0], 'matched_at_utc': '2026-11-20T12:00:00+00:00'}}, '2026-11-20T12:01:00+00:00')
        self.assertEqual(self.problems(), [])

    def test_rewritten_message_is_detected(self):
        self.write_messages([self.msg(1, '2026-11-20T10:00:00+00:00')], '2026-11-20T10:04:00+00:00')
        self.write_messages([self.msg(1, '2026-11-20T09:00:00+00:00')], '2026-11-20T12:00:00+00:00')
        self.assertTrue(any(x.startswith('messages rewritten') for x in self.problems()))

    def test_backdated_message_or_result_is_detected(self):
        self.write_messages([self.msg(1, '2026-11-20T08:00:00+00:00')], '2026-11-20T10:00:00+00:00')         # carimbo 2 h antes
        self.results({'g1': {'ft': [1, 0], 'matched_at_utc': '2026-11-20T08:00:00+00:00'}}, '2026-11-20T10:00:00+00:00')
        found = self.problems()
        self.assertTrue(any('message 1 published out of time' in x for x in found))
        self.assertTrue(any('results_hl.json entry published out of time' in x for x in found))

    def test_result_changed_or_removed_after_publication_is_detected(self):
        self.results({'g1': {'ft': [1, 0], 'matched_at_utc': '2026-11-20T10:00:00+00:00'},
                      'g2': {'ft': [0, 0], 'matched_at_utc': '2026-11-20T10:00:00+00:00'}}, '2026-11-20T10:01:00+00:00')
        self.results({'g1': {'ft': [2, 0], 'matched_at_utc': '2026-11-20T10:00:00+00:00'}}, '2026-11-20T11:00:00+00:00')
        found = self.problems()
        self.assertTrue(any('entry rewritten' in x and 'g1' in x for x in found))
        self.assertTrue(any('entry removed' in x and 'g2' in x for x in found))

    def test_observations_before_the_protocol_are_not_rechecked_but_rewrites_always_are(self):
        self.write_messages([self.msg(1, '2026-11-10T08:00:00+00:00')], '2026-11-10T12:00:00+00:00')         # antigo, publicado tarde
        self.assertEqual(self.problems(), [])
        self.write_messages([], '2026-11-21T10:00:00+00:00')                                                     # apagado depois
        self.assertTrue(any(x.startswith('messages rewritten') for x in self.problems()))

    def test_shallow_clone_is_unverified(self):
        self.write_messages([self.msg(1, '2026-11-20T10:00:00+00:00')], '2026-11-20T10:04:00+00:00')
        self.write_messages([self.msg(1, '2026-11-20T10:00:00+00:00'), self.msg(2, '2026-11-20T11:00:00+00:00')], '2026-11-20T11:04:00+00:00')
        clone = Path(self.tmp.name) / 'shallow'
        self.git('clone', '-q', '--depth', '1', 'file://%s' % self.root, str(clone), cwd=self.tmp.name)
        self.assertEqual(llm_eval.observation_proof(clone, 'HEAD', self.SINCE), ['observations: shallow repository'])

    def test_final_data_commit_is_the_first_at_or_after_C(self):
        c = T0 + timedelta(days=368)
        self.results({'g1': {'ft': [1, 0], 'matched_at_utc': '2027-11-20T10:00:00+00:00'}}, '2027-11-20T10:01:00+00:00')
        first = self.results({'g1': {'ft': [1, 0], 'matched_at_utc': '2027-11-20T10:00:00+00:00'},
                              'g2': {'ft': [0, 1], 'matched_at_utc': '2027-11-23T00:05:00+00:00'}}, '2027-11-23T00:06:00+00:00')
        self.commit('data/results_hl.json', {'results': {'g1': {'ft': [3, 0], 'matched_at_utc': '2027-11-20T10:00:00+00:00'}}}, '2027-11-25T00:00:00+00:00')
        self.assertEqual(c.isoformat(), '2027-11-23T00:00:00+00:00')
        self.assertEqual(llm_eval.confirmation_snapshot(self.root, 'HEAD', c), first)             # a revisão posterior não lhe chega
        self.assertIsNone(llm_eval.confirmation_snapshot(self.root, 'HEAD', c + timedelta(days=30)))


class ReviewP1(unittest.TestCase):
    """Revisão independente da PR #27: decisões históricas só com o que o sistema tinha LIDO nesse instante."""

    def test_P1A_signal_sent_before_birth_but_read_after_never_affects_controls(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=3, n_llm=2, n_ctrl=6)
        late = [sig('B0-late%d' % i, None, -3 * DAY + i, seen=3 * DAY, league='B0') for i in range(20)]   # emitidos antes, lidos depois
        early = [sig('B0-early%d' % i, None, -3 * DAY + i, seen=10, league='B0') for i in range(20)]     # emitidos e lidos antes
        with patch.multiple(llm_eval, **FAST):
            a, b, c = run(signals, registry), run(signals + late, registry), run(signals + early, registry)
        fp = lambda r: [h['controls_sha256'] for h in r['hypotheses']]
        self.assertEqual(fp(a), fp(b))                                                        # leitura tardia: nada muda
        self.assertEqual([h['controls'] for h in a['hypotheses']], [6, 6])
        self.assertEqual([h['controls'] for h in c['hypotheses']], [5, 5])                   # conhecidos no nascimento: B0 fica fora (5x o apoio)

    def scenario(self):
        reg = {'Jogo aberto | Liga = A0': hyp('A0', 'llm', LLM_BORN)}
        y = hyp('C', 'llm', LLM_BORN + timedelta(days=1))
        y['conditions'] = [['Hora (UTC)', 'tarde']]
        reg['Jogo aberto | Hora (UTC) = tarde'] = y
        return reg

    def groups_of(self, res):
        return [(h['id'], h['group'], h['representative']) for h in res['hypotheses']]

    def test_P1B_later_games_cannot_merge_a_historical_split(self):
        base = [sig('A0-m%d' % i, None, -4 * DAY + 60 + i * 20, league='A0') for i in range(10)]          # A0 de madrugada
        base += [sig('C-t%d' % i, None, -4 * DAY + 800 + i * 20, league='C') for i in range(10)]          # outra liga à tarde
        later = [sig('A0-t%d-%d' % (d, i), None, d * DAY + 760 + i, league='A0') for d in range(1, 21) for i in range(10)]   # depois: A0 à tarde
        with patch.multiple(llm_eval, **FAST):
            a, b = run(base, self.scenario()), run(base + later, self.scenario())
        self.assertEqual(self.groups_of(a), self.groups_of(b))
        self.assertEqual((b['population']['groups'], [h['representative'] for h in b['hypotheses']]), (2, [True, True]))
        self.assertEqual(llm_eval.page_summary(base + later, self.scenario())['groups'], 2)

    def test_P1B_later_games_cannot_split_a_historical_group(self):
        base = [sig('A0-t%d' % i, None, -4 * DAY + 760 + i, league='A0') for i in range(10)]              # no nascimento: iguais
        later = [sig('C-t%d-%d' % (d, i), None, d * DAY + 760 + i, league='C') for d in range(1, 21) for i in range(10)]   # depois divergem
        with patch.multiple(llm_eval, **FAST):
            a, b = run(base, self.scenario()), run(base + later, self.scenario())
        self.assertEqual(self.groups_of(a), self.groups_of(b))
        self.assertEqual(b['population']['groups'], 1)
        self.assertEqual([h['representative'] for h in b['hypotheses']], [True, False])
        self.assertEqual([h['membership_games_at_birth'] for h in b['hypotheses']], [10, 10])

    def test_P1B_unread_signals_at_birth_do_not_count(self):
        base = [sig('A0-m%d' % i, None, -4 * DAY + 60 + i * 20, league='A0') for i in range(10)]
        base += [sig('C-t%d' % i, None, -4 * DAY + 800 + i * 20, league='C') for i in range(10)]
        # A0 à tarde, emitidos antes do nascimento de "tarde" mas lidos só depois: não podem juntar os grupos
        unread = [sig('A0-u%d' % i, None, -4 * DAY + 760 + i, seen=4 * DAY, league='A0') for i in range(200)]
        with patch.multiple(llm_eval, **FAST):
            a, b = run(base, self.scenario()), run(base + unread, self.scenario())
        self.assertEqual(self.groups_of(a), self.groups_of(b))
        self.assertEqual(b['population']['groups'], 2)


class EProcessV4(unittest.TestCase):
    """Protocolo v4 (P1-D e P1-E): e-processo com garantia de Ville, dias fechados, imputação desfavorável."""

    def test_conclusion_at_any_time_is_permanent(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=60)
        with patch.multiple(llm_eval, **FAST):
            looks = [run(signals, registry, as_of=T0 + timedelta(days=d)) for d in (10, 25, 45, 70, 200)] + [run(signals, registry, as_of=FINAL + timedelta(days=100))]
        concluded = [r['concluded_on'] for r in looks]
        first = next(i for i, c in enumerate(concluded) if c is not None)
        self.assertTrue(all(c is None for c in concluded[:first]))
        self.assertEqual(len(set(concluded[first:])), 1)                                    # a mesma data em todas as avaliações seguintes
        self.assertTrue(all(r['verdict'] == V[1] for r in looks[first:]))
        self.assertTrue(all(r['verdict'] == V[2] for r in looks[:first]))                  # antes: em curso, sem conclusão

    def test_closed_days_are_never_rewritten(self):
        rng = __import__('random').Random(3)
        outcomes = [rng.choice([1.0, -1.0]) for _ in range(7)]
        signals, registry = world(outcomes, list(reversed(outcomes)), days=40)
        with patch.multiple(llm_eval, **FAST):
            early, late = run(signals, registry, as_of=T0 + timedelta(days=20)), run(signals, registry, as_of=T0 + timedelta(days=50))
        self.assertEqual(late['primary']['path'][:len(early['primary']['path'])], early['primary']['path'])
        self.assertTrue(all(row['day'] <= (T0 + timedelta(days=16)).date().isoformat() for row in early['primary']['path']))

    def test_final_result_identical_after_C_even_with_later_data(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0])
        extra = [sig('A%d-late%d' % (i, d), 1.0, d * DAY + i * 30, league='A%d' % i) for i in range(10) for d in range(366, 400)]
        later_reg = dict(registry, **{'Jogo aberto | Liga = A9 & Minuto = 31 a 60': hyp('A9', 'llm', T0 + timedelta(days=366), extra=[('Minuto', '31 a 60')])})
        with patch.multiple(llm_eval, **FAST):
            base = llm_eval.dumps(run(signals, registry, as_of=FINAL))
            for as_of in (FINAL + timedelta(days=1), FINAL + timedelta(days=90)):
                self.assertEqual(llm_eval.dumps(run(signals + extra, later_reg, as_of=as_of)), base)
        self.assertIn('"phase": "final"', base)
        self.assertIn('"verdict": "sem utilidade incremental demonstrada"', base)

    def test_window_ends_at_the_horizon(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=40)
        with patch.multiple(llm_eval, HORIZON_DAYS=30, **FAST):
            res = run(signals, registry, as_of=T0 + timedelta(days=60))
        # dias 1 a 29 inteiros; no dia 30 só A0 (decidido exatamente em H); nada depois de H
        self.assertEqual(res['primary']['games_test'], 10 * 29 + 1)
        self.assertEqual((res['horizon_utc'], res['final_utc']), (iso(T0 + timedelta(days=30)), iso(T0 + timedelta(days=33))))

    def test_hypotheses_born_after_the_horizon_are_not_evaluated(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=3, n_llm=2, n_ctrl=6)
        registry['late'] = hyp('A7', 'llm', T0 + timedelta(days=30, minutes=1))
        with patch.multiple(llm_eval, HORIZON_DAYS=30, **FAST):
            res = run(signals, registry, as_of=T0 + timedelta(days=60))
        self.assertEqual(res['population']['evaluated'], 2)
        self.assertNotIn('late', [h['id'] for h in res['hypotheses']])

    def test_minimums_gate_the_conclusion(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=60, n_llm=8)      # efeito forte, mas só 8 grupos
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, as_of=FINAL)
        self.assertGreaterEqual(res['primary']['k_sup'], 20)
        self.assertIsNone(res['concluded_on'])
        self.assertEqual(res['verdict'], V[3])

    def test_threshold_is_exactly_one_over_alpha(self):
        day = lambda d: (T0 + timedelta(days=d)).date().isoformat()
        test = {'t%d' % d: (day(d), 0.96, T0, 0.96) for d in range(40)}
        ctrl = {'c%d' % d: (day(d), -1.0, T0, 0.96) for d in range(40)}                        # X = 1 todos os dias
        with patch.multiple(llm_eval, BOOT_REPLICATES=9):
            res = llm_eval.eprocess([(1.0, test)], [(1.0, ctrl)], T0 + timedelta(days=60), None)
        ks = [row['k'] for row in res['path']]
        first = next(i for i, k in enumerate(ks) if k >= 20)
        self.assertEqual(res['crossed_day'], res['path'][first]['day'])
        self.assertLess(ks[first - 1], 20)
        self.assertEqual(res['path'][0]['lambda'], 0.125)                                     # λ_1 = 1/8, sem passado
        self.assertEqual(res['path'][1]['lambda'], 0.5)                                       # depois, no máximo 1/2

    def test_v5_hypothesis_born_mid_day_contributes_only_from_the_next_day(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=8, n_llm=1, n_ctrl=6)
        born = T0 + timedelta(days=5, hours=6)
        registry['Jogo aberto | Liga = A0']['born_utc'] = iso(born)
        signals += [sig('A0-noon5', 1.0, 5 * DAY + 720, league='A0')]                          # a meio do dia 5, depois do nascimento
        signals += [sig('B%d-noon5' % i, -1.0, 5 * DAY + 730 + i, league='B%d' % i) for i in range(6)]
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry)
        # nada do dia 5; o jogo do dia 6 é às 00:00 exatas (= início, a admissão exige depois): ficam os dias 7 e 8
        self.assertEqual(res['hypotheses'][0]['protocol_games'], 2)
        self.assertEqual(res['primary']['path'][0]['day'], (T0 + timedelta(days=7)).date().isoformat())
        self.assertEqual(llm_eval.next_day(born), T0 + timedelta(days=6))
        self.assertEqual(llm_eval.next_day(T0 + timedelta(days=6)), T0 + timedelta(days=7))   # nascido à meia-noite: o dia seguinte

    def test_v5_claim_scope_separates_conditional_h0_from_per_bet_utility(self):
        positive = llm_eval.claim_scope(V[1])
        self.assertIn('teorema', positive['conditional_h0'])
        self.assertIn('A-sel', positive['per_bet_utility'])
        self.assertIn('não é prova de vantagem', positive['per_bet_utility'])
        for verdict in V[2:]:
            self.assertEqual(llm_eval.claim_scope(verdict)['per_bet_utility'], 'nenhuma afirmação')
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=3)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry)
        self.assertIn('Alcance da conclusão', llm_eval.render(res))

    def test_v5_result_digest_ignores_run_metadata_only(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=3)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, as_of=FINAL)
        a = dict(res, run={'requested_ref': 'aaa', 'python': '3.11'})
        b = dict(res, run={'requested_ref': 'bbb', 'python': '3.12'})
        self.assertEqual(llm_eval.result_digest(a), llm_eval.result_digest(b))
        self.assertNotEqual(llm_eval.result_digest(a), llm_eval.result_digest(dict(b, verdict='x')))

    def without_result(self, b):
        return dict(b, w=None, ft=None, result_known_utc=None, match_result_first_seen_utc=None)

    def test_missing_results_are_imputed_against_the_hypothesis_only_once_the_day_is_closed(self):
        signals, registry = world([1.0], [1.0], days=1, n_llm=1, n_ctrl=5)
        llm_missing = [self.without_result(b) if b['match_id'] == 'A0-d1' else b for b in signals]
        ctrl_missing = [self.without_result(b) if b['match_id'].startswith('B') and b['match_id'].endswith('-d1') else b for b in signals]
        with patch.multiple(llm_eval, **FAST):
            open_day = run(llm_missing, registry, as_of=T0 + timedelta(days=2, hours=12))
            lm = run(llm_missing, registry, as_of=T0 + timedelta(days=10))
            cm = run(ctrl_missing, registry, as_of=T0 + timedelta(days=10))
        self.assertEqual(open_day['primary']['path'], [])                                      # dia 1 ainda aberto: nada imputado
        self.assertEqual(lm['primary']['path'][0]['x'], -1.0)                                 # LLM sem resultado: perda total
        self.assertEqual(cm['primary']['path'][0]['x'], 0.0)                                  # controlo sem resultado: ganho máximo
        self.assertEqual(lm['hypotheses'][0]['protocol_games'], 1)                            # admitido: a admissão não depende do resultado
        late = [dict(b, result_known_utc=iso(T0 + timedelta(days=5)), match_result_first_seen_utc=iso(T0 + timedelta(days=5)))
                if b['match_id'] == 'A0-d1' else b for b in signals]                         # ganhou, mas só se soube 4 dias depois
        with patch.multiple(llm_eval, **FAST):
            self.assertEqual(run(late, registry, as_of=T0 + timedelta(days=10))['primary']['path'][0]['x'], -1.0)

    def test_admission_never_depends_on_the_result(self):
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=10)
        hidden = [self.without_result(b) if b['league'].startswith('A') else b for b in signals]
        with patch.multiple(llm_eval, **FAST):
            a, b = run(signals, registry), run(hidden, registry)
        self.assertEqual([h['protocol_games'] for h in a['hypotheses']], [h['protocol_games'] for h in b['hypotheses']])
        self.assertEqual(a['primary']['games_test'], b['primary']['games_test'])

    @staticmethod
    def null_units(rng, days, odds_llm, odds_ctrl, n_llm=8, n_ctrl=12, day_sd=0.15, adaptive=False):
        """Sem utilidade incremental (mesma esperança de lucro por jogo), com efeito comum de dia. adaptive=True: a composição de
        cada dia depende dos resultados do dia ANTERIOR (seleção adaptativa coberta pela garantia)."""
        test, ctrl, last = {}, {}, 0.0
        for d in range(days):
            day, shift = (T0 + timedelta(days=d)).date().isoformat(), rng.gauss(0, day_sd)
            nl = (2 if last > 0 else 15) if adaptive else max(1, int(rng.expovariate(1 / n_llm)))
            total = 0.0
            for side, n, out, odds in (('L', nl, test, odds_llm), ('C', max(1, int(rng.expovariate(1 / n_ctrl))), ctrl, odds_ctrl)):
                for k in range(n):
                    o = rng.choice(odds)
                    top = o * 0.98 - 1
                    v = top if rng.random() < min(0.97, max(0.01, (1 + 2 * shift) / o)) else -1.0
                    if rng.random() < 0.05:
                        v = None                                                                # resultado em falta
                    out['%s%d-%d' % (side, d, k)] = (day, v, T0 + timedelta(days=d, hours=12), top)
                    total += (v or 0.0) * (1 if side == 'L' else -1)
            last = total
        return [(1.0, test)], [(1.0, ctrl)]

    def test_eprocess_controls_type_one_error_under_adversarial_nulls(self):
        rng, sims, days = __import__('random').Random(17), 150, 60
        clock = T0 + timedelta(days=days + 10)
        scenarios = [((1.5, 1.8, 2.0, 2.5, 3.5, 5.0), (1.5, 1.8, 2.0, 2.5, 3.5, 5.0), False),
                     ((1.4, 1.6, 1.8), (6.0, 8.0, 10.0), False),                                # a assimetria que derrubou o v3
                     ((6.0, 8.0, 10.0), (1.4, 1.6, 1.8), False),
                     ((1.4, 1.6, 1.8), (6.0, 8.0, 10.0), True)]                                 # e seleção adaptativa entre dias
        with patch.multiple(llm_eval, BOOT_REPLICATES=9):
            for odds_l, odds_c, adaptive in scenarios:
                hits = 0
                for _ in range(sims):
                    res = llm_eval.eprocess(*self.null_units(rng, days, odds_l, odds_c, adaptive=adaptive), clock, None)
                    self.assertTrue(all(row['x'] >= -1 and 0 <= row['lambda'] <= 0.5 for row in res['path']))   # hipóteses do teorema
                    hits += res['k_sup'] >= 20
                self.assertLessEqual(hits / sims, 0.05, (odds_l, odds_c, adaptive))

    def test_a_sel_boundary_within_day_outcome_dependent_composition(self):
        """Fronteira declarada (§6-A): se a composição do dia dependesse dos resultados desse mesmo dia (parar quando se
        ganha), a média diária fica positiva com apostas justas. H0 deixa de valer e o teste rejeita: por isso A-sel é declarada."""
        rng, sims, days = __import__('random').Random(23), 60, 60
        clock, hits = T0 + timedelta(days=days + 10), 0
        with patch.multiple(llm_eval, BOOT_REPLICATES=9):
            for _ in range(sims):
                test, ctrl = {}, {}
                for d in range(days):
                    day = (T0 + timedelta(days=d)).date().isoformat()
                    k = 0
                    while k < 10:                                    # LLM: pára no primeiro ganho do dia (composição pelo resultado)
                        win = rng.random() < 0.49
                        test['L%d-%d' % (d, k)] = (day, 0.96 if win else -1.0, T0, 0.96)
                        k += 1
                        if win:
                            break
                    for k in range(10):
                        ctrl['C%d-%d' % (d, k)] = (day, 0.96 if rng.random() < 0.49 else -1.0, T0, 0.96)
                hits += llm_eval.eprocess([(1.0, test)], [(1.0, ctrl)], clock, None)['k_sup'] >= 20
        self.assertGreater(hits / sims, 0.05)


if __name__ == '__main__':
    unittest.main()

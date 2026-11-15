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
    as_of = as_of or T0 + timedelta(days=30)
    born_eff = born or {hid: brain.chronology.born(h) for hid, h in registry.items()}
    return llm_eval.evaluate(signals, registry, as_of=as_of, born_eff=born_eff, protocol_time=protocol, problems=list(problems))


FAST = dict(REPLICATES=500, MIN_REPLICATES=450)


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
        self.assertEqual(res['population']['excluded'], {'no_proven_birth': 1, 'born_after_as_of': 1})
        self.assertEqual(res['population']['evaluated'], 1)


class HitRateIsNotEvidence(unittest.TestCase):
    def test_T5_T6_high_hit_rate_with_the_same_profit_as_the_controls_is_not_utility(self):
        sixty = [1.0, 1.0, 1.0, -1.0, -1.0]                     # 60 % de acerto à odd 1,50: perde dinheiro (necessário 68 %)
        signals, registry = world(sixty, sixty, llm_odds=1.5, ctrl_odds=1.5)
        res = run(signals, registry)
        self.assertEqual(res['primary']['d'], 0.0)
        self.assertEqual(res['verdict'], 'sem utilidade incremental demonstrada')
        self.assertTrue(all(h['protocol_mean_profit'] < 0 for h in res['hypotheses']))
        seventy = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0]
        res = run(*world(seventy, seventy))
        self.assertEqual((res['primary']['d'], res['verdict']), (0.0, 'sem utilidade incremental demonstrada'))

    def test_no_criterion_reads_hit_rates(self):
        # mesma evidência de lucro, acertos descritivos diferentes: o veredicto e a comparação não mudam
        signals, registry = world([1.0, -1.0], [1.0, -1.0], days=16)
        with patch.multiple(llm_eval, **FAST):
            a = run(signals, registry)
        tampered = [dict(b, w=0.5) if b['w'] == 1.0 and b['league'].startswith('A') else b for b in signals]   # meia vitória: menos lucro
        with patch.multiple(llm_eval, **FAST):
            b = run(tampered, registry)
        self.assertLess(b['primary']['d'], a['primary']['d'])
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
        self.assertEqual((s4['games_test'], s4['games_control'], s4['d']), (16, 16, 0.0))   # a refinada não herda crédito da mãe
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
        self.assertTrue(res['hypotheses'][0]['protocol_mean_profit'] < 0)                   # "acertou abaixo do necessário"...
        self.assertEqual(s5['d'], 0.0)                                                       # ...mas igual à família: nada
        self.assertFalse(s5['significant_holm'])
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
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0])
        res = run(signals, registry)
        self.assertEqual(res['insufficient'], [])
        self.assertLess(res['primary']['p'], 0.05)
        self.assertTrue(res['verdict'].startswith('utilidade incremental estatisticamente significativa'))
        self.assertIn('não é prova de vantagem', res['verdict'])
        unproven = run(signals, registry, problems=['history unavailable: shallow repository'])
        self.assertEqual((unproven['verdict'], unproven['verified']), ('não verificada', False))

    def test_T18_small_or_concentrated_samples_are_insufficient(self):
        with patch.multiple(llm_eval, **FAST):
            small = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=5))
            self.assertEqual(small['verdict'], 'evidência insuficiente')
            # 10 grupos x 24 jogos, mas tudo em 6 dias (4 jogos por dia e liga): falta diversidade temporal
            signals, registry = world([1.0], [1.0], n_llm=10, n_ctrl=10, days=0)
            for i in range(10):
                for lg, out in (('A%d' % i, [1.0, 1.0, 1.0, -1.0]), ('B%d' % i, [1.0, -1.0])):
                    signals += [sig('%s-x%d' % (lg, k), out[k % len(out)], (k // 4 + 1) * DAY + (i * 4 + k % 4) * 20 + (300 if lg[0] == 'B' else 0), league=lg) for k in range(24)]
            dense = run(signals, registry)
        self.assertEqual(dense['primary']['games_test'], 240)
        self.assertEqual(dense['verdict'], 'evidência insuficiente')
        self.assertIn('days with llm games < 14', dense['insufficient'])

    def test_each_minimum_fails_on_its_own(self):
        with patch.multiple(llm_eval, **FAST):
            few = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=45, n_llm=5), as_of=T0 + timedelta(days=50))               # 225 jogos em 45 dias, mas só 5 grupos
            signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=14, n_ctrl=15)      # 14 dias, 10 grupos, 140 + 75 jogos LLM...
            signals += [sig('A%d-burst%d' % (i, k), 1.0, DAY + 600 + k * 15 + i, league='A%d' % i) for i in range(5) for k in range(15)]
            burst = run(signals, registry)                                                         # ...com 75 + 10 no 1.º dia (40 %)
            short = run(*world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=15, n_ctrl=15))              # 150 jogos LLM em 15 dias, 10 grupos
        self.assertEqual(few['insufficient'], ['groups with >= 10 games < 10'])
        self.assertEqual(burst['insufficient'], ['one day holds more than 20 % of llm games'])
        self.assertEqual(short['insufficient'], ['llm games < 200'])
        self.assertEqual({few['verdict'], burst['verdict']}, {'evidência insuficiente'})

    def test_pre_protocol_evidence_never_supports_conclusions(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=21)
        with patch.multiple(llm_eval, **FAST):
            res = run(signals, registry, protocol=T0 + timedelta(days=40))                   # tudo anterior ao protocolo
        self.assertEqual((res['primary']['games_test'], res['verdict']), (0, 'evidência insuficiente'))
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


class GitProofs(unittest.TestCase):
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
        # o protocolo v1 (commit 9f7b2aa) deixou de ser aceite
        v1 = (Path(__file__).resolve().parents[1] / 'audits/pr4-llm-protocol.md').read_bytes()
        old = self.commit('audits/old.md', v1.decode('utf-8'), '2026-11-20T11:00:00+00:00')
        self.assertNotEqual(hashlib.sha256(v1).hexdigest(), llm_eval.PROTOCOL_SHA256)

    def test_P2C_data_between_commit_and_publication_is_not_prospective(self):
        signals, registry = world([1.0, 1.0, 1.0, -1.0], [1.0, -1.0], days=21)
        with patch.multiple(llm_eval, **FAST):
            at_commit = run(signals, registry, protocol=T0)
            at_publication = run(signals, registry, protocol=T0 + timedelta(days=10, hours=12))   # publicada 10,5 dias depois do commit
        self.assertEqual(at_commit['primary']['games_test'], 210)
        self.assertEqual(at_publication['primary']['games_test'], 110)                   # só os 11 dias depois da publicação
        self.assertEqual(sum(h['pre_protocol_games'] for h in at_publication['hypotheses']), 100)


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


if __name__ == '__main__':
    unittest.main()

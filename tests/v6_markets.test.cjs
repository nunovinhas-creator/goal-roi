// Matriz de compatibilidade do motor de mercados V6 (node --test tests/v6_markets.test.cjs).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../docs/v6/v6-markets.js');

const TEAMS = ['Sporting', 'Benfica'];
const T0 = '2026-11-25T18:00:00+00:00';
const at = min => new Date(Date.parse(T0) + min * 60000).toISOString();
const sig = (k, market, selection, extra = {}) => Object.assign({k, market, selection, d: T0, phase: 'pre'}, extra);
const rel = (a, b) => M.relation(M.parse(a, TEAMS), M.parse(b, TEAMS)).relation;
const cat = (a, b) => M.classify(a, b, TEAMS).category;
const S = 'STAKE_LOAD', P = 'POLYMARKET', B = 'BETFAIR';

// Matriz: [A, B, relação esperada]. Cobre todos os mercados suportados, linhas meias, inteiras e de quarto.
const MATRIX = [
  // totais
  [['Asian Total', 'Over 1.5'], ['Asian Total', 'Under 3.5'], 'compativeis'],
  [['Asian Total', 'Over 2.5'], ['Asian Total', 'Under 2.5'], 'exclusivas'],
  [['Asian Total', 'Over 2.25'], ['Asian Total', 'Under 2.25'], 'exclusivas'],
  [['Asian Total', 'Over 2'], ['Asian Total', 'Under 2'], 'exclusivas'],
  [['Asian Total', 'Over 2.5'], ['Asian Total', 'Over 2.5'], 'equivalentes'],
  [['Asian Total', 'Over 2.5'], ['Asian Total', 'Over 2.75'], 'compativeis'],
  [['Asian Total', 'Over 3.5'], ['Asian Total', 'Under 4.5'], 'compativeis'],
  [['Asian Total', 'Over 4.5'], ['Asian Total', 'Under 3.5'], 'exclusivas'],
  [['Asian Total', 'Under 0.5'], ['1x2', 'Sporting'], 'exclusivas'],
  [['Asian Total', 'Over 2.5'], ['1x2', 'Draw'], 'compativeis'],
  // 1x2, dupla hipótese, empate anula, handicap
  [['1x2', 'Sporting'], ['1x2', 'Benfica'], 'exclusivas'],
  [['1x2', 'Sporting'], ['1X2', '1'], 'equivalentes'],
  [['1X2', 'X'], ['Polymarket', 'X'], 'equivalentes'],
  [['1x2', 'Sporting'], ['Polymarket', 'AH1(-0.5)'], 'equivalentes'],
  [['1x2', 'Benfica'], ['Polymarket', 'AH1(+0.5)'], 'exclusivas'],
  [['Polymarket', 'AH1(+0.5)'], ['Polymarket', 'AH2(+0.5)'], 'compativeis'],
  [['Polymarket', 'AH1(-0.5)'], ['Polymarket', 'AH2(+0.5)'], 'exclusivas'],
  [['Double Chance', 'Draw or Benfica'], ['Polymarket', 'AH2(+0.5)'], 'equivalentes'],
  [['Double Chance', 'Draw or Benfica'], ['1x2', 'Sporting'], 'exclusivas'],
  [['Draw No Bet', 'Sporting'], ['Asian Handicap', 'Sporting (0)'], 'equivalentes'],
  [['Draw No Bet', 'Sporting'], ['Draw No Bet', 'Benfica'], 'exclusivas'],
  [['Asian Handicap', 'Sporting (-1)'], ['Asian Handicap', 'Benfica (+1)'], 'exclusivas'],
  [['Asian Handicap', 'Sporting (-0.75)'], ['Asian Handicap', 'Benfica (+0.75)'], 'exclusivas'],
  [['Asian Handicap', 'Sporting (-1.5)'], ['1x2', 'Sporting'], 'compativeis'],
  [['Asian Handicap', 'Sporting (-1.25)'], ['Polymarket', 'AH2(+1.5)'], 'exclusivas'],
  [['Asian Handicap', 'Sporting (+1)'], ['Asian Total', 'Under 1.5'], 'compativeis'],
  [['Polymarket', 'AH2(-2.5)'], ['Asian Handicap', 'Benfica (-2.5)'], 'equivalentes'],
  // fora do resultado final em golos ou não suportados
  [['1st Half - Asian Total', 'Over 1.5'], ['Asian Total', 'Under 0.5'], 'nao_comparaveis'],
  [['Total Bookings', 'Under 3.5'], ['Asian Total', 'Over 3.5'], 'nao_comparaveis'],
  [['Liverpool Total Corners', 'Over 5.5'], ['Asian Total', 'Over 5.5'], 'nao_comparaveis'],
  [['Paris Saint Germain Total', 'Over 1.5'], ['Asian Total', 'Under 1.5'], 'nao_comparaveis'],
  [['1x2 (2up)', 'Sporting'], ['1x2', 'Sporting'], 'nao_comparaveis'],
  [['2nd Team to Score', 'Sporting'], ['1x2', 'Sporting'], 'nao_comparaveis'],
  [['Sporting to Win Both Halves', 'Yes'], ['1x2', 'Benfica'], 'nao_comparaveis'],
  [['Both Teams Score', 'Yes'], ['1x2', 'Benfica'], 'nao_comparaveis'],
  // ambas marcam (como parser.settle)
  [['Both Teams To Score', 'Yes'], ['Asian Total', 'Under 1.5'], 'exclusivas'],
  [['Both Teams To Score', 'No'], ['1x2', 'Draw'], 'compativeis'],
  [['Both Teams To Score', 'Yes'], ['Both Teams To Score', 'No'], 'exclusivas'],
  // paridade com parser.py: "Empate" não é aceite; dupla hipótese só com " or "
  [['1x2', 'Empate'], ['1x2', 'Sporting'], 'insuficiente'],
  [['Double Chance', 'Draw OR Benfica'], ['1x2', 'Sporting'], 'insuficiente'],
  // na dúvida, insuficiente
  [['1x2', 'Sporting CP'], ['1x2', 'Benfica'], 'insuficiente'],
  [['Asian Handicap', 'Spor (-1)'], ['1x2', 'Sporting'], 'compativeis'],
  [['Asian Total', 'Over 2.3'], ['Asian Total', 'Under 2.5'], 'insuficiente'],
  [['Asian Total', 'Over'], ['Asian Total', 'Under 2.5'], 'insuficiente'],
  [['Polymarket', 'None'], ['1x2', 'Sporting'], 'insuficiente'],
  [['', 'Over 2.5'], ['1x2', 'Sporting'], 'insuficiente']
];

test('matriz de compatibilidade (todos os mercados suportados)', () => {
  for (const [a, b, want] of MATRIX) {
    assert.equal(rel(sig(S, ...a), sig(P, ...b)), want, a.join(' ') + ' × ' + b.join(' '));
    assert.equal(rel(sig(P, ...b), sig(S, ...a)), want, 'simetria: ' + b.join(' ') + ' × ' + a.join(' '));
  }
});

test('Over 1,5 e Under 3,5 não são contradição; Over 2,5 e Under 2,5 entre fontes são', () => {
  assert.equal(cat(sig(S, 'Asian Total', 'Over 1.5'), sig(P, 'Asian Total', 'Under 3.5')), 'compativeis');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5'), sig(P, 'Asian Total', 'Under 2.5')), 'exclusivas_entre_fontes');
});

test('seleções opostas da mesma fonte não são contradição entre fontes', () => {
  assert.equal(cat(sig(P, 'Asian Total', 'Over 2.5'), sig(P, 'Asian Total', 'Under 2.5', {d: at(30)})), 'exclusivas_mesma_fonte');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5'), sig(S, 'Asian Total', 'Over 2.5', {d: at(30)})), 'repeticao');
});

test('convergência só entre fontes independentes e contemporâneas', () => {
  assert.equal(cat(sig(B, '1X2', '1'), sig(P, 'Polymarket', 'AH1(-0.5)', {d: at(60)})), 'convergencia');
  // equivalentes mas afastadas no tempo ou em fases diferentes: concordância não contemporânea (nunca "divergência")
  assert.equal(cat(sig(B, '1X2', '1'), sig(P, 'Polymarket', 'AH1(-0.5)', {d: at(8 * 60)})), 'concordancia_nao_contemporanea');
  assert.equal(cat(sig(B, '1X2', '1'), sig(P, '1x2', 'Sporting', {phase: 'live', score_at_signal: [0, 0]})), 'concordancia_nao_contemporanea');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5'), sig(P, 'Asian Total', 'Over 2.5', {d: at(10 * 60)})), 'concordancia_nao_contemporanea');
  // exclusivas afastadas no tempo: divergência temporal
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5'), sig(P, 'Asian Total', 'Under 2.5', {d: at(10 * 60)})), 'divergencia_temporal');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5', {phase: 'live', score_at_signal: [1, 0]}), sig(P, 'Asian Total', 'Under 2.5', {phase: 'live', score_at_signal: [1, 0], d: at(25)})), 'divergencia_temporal');
  // sinais analíticos do canal não são fonte de mercado independente
  assert.equal(cat(sig('OPEN_GAME', 'Asian Total', 'Over 3.5'), sig(S, 'Asian Total', 'Under 3.5')), 'insuficiente');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5', {d: 'lixo'}), sig(P, 'Asian Total', 'Under 2.5')), 'insuficiente');
  assert.equal(cat(sig(S, 'Asian Total', 'Over 2.5', {phase: null}), sig(P, 'Asian Total', 'Under 2.5')), 'insuficiente');
});

test('liquidações asiáticas: quartos, devoluções e meias', () => {
  assert.equal(M.quarter(0, -0.25), -0.5);   // linha -0,25 em empate: meia derrota
  assert.equal(M.quarter(1, -0.75), 0.5);    // linha -0,75 com vitória por 1: meia vitória
  assert.equal(M.quarter(1, -1), 0);         // linha -1 com vitória por 1: devolução
  const over2 = M.parse(sig(S, 'Asian Total', 'Over 2'), TEAMS), under2 = M.parse(sig(P, 'Asian Total', 'Under 2'), TEAMS);
  assert.equal(over2.rule(1, 1), 0); assert.equal(under2.rule(1, 1), 0);
  const r = M.relation(over2, under2);
  assert.equal(r.relation, 'exclusivas'); assert.equal(r.sharedPush, true);
  const o225 = M.parse(sig(S, 'Asian Total', 'Over 2.25'), TEAMS);
  assert.equal(o225.rule(1, 1), -0.5); assert.equal(o225.rule(2, 1), 1);
});

test('ao vivo: marcador restringe resultados; handicap ao vivo exige convenção e marcador', () => {
  // A 3-0, Over 2,5 já está ganho: uma seleção já decidida pelo marcador não sustenta nenhuma relação
  const o = sig(S, 'Asian Total', 'Over 2.5', {phase: 'live', score_at_signal: [3, 0]});
  assert.equal(rel(o, sig(P, '1x2', 'Sporting', {phase: 'live', score_at_signal: [3, 0]})), 'insuficiente');
  // A 1-0, Over 0,5 ganho; a 2-0, Over 1,5 ganho: ambas decididas, nunca "convergência"
  assert.equal(cat(sig(S, 'Asian Total', 'Over 0.5', {phase: 'live', score_at_signal: [1, 0]}),
    sig(P, 'Asian Total', 'Over 1.5', {phase: 'live', score_at_signal: [2, 0], d: at(5)})), 'insuficiente');
  // Uma seleção ainda em aberto continua comparável
  assert.equal(rel(sig(S, 'Asian Total', 'Over 3.5', {phase: 'live', score_at_signal: [2, 0]}), sig(P, '1x2', 'Sporting', {phase: 'live', score_at_signal: [2, 0]})), 'compativeis');
  // Handicap ao vivo da Stake conta a partir do marcador: Sporting (-0.5) a 1-0 precisa de mais um golo de margem
  const ah = M.parse(sig(S, 'Asian Handicap', 'Sporting (-0.5)', {phase: 'live', score_at_signal: [1, 0]}), TEAMS);
  assert.equal(ah.rule(1, 0), -1); assert.equal(ah.rule(2, 0), 1);
  assert.equal(rel(sig(S, 'Asian Handicap', 'Sporting (-0.5)', {phase: 'live'}), sig(P, '1x2', 'Sporting')), 'insuficiente');
  assert.equal(rel(sig(P, 'Polymarket', 'AH1(-0.5)', {phase: 'live', score_at_signal: [0, 0]}), sig(S, '1x2', 'Sporting')), 'insuficiente');
  // marcadores impossíveis na mesma sequência de golos
  assert.equal(rel(sig(S, 'Asian Total', 'Over 2.5', {phase: 'live', score_at_signal: [2, 0]}), sig(P, 'Asian Total', 'Over 2.5', {phase: 'live', score_at_signal: [0, 1]})), 'insuficiente');
});

test('linhas fora do intervalo de resultados avaliado não produzem relações', () => {
  assert.equal(rel(sig(S, 'Asian Handicap', 'Sporting (+13)'), sig(P, 'Polymarket', 'AH2(+14)')), 'insuficiente');
  assert.equal(rel(sig(S, 'Asian Total', 'Over 11.5'), sig(P, 'Asian Total', 'Under 11.5')), 'insuficiente');
  assert.equal(rel(sig(S, 'Asian Handicap', 'Sporting (-6)'), sig(P, 'Polymarket', 'AH2(+6)')), 'exclusivas');
});

test('identidade do jogo e análise agregada', () => {
  assert.equal(M.gameIdentity('x', {teams: ['A', 'B']}).ok, true);
  for (const m of [{}, {teams: ['A']}, {teams: ['A', 'a']}, {teams: ['A', '']}]) assert.equal(M.gameIdentity('x', m).ok, false);
  assert.equal(M.gameIdentity('', {teams: ['A', 'B']}).ok, false);
  const ev = [sig(S, 'Asian Total', 'Over 1.5'), sig(P, 'Asian Total', 'Under 3.5', {d: at(5)}), sig(P, 'Asian Total', 'Under 3.5', {d: at(9)}),
    sig(B, '1X2', '1', {d: at(10)}), sig(S, '1x2', 'Sporting', {d: at(11)}), sig(S, 'Total Bookings', 'Under 3.5')];
  const a = M.analyseGame(ev, TEAMS, {ok: true});
  assert.equal(a.repeated, 1);
  assert.equal(a.counts.convergencia, 1);
  assert.equal(a.counts.exclusivas_entre_fontes, 0);
  assert.equal(a.counts.nao_comparaveis, 4);
  assert.equal(M.headline(a.counts), 'convergencia');
  assert.equal(M.analyseGame(ev, TEAMS, {ok: false, reason: 'x'}).counts.insuficiente, 1);
  assert.equal(M.headline(M.analyseGame([ev[0]], TEAMS, {ok: true}).counts), null);
});

test('resultados extremos e propriedades: simetria e reflexividade em toda a matriz', () => {
  const all = MATRIX.flatMap(([a, b]) => [a, b]).map(x => sig(S, ...x));
  for (const x of all) {
    const r = rel(x, Object.assign({}, x, {k: P}));
    const parsed = M.parse(x, TEAMS);
    assert.equal(r, parsed.kind === 'ok' ? 'equivalentes' : parsed.kind === 'nao_comparavel' ? 'nao_comparaveis' : 'insuficiente', x.market + ' ' + x.selection);
  }
});

/* Goal-ROI Intelligence V6 — motor de compatibilidade entre sinais de mercado (sem DOM, sem dependências).
 *
 * Cada seleção é convertida numa função de liquidação sobre o resultado final em 90 minutos, com as regras de
 * parser.py (settle, _quarter, _side, selection_1x2): 1 ganha, 0,5 meia vitória, 0 devolução, −0,5 meia
 * derrota, −1 perde. Duas seleções são comparadas em todos os resultados finais possíveis (0–12 golos por equipa,
 * restringidos ao marcador conhecido no momento de cada sinal ao vivo). Nada é inferido por semelhança de nomes:
 * na dúvida, a relação é "evidência insuficiente".
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.V6Markets = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const MAX_GOALS = 12;
  const WINDOW_MIN = {pre: 6 * 60, live: 10}; // sinais mais afastados do que isto não são contemporâneos
  // Plataformas de mercado distintas. Todas chegam pelo mesmo canal de recolha: a independência é de plataforma
  // (preço e volume próprios), não de canal. Sinais analíticos do próprio canal não são uma fonte de mercado.
  const MARKET_SOURCES = {BETFAIR: 'Betfair', POLYMARKET: 'Polymarket', STAKE_LOAD: 'Stake'};
  const CATEGORIES = {
    convergencia: 'Convergência entre fontes independentes',
    repeticao: 'Repetição na mesma fonte',
    exclusivas_entre_fontes: 'Seleções mutuamente exclusivas entre fontes',
    exclusivas_mesma_fonte: 'Seleções opostas na mesma fonte',
    compativeis: 'Mercados compatíveis',
    nao_comparaveis: 'Mercados não comparáveis',
    divergencia_temporal: 'Divergência temporal',
    insuficiente: 'Evidência insuficiente'
  };

  // ---------- regras de liquidação (espelho de parser.py) ----------
  function asianLine(token) {
    const line = Number(token);
    return Number.isFinite(line) && Number.isInteger(line * 4) ? line : null;
  }
  function quarter(diff, line) {
    const parts = Number.isInteger(line * 2) ? [line] : [line - 0.25, line + 0.25];
    return parts.reduce((s, p) => s + (diff + p > 0 ? 1 : diff + p < 0 ? -1 : 0), 0) / parts.length;
  }
  function side(token, teams) { // _side: "Draw" -> X; equipa por igualdade, prefixo ou contido, sem ambiguidade
    const t = String(token || '').trim().toLowerCase();
    if (!t) return null;
    if (t === 'draw') return 'X';
    const names = Array.isArray(teams) ? teams : [];
    const exact = ['1', '2'].filter((k, i) => names[i] && String(names[i]).toLowerCase() === t);
    if (exact.length === 1) return exact[0];
    const hits = ['1', '2'].filter((k, i) => names[i] && (String(names[i]).toLowerCase().startsWith(t) || String(names[i]).toLowerCase().includes(t)));
    return hits.length === 1 ? hits[0] : null;
  }
  function side1x2(selection, teams) { // selection_1x2: 1/X/2, "Draw" ou nome exato e único
    const token = String(selection || '').trim();
    if (['1', 'X', '2'].includes(token.toUpperCase())) return token.toUpperCase();
    if (token.toLowerCase() === 'draw' || token.toLowerCase() === 'empate') return 'X';
    if (!Array.isArray(teams) || teams.length !== 2 || teams.some(t => typeof t !== 'string' || !t.trim())) return null;
    const hits = ['1', '2'].filter((k, i) => teams[i].trim().toLowerCase() === token.toLowerCase());
    return hits.length === 1 ? hits[0] : null;
  }

  // Converte um sinal numa seleção canónica. Devolve {kind:'ok'|'nao_comparavel'|'insuficiente', ...}.
  function parse(signal, teams) {
    const market = String(signal.market || '').trim(), mk = market.toLowerCase();
    const sel = String(signal.selection === null || signal.selection === undefined ? '' : signal.selection).trim();
    const live = signal.phase === 'live';
    const score = Array.isArray(signal.score_at_signal) && signal.score_at_signal.length === 2 &&
      signal.score_at_signal.every(g => Number.isInteger(g) && g >= 0) ? signal.score_at_signal.slice() : null;
    const base = {market, selection: sel, phase: signal.phase || null, score, live};
    const no = reason => Object.assign(base, {kind: 'nao_comparavel', reason});
    const weak = reason => Object.assign(base, {kind: 'insuficiente', reason});
    if (!market || !sel || sel === 'None') return weak('mercado ou seleção em falta');
    if (/^1st half|^2nd half|half -|corners|bookings|cards|both halves|to score|exact|\(2up\)|team total/i.test(market) ||
        (/ total$/i.test(market) && mk !== 'asian total')) return no('mercado fora do resultado final em golos (' + market + ')');
    let rule = null, key = null;
    if (mk === 'asian total' || (mk === 'polymarket' && /^(over|under)\s/i.test(sel))) {
      const m = sel.match(/^(over|under)\s+([0-9]+(?:\.[0-9]+)?)$/i);
      const line = m ? asianLine(m[2]) : null;
      if (line === null) return weak('linha de golos ilegível');
      const over = m[1].toLowerCase() === 'over';
      rule = (h, a) => (over ? quarter(h + a, -line) : quarter(-(h + a), line));
      key = 'total|' + (over ? 'over' : 'under') + '|' + line;
    } else if (mk === '1x2' || (mk === 'polymarket' && /^(1|2|x|draw)$/i.test(sel))) {
      const s = side1x2(sel, teams);
      if (!s) return weak('equipa da seleção sem correspondência exata');
      rule = (h, a) => (s === (h > a ? '1' : a > h ? '2' : 'X') ? 1 : -1);
      key = '1x2|' + s;
    } else if (mk === 'double chance') {
      const parts = sel.split(/\s+or\s+/i).map(t => side(t, teams));
      if (parts.length !== 2 || parts.includes(null) || parts[0] === parts[1]) return weak('dupla hipótese ilegível');
      rule = (h, a) => (parts.includes(h > a ? '1' : a > h ? '2' : 'X') ? 1 : -1);
      key = 'dc|' + parts.slice().sort().join('');
    } else if (mk === 'draw no bet') {
      const s = side(sel, teams);
      if (s !== '1' && s !== '2') return weak('equipa da seleção ambígua');
      rule = (h, a) => quarter(s === '1' ? h - a : a - h, 0);
      key = 'ah|' + s + '|0';
    } else if (mk === 'asian handicap' || (mk === 'polymarket' && /^AH[12]\(/i.test(sel))) {
      let s, line;
      const pm = sel.match(/^AH([12])\(([+-]?[0-9]+(?:\.[0-9]+)?)\)$/i);
      if (pm) { s = pm[1]; line = asianLine(pm[2]); }
      else {
        const m = sel.match(/^(.+?) \(([+-]?[0-9]+(?:\.[0-9]+)?)\)$/);
        s = m ? side(m[1], teams) : null; line = m ? asianLine(m[2]) : null;
      }
      if ((s !== '1' && s !== '2') || line === null) return weak('handicap ilegível ou equipa ambígua');
      if (live) {
        // Ao vivo, o handicap asiático de casa de apostas conta a partir do marcador no momento da aposta.
        // A convenção da Polymarket ao vivo não está documentada: não se compara.
        if (signal.k !== 'STAKE_LOAD' || !score) return weak('handicap ao vivo sem convenção ou marcador conhecido');
        const [h0, a0] = score;
        rule = (h, a) => quarter(s === '1' ? (h - h0) - (a - a0) : (a - a0) - (h - h0), line);
        key = 'ahlive|' + s + '|' + line + '|' + h0 + '-' + a0;
      } else {
        rule = (h, a) => quarter(s === '1' ? h - a : a - h, line);
        key = 'ah|' + s + '|' + line;
      }
    } else return no('mercado não suportado (' + market + ')');
    return Object.assign(base, {kind: 'ok', key, rule});
  }

  // Resultados finais possíveis dado o marcador conhecido de cada sinal ao vivo. null = marcadores incompatíveis.
  function reachable(a, b) {
    const floor = [0, 0];
    for (const x of [a, b]) if (x.live && x.score) { floor[0] = Math.max(floor[0], x.score[0]); floor[1] = Math.max(floor[1], x.score[1]); }
    const out = [];
    for (let h = floor[0]; h <= MAX_GOALS; h++) for (let g = floor[1]; g <= MAX_GOALS; g++) out.push([h, g]);
    return out;
  }

  // Relação matemática entre duas seleções já interpretadas.
  function relation(a, b) {
    if (a.kind === 'nao_comparavel' || b.kind === 'nao_comparavel') return {relation: 'nao_comparaveis', reason: (a.kind === 'nao_comparavel' ? a : b).reason};
    if (a.kind !== 'ok' || b.kind !== 'ok') return {relation: 'insuficiente', reason: (a.kind !== 'ok' ? a : b).reason};
    if (a.live && b.live && a.score && b.score &&
        ((a.score[0] < b.score[0] && a.score[1] > b.score[1]) || (a.score[0] > b.score[0] && a.score[1] < b.score[1]))) {
      // marcadores que não podem pertencer à mesma sequência de golos: identidade do jogo duvidosa
      return {relation: 'insuficiente', reason: 'marcadores incompatíveis entre sinais ao vivo'};
    }
    let equal = true, bothWin = false, bothNonLose = false, sharedPush = false;
    for (const [h, g] of reachable(a, b)) {
      const x = a.rule(h, g), y = b.rule(h, g);
      if (x !== y) equal = false;
      if (x > 0 && y > 0) bothWin = true;
      if (x >= 0 && y >= 0) bothNonLose = true;
      if (x === 0 && y === 0) sharedPush = true;
    }
    if (equal) return {relation: 'equivalentes', reason: 'liquidação idêntica em todos os resultados possíveis'};
    if (!bothWin) return {relation: 'exclusivas', reason: bothNonLose ? 'nunca ganham ambas (há resultados em que uma ou ambas devolvem)' : 'nunca ganham ambas',
      sharedPush};
    return {relation: 'compativeis', reason: 'existem resultados em que ambas ganham'};
  }

  const sourceOf = k => MARKET_SOURCES[k] || null;
  const minutes = (x, y) => Math.abs(Date.parse(x) - Date.parse(y)) / 60000;

  // Classificação de um par de sinais (cada um com k, d, phase, market, selection, score_at_signal).
  function classify(sa, sb, teams) {
    const A = parse(sa, teams), B = parse(sb, teams);
    const rel = relation(A, B);
    const srcA = sourceOf(sa.k), srcB = sourceOf(sb.k);
    const out = {relation: rel.relation, reason: rel.reason, a: A.key || A.market + ' · ' + A.selection, b: B.key || B.market + ' · ' + B.selection};
    if (rel.relation === 'nao_comparaveis') return Object.assign(out, {category: 'nao_comparaveis'});
    if (rel.relation === 'insuficiente') return Object.assign(out, {category: 'insuficiente'});
    if (rel.relation === 'compativeis') return Object.assign(out, {category: 'compativeis'});
    if (!srcA || !srcB) return Object.assign(out, {category: 'insuficiente', reason: 'sinal analítico do canal, não é uma fonte de mercado independente'});
    const t1 = Date.parse(sa.d), t2 = Date.parse(sb.d);
    if (!Number.isFinite(t1) || !Number.isFinite(t2)) return Object.assign(out, {category: 'insuficiente', reason: 'hora do sinal desconhecida'});
    if (srcA === srcB) return Object.assign(out, {category: rel.relation === 'equivalentes' ? 'repeticao' : 'exclusivas_mesma_fonte'});
    const phaseA = sa.phase === 'live' ? 'live' : sa.phase === 'pre' ? 'pre' : null, phaseB = sb.phase === 'live' ? 'live' : sb.phase === 'pre' ? 'pre' : null;
    if (!phaseA || !phaseB) return Object.assign(out, {category: 'insuficiente', reason: 'fase do sinal desconhecida'});
    if (phaseA !== phaseB || minutes(sa.d, sb.d) > WINDOW_MIN[phaseA]) {
      return Object.assign(out, {category: 'divergencia_temporal', reason: rel.reason + '; sinais afastados (' + Math.round(minutes(sa.d, sb.d)) + ' min' + (phaseA !== phaseB ? ', antes e durante o jogo' : '') + ')'});
    }
    return Object.assign(out, {category: rel.relation === 'equivalentes' ? 'convergencia' : 'exclusivas_entre_fontes'});
  }

  // Análise de um jogo: compara a observação mais recente de cada seleção distinta (fonte + seleção canónica).
  function analyseGame(events, teams, identity) {
    const counts = Object.fromEntries(Object.keys(CATEGORIES).map(k => [k, 0]));
    if (!identity || !identity.ok) {
      return {identity: identity || {ok: false, reason: 'sem identidade'}, counts: Object.assign(counts, {insuficiente: 1}), pairs: [], repeated: 0};
    }
    const latest = new Map();
    let repeated = 0;
    for (const e of events) {
      const p = parse(e, teams);
      const id = (e.k || '?') + '|' + (p.key || (p.market + '|' + p.selection)) + '|' + (e.phase || '');
      if (latest.has(id)) { repeated++; if (Date.parse(e.d) > Date.parse(latest.get(id).d)) latest.set(id, e); }
      else latest.set(id, e);
    }
    counts.repeticao += repeated;
    const list = [...latest.values()], pairs = [];
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const c = classify(list[i], list[j], teams);
      counts[c.category]++;
      if (c.category !== 'nao_comparaveis') pairs.push(Object.assign(c, {
        a: {source: sourceOf(list[i].k) || 'Canal', selection: list[i].selection, market: list[i].market, d: list[i].d, phase: list[i].phase},
        b: {source: sourceOf(list[j].k) || 'Canal', selection: list[j].selection, market: list[j].market, d: list[j].d, phase: list[j].phase}}));
    }
    return {identity, counts, pairs, repeated};
  }

  // Identidade inequívoca: um identificador de jogo e exatamente duas equipas com nome.
  function gameIdentity(id, m) {
    if (!id || typeof id !== 'string') return {ok: false, reason: 'jogo sem identificador'};
    const t = m && m.teams;
    if (!Array.isArray(t) || t.length !== 2 || t.some(x => typeof x !== 'string' || !x.trim())) return {ok: false, reason: 'equipas desconhecidas'};
    if (t[0].trim().toLowerCase() === t[1].trim().toLowerCase()) return {ok: false, reason: 'equipas iguais'};
    return {ok: true};
  }

  // Resumo de um jogo para a interface: estado dominante, por ordem de gravidade.
  function headline(counts) {
    for (const k of ['exclusivas_entre_fontes', 'convergencia', 'divergencia_temporal', 'exclusivas_mesma_fonte', 'compativeis', 'repeticao', 'insuficiente', 'nao_comparaveis'])
      if (counts[k] > 0) return k;
    return null; // um só sinal: nada a comparar
  }

  return {CATEGORIES, MARKET_SOURCES, WINDOW_MIN, MAX_GOALS, parse, relation, classify, analyseGame, gameIdentity, headline, quarter, side, side1x2};
});

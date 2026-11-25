/* Goal-ROI Intelligence V6 — núcleo de derivação (sem DOM, sem dependências).
 *
 * Usado pelo navegador (window.V6Core) e por scripts/build_dashboard_snapshots.mjs (CommonJS),
 * para que os snapshots derivados e o modo de recurso (report.json completo) produzam exatamente
 * os mesmos números. Nada aqui calcula estatísticas novas de inferência: copia valores publicados
 * pelo relatório e faz apenas contagens, somas e máximos descritivos, sempre com a origem indicada.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.V6Core = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const SCHEMA = 'goal_roi_dashboard_v6_1';
  const MANIFEST_SCHEMA = 'goal_roi_dashboard_manifest_v6_1';
  const PROSPECTIVE_SCHEMA = 'goal_roi_prospective_report_v1';
  const SNAPSHOTS = ['summary', 'signals', 'performance', 'science', 'operations', 'learning'];
  const FILE = name => 'dashboard-' + name + '.json';
  const VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.';
  // Critérios congelados no protocolo prospetivo (scangoal/prospective.py e prospective_protocol.json).
  // Servem apenas para detetar certificações inválidas no relatório recebido; não são recalculados aqui.
  const FROZEN = Object.freeze({min_games: 200, z_crit: 2.576, min_band: 30, missing_after_hours: 72});
  const H1_STATUSES = ['insufficient_games', 'supported_predictive_only', 'not_supported'];
  const H2_STATUSES = ['insufficient_games', 'descriptive_only'];
  const REFERENCE = 'Q7 Todas as apostas (referência)';
  const STALE_MIN = 45, DEAD_MIN = 180;
  const SOURCES = {BETFAIR: 'Betfair', POLYMARKET: 'Polymarket', STAKE_LOAD: 'Stake'};

  // ---------- utilitários ----------
  const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const num = v => typeof v === 'number' && Number.isFinite(v);
  const int = v => Number.isInteger(v) && v >= 0;
  const time = v => (typeof v === 'string' && v ? Date.parse(v) : NaN);
  const round = (v, p = 4) => (num(v) ? Math.round(v * 10 ** p) / 10 ** p : null);
  const sourceOf = type => {
    const t = String(type || '');
    if (t.startsWith('BETFAIR')) return 'Betfair';
    if (t.startsWith('POLYMARKET')) return 'Polymarket';
    if (t.startsWith('STAKE')) return 'Stake';
    return 'Outros';
  };

  // ---------- privacidade ----------
  // Nunca entram nos snapshots nem na interface: amostras de mensagens em bruto, chaves com aparência
  // de credencial e cadeias com aparência de token, e-mail, telefone ou identificador Telegram.
  const PRIVATE_KEY = /(token|secret|password|passwd|api[_-]?key|session|cookie|chat[_-]?id|user[_-]?id|phone|email|unparsed_samples|telegram_raw|^raw$|^text$)/i;
  const PRIVATE_VALUE = [
    /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/,                 // e-mail
    /\b\d{6,}:[A-Za-z0-9_-]{30,}\b/,                 // token de bot Telegram
    /\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/, // token GitHub
    /\b(sk|pk)[-_][A-Za-z0-9]{20,}\b/,               // chaves de API genéricas
    /(^|[^\w])\+\d{9,15}\b/,                         // telefone internacional
    /(^|\s)@[A-Za-z][A-Za-z0-9_]{4,31}\b/            // identificador Telegram
  ];
  const ALLOWED_HANDLES = new Set(['@scangoal_bot', '@cf']);
  function privateString(s) {
    if (typeof s !== 'string') return false;
    for (const re of PRIVATE_VALUE) {
      const m = s.match(re);
      if (m && !ALLOWED_HANDLES.has(m[0].trim().split('/')[0])) return true;
    }
    return false;
  }
  function sanitize(value, path = '') {
    if (Array.isArray(value)) return value.map((v, i) => sanitize(v, path + '[' + i + ']')).filter(v => v !== undefined);
    if (isObj(value)) {
      const out = {};
      for (const [k, v] of Object.entries(value)) {
        if (PRIVATE_KEY.test(k)) continue;
        const clean = sanitize(v, path + '.' + k);
        if (clean !== undefined) out[k] = clean;
      }
      return out;
    }
    if (typeof value === 'string' && privateString(value)) return '[ocultado]';
    return value;
  }
  function privacyFindings(value, path = '$', found = []) {
    if (Array.isArray(value)) value.forEach((v, i) => privacyFindings(v, path + '[' + i + ']', found));
    else if (isObj(value)) for (const [k, v] of Object.entries(value)) {
      if (PRIVATE_KEY.test(k)) found.push(path + '.' + k);
      privacyFindings(v, path + '.' + k, found);
    } else if (privateString(value)) found.push(path);
    return found;
  }

  // ---------- validação ----------
  function validateReport(r) {
    const problems = [];
    if (!isObj(r)) return {ok: false, problems: ['relatório não é um objeto']};
    const gen = time(r.generated_at_utc);
    if (!Number.isFinite(gen)) problems.push('generated_at_utc inválido');
    for (const k of ['unique_bets', 'settled_bets']) if (!int(r[k])) problems.push(k + ' inválido');
    for (const k of ['groups', 'questions', 'audit', 'brain', 'insights']) if (!isObj(r[k])) problems.push(k + ' em falta');
    for (const k of ['timeline', 'daily']) if (!Array.isArray(r[k])) problems.push(k + ' em falta');
    if (int(r.unique_bets) && int(r.settled_bets) && r.settled_bets > r.unique_bets) problems.push('liquidadas > acompanhadas');
    if (isObj(r.groups)) {
      const g = Object.values(r.groups);
      if (g.some(x => !isObj(x) || !int(x.bets) || !int(x.settled))) problems.push('grupo malformado');
      else {
        if (g.reduce((s, x) => s + x.bets, 0) !== r.unique_bets) problems.push('soma dos grupos ≠ apostas acompanhadas');
        if (g.reduce((s, x) => s + x.settled, 0) !== r.settled_bets) problems.push('soma dos grupos ≠ apostas liquidadas');
      }
    }
    const ref = r.questions && r.questions[REFERENCE];
    if (isObj(r.questions) && (!ref || ref.bets !== r.unique_bets || ref.settled !== r.settled_bets)) problems.push('referência Q7 inconsistente');
    if (Number.isFinite(gen) && Array.isArray(r.timeline) && r.timeline.some(p => time(p.d) > gen + 1000)) problems.push('liquidação posterior ao relatório');
    return {ok: problems.length === 0, problems};
  }

  // Valida o relatório prospetivo contra os critérios congelados. Um estado "suportado" com menos de
  // 200 jogos com resultado independente é uma certificação inválida e nunca é apresentado como tal.
  function validateProspective(p) {
    const problems = [], certification = [];
    if (!isObj(p)) return {ok: false, problems: ['relatório prospetivo ausente'], certification};
    if (p.schema !== PROSPECTIVE_SCHEMA) problems.push('esquema prospetivo desconhecido: ' + String(p.schema));
    for (const k of ['ledger_entries', 'games_registered', 'signals_registered', 'signals_registered_before_result',
      'signals_result_known_at_registration', 'games_with_independent_result', 'games_with_conflicting_results',
      'games_without_result_after_72h']) if (!int(p[k])) problems.push(k + ' inválido');
    if (int(p.signals_registered) && int(p.signals_registered_before_result) && int(p.signals_result_known_at_registration) &&
        p.signals_registered_before_result + p.signals_result_known_at_registration !== p.signals_registered) problems.push('contagem de sinais incoerente');
    if (int(p.games_with_independent_result) && int(p.games_registered) && p.games_with_independent_result > p.games_registered) problems.push('resultados independentes > jogos registados');
    const h1 = p.H1, h2 = p.H2;
    if (!isObj(h1) || !isObj(h2) || !isObj(p.D1)) problems.push('H1, H2 ou D1 em falta');
    if (isObj(h1)) {
      if (!H1_STATUSES.includes(h1.status)) problems.push('estado H1 desconhecido');
      if (!int(h1.games_with_result) || !int(h1.games_in_population) || h1.games_with_result > h1.games_in_population) problems.push('amostra H1 incoerente');
      if (h1.status !== 'insufficient_games' && !(h1.games_with_result >= FROZEN.min_games)) certification.push('H1 declara decisão com menos de ' + FROZEN.min_games + ' jogos');
      if (h1.status === 'supported_predictive_only') {
        if (!(num(h1.z) && h1.z >= FROZEN.z_crit)) certification.push('H1 suportada com z abaixo de ' + FROZEN.z_crit);
        const bands = isObj(h1.minute_bands) ? Object.values(h1.minute_bands) : [];
        if (bands.some(b => b.games >= FROZEN.min_band && !(b.observed > b.expected))) certification.push('H1 suportada com faixa de minutos sem excesso');
      }
      if (h1.status === 'insufficient_games' && h1.games_with_result >= FROZEN.min_games) problems.push('H1 com amostra suficiente mas sem decisão');
    }
    if (isObj(h2)) {
      if (!H2_STATUSES.includes(h2.status)) certification.push('H2 com estado fora do protocolo: ' + String(h2.status));
      if (!int(h2.games_settled) || !int(h2.wins) || !int(h2.losses) || h2.wins + h2.losses > h2.games_settled) problems.push('amostra H2 incoerente');
      if (h2.status === 'descriptive_only' && !(h2.games_settled >= FROZEN.min_games)) certification.push('H2 descritiva antes de ' + FROZEN.min_games + ' jogos');
    }
    if (typeof p.verdict !== 'string' || !/NÃO DEMONSTRADA/.test(p.verdict)) certification.push('veredito prospetivo alterado');
    return {ok: problems.length === 0 && certification.length === 0, problems, certification};
  }

  // Certificação das táticas no relatório principal: nunca "validada" sem certificação ativa e registo válido.
  function certificationState(brain) {
    const v = (brain && brain.validation) || {}, sb = (brain && brain.strategy_board) || {};
    const validated = (sb.counts && sb.counts.validada) || 0;
    const active = v.state === 'active' && v.ledger_valid === true && sb.certification_active === true;
    const invalid = [];
    if (validated > 0 && !active) invalid.push(validated + ' estratégia(s) marcadas como validadas sem certificação ativa');
    if (v.state === 'active' && v.ledger_valid !== true) invalid.push('certificação ativa com registo inválido');
    if (sb.statistical_claim && sb.statistical_claim !== 'none' && !active) invalid.push('afirmação estatística sem certificação');
    return {active, validated: active ? validated : 0, state: v.state || 'desconhecido', invalid,
      verdict: sb.verdict || null, criteria_sha256: sb.criteria_sha256 || null,
      executable_price_evidence: sb.executable_price_evidence === true, costs_known: sb.costs_known === true};
  }

  function freshness(collectedAt, now) {
    const t = time(collectedAt);
    if (!Number.isFinite(t)) return {minutes: null, state: 'desconhecido'};
    const minutes = Math.max(0, Math.round((now - t) / 60000));
    return {minutes, state: minutes > DEAD_MIN ? 'parada' : minutes > STALE_MIN ? 'atrasada' : 'normal'};
  }

  // ---------- sinais e mercados ----------
  const signalKey = s => [s.match_id, s.type, s.market, s.selection, s.odds, s.date_utc].join('|');
  function dedupe(items) {
    const seen = new Map();
    for (const it of items || []) {
      const k = signalKey(it);
      if (seen.has(k)) seen.get(k).duplicates++;
      else seen.set(k, Object.assign({}, it, {duplicates: 0}));
    }
    return [...seen.values()];
  }
  function groupByGame(items) {
    const games = new Map();
    for (const it of dedupe(items)) {
      const id = it.match_id || it.match || '?';
      if (!games.has(id)) games.set(id, {match_id: id, match: it.match, league: it.league || null, kickoff_utc: it.kickoff_utc || null,
        signals: [], sources: [], verdicts: {}, conflict: false, duplicates: 0, last_utc: null});
      const g = games.get(id);
      g.signals.push(it);
      g.duplicates += it.duplicates;
      const src = sourceOf(it.type);
      if (!g.sources.includes(src)) g.sources.push(src);
      g.verdicts[it.verdict] = (g.verdicts[it.verdict] || 0) + 1;
      g.conflict = g.conflict || it.conflict === true;
      if (!g.last_utc || time(it.date_utc) > time(g.last_utc)) g.last_utc = it.date_utc;
    }
    for (const g of games.values()) g.signals.sort((a, b) => time(a.date_utc) - time(b.date_utc));
    return [...games.values()].sort((a, b) => time(b.last_utc) - time(a.last_utc));
  }

  // Lado de uma seleção, para detetar convergência/contradição entre fontes no mesmo jogo.
  function side(selection, teams) {
    const s = String(selection || '').toLowerCase();
    if (/^over\b|\bover\b/.test(s)) return 'over';
    if (/^under\b|\bunder\b/.test(s)) return 'under';
    if (/^ah1|^1\b|^w1/.test(s)) return 'home';
    if (/^ah2|^2\b|^w2/.test(s)) return 'away';
    if (Array.isArray(teams)) {
      const norm = x => String(x || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      const sel = norm(s.replace(/\(.*\)/, ''));
      if (sel && norm(teams[0]).startsWith(sel.slice(0, 5))) return 'home';
      if (sel && norm(teams[1]).startsWith(sel.slice(0, 5))) return 'away';
    }
    return null;
  }
  const OPPOSITE = {over: 'under', under: 'over', home: 'away', away: 'home'};

  function marketTimeline(matches) {
    const out = [];
    for (const [id, m] of Object.entries(matches || {})) {
      const events = (m.events || []).filter(e => Number.isFinite(time(e.d))).slice().sort((a, b) => time(a.d) - time(b.d));
      const bySel = new Map(), sources = {}, sides = {};
      for (const e of events) {
        const src = SOURCES[e.k] || sourceOf(e.k);
        sources[src] = (sources[src] || 0) + 1;
        const key = src + '|' + e.market + '|' + e.selection;
        if (!bySel.has(key)) bySel.set(key, {source: src, market: e.market, selection: e.selection, phase: e.phase,
          first_utc: e.d, first_odds: num(e.odds) ? e.odds : null, last_utc: e.d, last_odds: num(e.odds) ? e.odds : null,
          observations: 0, volume: 0, max_mult: 1});
        const s = bySel.get(key);
        s.observations++;
        s.last_utc = e.d;
        if (num(e.odds)) s.last_odds = e.odds;
        if (num(e.total)) s.volume += e.total;
        if (num(e.mult) && e.mult > s.max_mult) s.max_mult = e.mult;
        const sd = side(e.selection, m.teams);
        if (sd) (sides[sd] = sides[sd] || new Set()).add(src);
      }
      const contradictions = Object.keys(sides).filter(sd => sides[OPPOSITE[sd]] && sd < OPPOSITE[sd])
        .map(sd => ({a: sd, b: OPPOSITE[sd], sources_a: [...sides[sd]], sources_b: [...sides[OPPOSITE[sd]]]}));
      const convergences = Object.entries(sides).filter(([, set]) => set.size > 1).map(([sd, set]) => ({side: sd, sources: [...set]}));
      out.push({match_id: id, teams: m.teams || null, events: events.length,
        first_utc: events.length ? events[0].d : null, last_utc: events.length ? events[events.length - 1].d : null,
        sources, selections: [...bySel.values()].map(s => Object.assign(s, {volume: round(s.volume, 0),
          move_pct: num(s.first_odds) && num(s.last_odds) && s.first_odds > 0 ? round((s.last_odds / s.first_odds - 1) * 100, 1) : null})),
        convergences, contradictions,
        series: events.map(e => ({d: e.d, source: SOURCES[e.k] || sourceOf(e.k), market: e.market, selection: e.selection,
          odds: num(e.odds) ? e.odds : null, total: num(e.total) ? e.total : null, share: num(e.share) ? e.share : null, phase: e.phase || null}))});
    }
    return out.sort((a, b) => time(b.last_utc) - time(a.last_utc));
  }

  // Mapa de calor hora de Lisboa × fonte, a partir das apostas publicadas no simulador.
  const LISBON = (() => {
    try { return new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Lisbon', hour: '2-digit', hourCycle: 'h23', weekday: 'short'}); }
    catch (e) { return null; }
  })();
  function lisbonParts(iso) {
    const d = new Date(iso);
    if (!LISBON) return {hour: d.getUTCHours(), weekday: d.getUTCDay()};
    const parts = Object.fromEntries(LISBON.formatToParts(d).map(p => [p.type, p.value]));
    return {hour: Number(parts.hour) % 24, weekday: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday)};
  }
  function heatmap(entries) {
    const rows = {};
    for (const e of entries || []) {
      if (!Number.isFinite(time(e.d))) continue;
      const src = sourceOf(e.t);
      const {hour} = lisbonParts(e.d);
      (rows[src] = rows[src] || Array(24).fill(0))[hour]++;
    }
    return rows;
  }

  // ---------- desempenho ----------
  function drawdown(series) {
    let peak = 0, max = 0, at = null;
    (series || []).forEach((v, i) => {
      if (!num(v)) return;
      if (v > peak) peak = v;
      if (peak - v > max) { max = peak - v; at = i; }
    });
    return {max: round(max, 2), at};
  }
  function periodTable(entries) {
    const byWeek = {}, bySource = {};
    for (const e of entries || []) {
      const t = time(e.d);
      if (!Number.isFinite(t)) continue;
      const src = sourceOf(e.t);
      const d = new Date(t), day = (d.getUTCDay() + 6) % 7;
      const monday = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - day)).toISOString().slice(0, 10);
      for (const [bucket, key] of [[byWeek, monday], [bySource, src]]) {
        const row = bucket[key] = bucket[key] || {bets: 0, settled: 0, units: 0, wins: 0};
        row.bets++;
        if (num(e.w)) {
          row.settled++;
          row.units += e.w;
          if (e.w > 0) row.wins++;
        }
      }
    }
    const fin = obj => Object.fromEntries(Object.entries(obj).map(([k, r]) =>
      [k, Object.assign(r, {units: round(r.units, 2), roi: r.settled ? round(r.units / r.settled, 4) : null})]));
    return {by_week: fin(byWeek), by_source: fin(bySource)};
  }

  // ---------- construtores de snapshots ----------
  // O cabeçalho não inclui a hora de geração: um snapshot cujo conteúdo não mudou mantém o mesmo hash entre
  // revisões e não é descarregado de novo. A coerência entre ficheiros é garantida pelo manifesto (hash por ficheiro).
  function base() {
    return {schema: SCHEMA};
  }

  function buildSummary(report, prospective, now) {
    const ref = report.questions[REFERENCE] || {};
    const cert = certificationState(report.brain);
    const pv = prospective ? validateProspective(prospective) : {ok: false, problems: ['ausente'], certification: []};
    const agenda = (report.brain.agenda || {});
    return Object.assign(base(), {kind: 'summary', data: {
      generated_at_utc: report.generated_at_utc,
      collected_at_utc: report.audit.collector_last_run_utc || report.audit.last_collected_at_utc || null,
      freshness: freshness(report.audit.collector_last_run_utc || report.audit.last_collected_at_utc, now),
      tracked: report.unique_bets, settled: report.settled_bets, messages: report.messages,
      reference: {roi: ref.roi || null, units: num(ref.units) ? ref.units : null, matches: ref.matches || null,
        wins: ref.wins || null, pushes: ref.pushes || null, avg_odds: ref.avg_odds || null, conclusive: ref.conclusive === true, note: ref.note || null},
      first_signal_utc: report.first_signal_utc || null,
      agenda_counts: agenda.counts || {}, agenda_items: (agenda.items || []).length,
      hypotheses_counts: report.brain.counts || {},
      certification: cert,
      prospective: prospective ? {valid: pv.ok, problems: pv.problems.concat(pv.certification),
        verdict: prospective.verdict, games_registered: prospective.games_registered,
        games_with_independent_result: prospective.games_with_independent_result,
        h1: prospective.H1 && {status: prospective.H1.status, games: prospective.H1.games_with_result, target: FROZEN.min_games},
        h2: prospective.H2 && {status: prospective.H2.status, games: prospective.H2.games_settled, target: FROZEN.min_games}} : null,
      narrative: Array.isArray(report.insights.narrative) ? report.insights.narrative.slice(0, 8) : [],
      recent_discoveries: (report.brain.notes || []).slice(0, 5).map(n => ({id: n.id, at_utc: n.at_utc, status: n.status, n: n.n, hit_rate: n.hit_rate, break_even: n.break_even})),
      verdict: VERDICT
    }});
  }

  function buildSignals(report) {
    const agenda = report.brain.agenda || {};
    const items = (agenda.items || []).map(it => ({match_id: it.match_id, match: it.match, league: it.league, family: it.family,
      type: it.type, source: sourceOf(it.type), phase: it.phase, when: it.when, kickoff_utc: it.kickoff_utc, date_utc: it.date_utc,
      minute: it.minute, score_at_signal: it.score_at_signal, market: it.market, selection: it.selection, odds: it.odds,
      odds_last: it.odds_last, max_mult: it.max_mult, share: it.share, share_last: it.share_last, verdict: it.verdict,
      conflict: it.conflict === true, validated: false, odds_band: it.odds_band, odds_band_n: it.odds_band_n,
      good: (Array.isArray(it.good) ? it.good : []).map(evidence), good_total: it.good_total,
      bad: (Array.isArray(it.bad) ? it.bad : []).map(evidence), bad_total: it.bad_total}));
    const markets = marketTimeline(report.matches);
    const committed = report.simulation && int(report.simulation.committed_games) ? report.simulation.committed_games : null;
    return Object.assign(base(), {kind: 'signals', data: {
      agenda_generated_utc: agenda.generated_utc || null, rules: agenda.rules || null, counts: agenda.counts || {},
      items, games: groupByGame(items).map(g => Object.assign({}, g, {signals: g.signals.map(s => signalKey(s))})),
      markets,
      polymarket_over25: (report.polymarket_over25 || []).map(p => ({match_id: p.match_id, match: p.match, date_utc: p.date_utc,
        kickoff_utc: p.kickoff_utc, phase: p.phase, market: p.market, selection: p.selection, odds: p.odds, share: p.share, total: p.total})),
      heatmap: heatmap(report.simulation && report.simulation.entries),
      price_move: report.insights.price_move || null,
      coverage: {games_with_market_history: markets.length, games_in_history: committed,
        share: committed ? round(markets.length / committed, 4) : null,
        note: 'cronologia por jogo publicada apenas para os jogos incluídos em report.matches'}
    }});
  }
  function evidence(h) {
    const e = h.economics || {};
    return {id: h.id, conditions: h.conditions, n: h.n, games: h.games, hits: h.hits, break_even: h.break_even,
      prob_above: h.prob_above, roi: h.roi, status: h.status, source: h.source,
      prospective: {status: e.status || null, scope: e.scope || null, results_confirmed: e.results_confirmed, wins: e.wins, losses: e.losses, pending: e.pending}};
  }

  function buildPerformance(report) {
    const series = (report.timeline || []).map(p => p.u);
    const sim = report.simulation || {};
    const ins = report.insights || {};
    return Object.assign(base(), {kind: 'performance', data: {
      reference: report.questions[REFERENCE] || null,
      groups: report.groups, questions: report.questions,
      accuracy: report.accuracy || {},
      timeline: (report.timeline || []).map(p => ({d: p.d, u: p.u, w: p.w, t: p.t, o: p.o, m: p.m, s: p.s})),
      timeline_window: {points: series.length, settled: report.settled_bets, note: 'últimas ' + series.length + ' liquidações publicadas'},
      drawdown_window: drawdown(series),
      luck: ins.luck || null,
      calibration: ins.calibration || [], regime: ins.regime || [],
      daily: report.daily || [],
      periods: periodTable(sim.entries),
      simulation_meta: Object.fromEntries(Object.entries(sim).filter(([k]) => k !== 'entries')),
      walk_forward: report.brain.walk_forward ? Object.fromEntries(Object.entries(report.brain.walk_forward).filter(([k]) => k !== 'curve')) : null,
      walk_forward_curve: (report.brain.walk_forward && report.brain.walk_forward.curve) || []
    }});
  }

  function buildScience(report, prospective, protocol) {
    const pv = prospective ? validateProspective(prospective) : null;
    const pick = p => p && {
      schema: p.schema, activated_utc: p.activated_utc, ledger_entries: p.ledger_entries,
      games_registered: p.games_registered, signals_registered: p.signals_registered,
      signals_registered_before_result: p.signals_registered_before_result,
      signals_result_known_at_registration: p.signals_result_known_at_registration,
      games_with_independent_result: p.games_with_independent_result, games_with_conflicting_results: p.games_with_conflicting_results,
      games_without_result_after_72h: p.games_without_result_after_72h, independent_result_share: p.independent_result_share,
      H1: p.H1, H2: p.H2, D1: p.D1, verdict: p.verdict};
    const decision = k => protocol && protocol.hypotheses && protocol.hypotheses[k] ? {
      status: protocol.hypotheses[k].status, population: protocol.hypotheses[k].population, decision: protocol.hypotheses[k].decision,
      statistic: protocol.hypotheses[k].statistic, definition: protocol.hypotheses[k].definition, limits: protocol.hypotheses[k].limits} : null;
    const b = report.brain;
    return Object.assign(base(), {kind: 'science', data: {
      prospective: pick(prospective), validation: pv,
      frozen: FROZEN, protocol: protocol ? {schema: protocol.schema, frozen: protocol.frozen, scope: protocol.scope,
        H1: decision('H1'), H2: decision('H2'), D1: decision('D1'), missing: protocol.independent_result && protocol.independent_result.missing,
        conflict: protocol.independent_result && protocol.independent_result.conflict} : null,
      certification: certificationState(b),
      strategy_board: b.strategy_board ? {counts: b.strategy_board.counts, verdict: b.strategy_board.verdict,
        statistical_claim: b.strategy_board.statistical_claim, criteria_sha256: b.strategy_board.criteria_sha256,
        executable_price_evidence: b.strategy_board.executable_price_evidence, costs_known: b.strategy_board.costs_known,
        reason_labels: b.strategy_board.reason_labels, reasons: b.strategy_board.reasons} : null,
      validation_method: b.validation ? {method: b.validation.method, alpha: b.validation.alpha, state: b.validation.state,
        ledger_valid: b.validation.ledger_valid, ledger_entries: b.validation.ledger_entries, test: b.validation.test} : null,
      multiplicity: b.multiplicity || null, expected_by_chance: num(b.expected_by_chance) ? b.expected_by_chance : null,
      eligibility: b.eligibility || null, evidence_scope: b.evidence_scope || null,
      verdict: VERDICT
    }});
  }

  function buildOperations(report, prospective, now) {
    const a = report.audit || {};
    const agendaItems = (report.brain.agenda && report.brain.agenda.items) || [];
    const dups = dedupe(agendaItems).reduce((s, x) => s + x.duplicates, 0);
    const days = (report.daily || []).map(d => d.day).filter(Boolean).sort();
    const gaps = [];
    for (let i = 1; i < days.length; i++) {
      const diff = (Date.parse(days[i]) - Date.parse(days[i - 1])) / 86400000;
      if (diff > 1) gaps.push({after: days[i - 1], before: days[i], missing_days: diff - 1});
    }
    const lag = (iso) => { const f = freshness(iso, now); return {at_utc: iso || null, minutes: f.minutes, state: f.state}; };
    return Object.assign(base(), {kind: 'operations', data: {
      collector: lag(a.collector_last_run_utc), highlightly: lag(a.hl_last_run_utc), bsd: lag(a.bsd_last_run_utc),
      counters: {versions: a.versions, edits_changing_signal: a.edits_changing_signal, bot_mark_disagreements: a.bot_mark_disagreements,
        impossible_ft_observations: a.impossible_ft_observations, hl_results: a.hl_results, hl_vs_bot_disagreements: a.hl_vs_bot_disagreements,
        hl_near_misses: a.hl_near_misses, bsd_results: a.bsd_results, bsd_vs_bot_disagreements: a.bsd_vs_bot_disagreements,
        bsd_near_misses: a.bsd_near_misses, goal_minutes_matches: a.goal_minutes_matches, goal_minutes_inconsistent: a.goal_minutes_inconsistent,
        manual_results: a.manual_results, manual_vs_other_disagreements: a.manual_vs_other_disagreements},
      name_review: (a.name_review || []).length,
      exclusions: {first_seen_with_result: report.excluded_first_seen_with_result, edited_before_seen: report.excluded_edited_before_seen,
        burst: report.excluded_burst, low_odds: report.excluded_low_odds, unparsed: report.unparsed, unsettled_market: report.unsettled_market},
      eligibility: report.brain.eligibility || null,
      types: report.types || {},
      unsettled: (report.unsettled || []).map(u => ({match_id: u.match_id, match: u.match, league: u.league, date_utc: u.date_utc, why: u.why, signals: u.signals})),
      agenda_duplicates: dups,
      continuity: {days: days.length, first_day: days[0] || null, last_day: days[days.length - 1] || null, gaps},
      prospective: prospective ? {conflicts: prospective.games_with_conflicting_results, missing_72h: prospective.games_without_result_after_72h,
        result_known_at_registration: prospective.signals_result_known_at_registration,
        independent_result_share: prospective.independent_result_share} : null,
      coverage: {
        timeline_points: (report.timeline || []).length, settled: report.settled_bets,
        recent_bets: (report.recent_bets || []).length, tracked: report.unique_bets,
        match_histories: Object.keys(report.matches || {}).length,
        committed_games: report.simulation ? report.simulation.committed_games : null,
        simulation_entries: report.simulation && Array.isArray(report.simulation.entries) ? report.simulation.entries.length : 0
      }
    }});
  }

  function buildLearning(report) {
    const b = report.brain;
    const lite = h => ({id: h.id, family: h.family, conditions: h.conditions, n: h.n, games: h.games, hits: h.hits,
      hit_rate: h.hit_rate, break_even: h.break_even, prob_above: h.prob_above, roi: h.roi, status: h.status,
      source: h.source || null, born_utc: h.born_utc || null, reason: h.reason || null, expect: h.expect || null, mode: h.mode || null,
      evidence_level: h.evidence_level || null,
      prospective: h.prospective ? {games: h.prospective.games, roi: h.prospective.roi,
        status: h.prospective.economics ? h.prospective.economics.status : null,
        scope: h.prospective.economics ? h.prospective.economics.scope : null} : null});
    const replicated = (b.top || []).filter(h => h.prospective && h.prospective.economics && h.prospective.economics.status === 'positive_exploratory');
    return Object.assign(base(), {kind: 'learning', data: {
      counts: b.counts || {}, hypotheses_total: num(b.hypotheses) ? b.hypotheses : null, tested: b.tested,
      status_rules: b.status_rules || null, rules: b.rules || null,
      journal: (b.notes || []).map(n => ({id: n.id, note: n.note, at_utc: n.at_utc, model: n.model, n: n.n, hits: n.hits,
        hit_rate: n.hit_rate, break_even: n.break_even, status: n.status})),
      llm: {runs: b.llm_runs, total: b.llm_total, last_run_utc: b.llm_last_run_utc, last_outcome: b.llm_last_outcome,
        today: b.llm_today || null, evaluation: b.llm_evaluation || null, hypotheses: (b.llm || []).map(lite)},
      promising: (b.top || []).filter(h => h.status === 'promissora').map(lite),
      top: (b.top || []).slice(0, 40).map(lite),
      rejected: (b.abandoned || []).slice(0, 60).map(lite), rejected_total: (b.abandoned || []).length,
      replicated_exploratory: replicated.slice(0, 40).map(lite), replicated_exploratory_total: replicated.length,
      track: b.track ? {by_source: b.track.by_source, meaning: b.track.meaning, unit: b.track.unit, min_results: b.track.min_results} : null,
      watch: (b.watch || []).slice(0, 30).map(w => ({date_utc: w.date_utc, match: w.match, match_id: w.match_id, type: w.type,
        market: w.market, selection: w.selection, odds: w.odds, phase: w.phase, reasons: (w.reasons || []).length})),
      multiplicity: b.multiplicity || null, expected_by_chance: b.expected_by_chance,
      limitations: [
        'Hipóteses "promissoras" e "abandonadas" não têm correção de multiplicidade e são exploratórias.',
        'Uma replicação prospetiva exploratória não é validação: preços executáveis e custos não são conhecidos.',
        'As hipóteses partilham jogos; contagens de hipóteses não são amostras independentes.',
        'As notas do modelo de linguagem são texto gerado e não constituem evidência.'
      ]
    }});
  }

  function buildAll(report, prospective, protocol, now) {
    const at = now === undefined ? Date.now() : now;
    const files = {
      summary: buildSummary(report, prospective, at), signals: buildSignals(report), performance: buildPerformance(report),
      science: buildScience(report, prospective, protocol), operations: buildOperations(report, prospective, at), learning: buildLearning(report)
    };
    for (const k of Object.keys(files)) files[k] = sanitize(files[k]);
    return files;
  }

  // ---------- consistência entre revisões ----------
  function validateManifest(m) {
    const problems = [];
    if (!isObj(m) || m.schema !== MANIFEST_SCHEMA) return {ok: false, problems: ['manifesto com esquema desconhecido']};
    if (!Number.isFinite(time(m.report_generated_at_utc))) problems.push('manifesto sem revisão');
    if (!isObj(m.files)) problems.push('manifesto sem ficheiros');
    else for (const k of SNAPSHOTS) {
      const f = m.files[k];
      if (!isObj(f) || f.path !== FILE(k) || typeof f.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(f.sha256) || !int(f.bytes)) problems.push('entrada inválida: ' + k);
    }
    return {ok: problems.length === 0, problems};
  }
  // integrity: {sha256} calculado no cliente quando o navegador o permite; senão {bytes}. Um ficheiro de outra revisão
  // tem outro hash (ou tamanho) e é recusado: nunca se misturam snapshots de revisões diferentes.
  function checkSnapshot(name, snap, manifest, integrity) {
    const problems = [], entry = manifest && manifest.files && manifest.files[name];
    if (!entry) problems.push(name + ': ausente do manifesto');
    if (!isObj(snap) || snap.schema !== SCHEMA) problems.push(name + ': esquema incompatível');
    else {
      if (snap.kind !== name) problems.push(name + ': tipo errado (' + snap.kind + ')');
      if (!isObj(snap.data)) problems.push(name + ': sem dados');
    }
    if (entry && integrity) {
      if (integrity.sha256 && integrity.sha256 !== entry.sha256) problems.push(name + ': hash diferente do manifesto');
      if (!integrity.sha256 && integrity.bytes !== entry.bytes) problems.push(name + ': tamanho diferente do manifesto');
    }
    if (entry && !integrity) problems.push(name + ': integridade não verificada');
    return {ok: problems.length === 0, problems};
  }

  // Sequenciador: só aplica a resposta mais recente pedida e nunca recua no tempo de geração.
  function createSequencer() {
    let requested = 0, applied = 0, appliedAt = -Infinity;
    return {
      begin() { return ++requested; },
      accept(ticket, generatedAt) {
        const t = time(generatedAt);
        if (ticket < applied || !Number.isFinite(t) || t < appliedAt) return false;
        applied = ticket; appliedAt = t; return true;
      },
      state() { return {requested, applied, appliedAt}; }
    };
  }

  return {SCHEMA, MANIFEST_SCHEMA, PROSPECTIVE_SCHEMA, SNAPSHOTS, FILE, FROZEN, VERDICT, REFERENCE,
    sanitize, privacyFindings, validateReport, validateProspective, certificationState, freshness,
    dedupe, groupByGame, signalKey, marketTimeline, heatmap, drawdown, periodTable, sourceOf, side,
    buildSummary, buildSignals, buildPerformance, buildScience, buildOperations, buildLearning, buildAll,
    validateManifest, checkSnapshot, createSequencer};
});

// Testes do núcleo V6 (node --test tests/v6_core.test.cjs). Sem rede, sem dependências.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const core = require('../docs/v6/v6-core.js');
const {build} = require('../scripts/build_dashboard_snapshots.cjs');

const ROOT = path.join(__dirname, '..');
const REPORT_PATH = process.env.V6_REPORT || path.join(ROOT, 'data/report.json');
const report = JSON.parse(fs.readFileSync(REPORT_PATH, 'utf8'));
const prospective = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/prospective_report.json'), 'utf8'));
const protocol = JSON.parse(fs.readFileSync(path.join(ROOT, 'scangoal/prospective_protocol.json'), 'utf8'));
const clone = o => structuredClone(o);
const AT = Date.parse(report.generated_at_utc);

test('o relatório publicado passa a validação', () => {
  assert.deepEqual(core.validateReport(report).problems, []);
  assert.deepEqual(core.validateProspective(prospective), {ok: true, problems: [], certification: []});
});

test('dados incompletos: campos obrigatórios em falta são recusados', () => {
  for (const key of ['groups', 'questions', 'audit', 'brain', 'timeline', 'generated_at_utc', 'unique_bets']) {
    const r = clone(report); delete r[key];
    assert.equal(core.validateReport(r).ok, false, key);
  }
  assert.equal(core.validateReport(null).ok, false);
  assert.equal(core.validateReport([]).ok, false);
});

test('dados incompletos: secções opcionais em falta não impedem a derivação', () => {
  const r = clone(report);
  delete r.matches; delete r.simulation; delete r.polymarket_over25; delete r.unsettled; delete r.recent_bets;
  delete r.brain.agenda; delete r.brain.notes; delete r.brain.llm; delete r.brain.top; delete r.brain.walk_forward;
  r.insights = {};
  const files = core.buildAll(r, null, null, AT);
  assert.equal(files.signals.data.items.length, 0);
  assert.equal(files.signals.data.coverage.games_in_history, null);
  assert.equal(files.summary.data.prospective, null);
  assert.equal(files.science.data.prospective, null);
  assert.deepEqual(files.performance.data.walk_forward_curve, []);
});

test('incoerências internas do relatório são detetadas', () => {
  const r = clone(report);
  Object.values(r.groups)[0].bets += 1;
  assert.match(core.validateReport(r).problems.join(), /soma dos grupos/);
  const r2 = clone(report);
  r2.timeline[0].d = '2099-01-01T00:00:00+00:00';
  assert.match(core.validateReport(r2).problems.join(), /posterior/);
  const r3 = clone(report);
  r3.settled_bets = r3.unique_bets + 1;
  assert.equal(core.validateReport(r3).ok, false);
});

test('certificações inválidas no relatório prospetivo nunca passam como decisão', () => {
  const p = clone(prospective);
  p.H1.status = 'supported_predictive_only';             // com 19 jogos
  let v = core.validateProspective(p);
  assert.equal(v.ok, false);
  assert.match(v.certification.join(), /menos de 200/);
  p.H1.games_with_result = 250; p.H1.games_in_population = 300; p.H1.z = 1.2;
  v = core.validateProspective(p);
  assert.match(v.certification.join(), /z abaixo/);
  const p2 = clone(prospective); p2.H2.status = 'validated';
  assert.match(core.validateProspective(p2).certification.join(), /fora do protocolo/);
  const p3 = clone(prospective); p3.verdict = 'RENTABILIDADE DEMONSTRADA';
  assert.match(core.validateProspective(p3).certification.join(), /veredito/);
  const p4 = clone(prospective); p4.schema = 'outro';
  assert.equal(core.validateProspective(p4).ok, false);
  const p5 = clone(prospective); p5.signals_registered += 3;
  assert.match(core.validateProspective(p5).problems.join(), /incoerente/);
});

test('estratégias "validadas" sem certificação ativa são anuladas', () => {
  const b = clone(report.brain);
  b.strategy_board.counts.validada = 3;
  b.strategy_board.statistical_claim = 'significant';
  const c = core.certificationState(b);
  assert.equal(c.active, false);
  assert.equal(c.validated, 0);
  assert.equal(c.invalid.length, 2);
  b.validation.state = 'active'; b.validation.ledger_valid = false;
  assert.match(core.certificationState(b).invalid.join(), /registo inválido/);
});

test('duplicações na agenda são agrupadas e contadas', () => {
  const items = report.brain.agenda.items.slice(0, 3);
  const doubled = items.concat(clone(items), clone(items.slice(0, 1)));
  const out = core.dedupe(doubled);
  assert.equal(out.length, new Set(items.map(core.signalKey)).size);
  assert.equal(out.reduce((s, x) => s + x.duplicates, 0), doubled.length - out.length);
  const games = core.groupByGame(doubled);
  assert.equal(games.reduce((s, g) => s + g.signals.length, 0), out.length);
});

test('contradições e convergências entre fontes', () => {
  const matches = {x: {teams: ['A', 'B'], events: [
    {d: '2026-11-20T10:00:00+00:00', k: 'POLYMARKET', selection: 'Over 2.5', market: 'Asian Total', odds: 1.9, total: 1000},
    {d: '2026-11-20T11:00:00+00:00', k: 'STAKE_LOAD', selection: 'Under 2.5', market: 'Total', odds: 2.0, total: 500},
    {d: '2026-11-20T12:00:00+00:00', k: 'BETFAIR', selection: 'Over 2.5', market: 'Total', odds: 1.8, total: 800, mult: 4},
    {d: 'inválida', k: 'BETFAIR', selection: 'Over 2.5', market: 'Total', odds: 9}]}};
  const [m] = core.marketTimeline(matches);
  assert.equal(m.events, 3);
  assert.deepEqual(m.contradictions.map(c => [c.a, c.b]), [['over', 'under']]);
  assert.deepEqual(m.convergences.map(c => c.side), ['over']);
  const bf = m.selections.find(s => s.source === 'Betfair');
  assert.equal(bf.max_mult, 4);
});

test('respostas fora de ordem e revisões mais antigas são ignoradas', () => {
  const seq = core.createSequencer();
  const a = seq.begin(), b = seq.begin();
  assert.equal(seq.accept(b, '2026-11-25T10:00:00Z'), true);
  assert.equal(seq.accept(a, '2026-11-25T11:00:00Z'), false, 'pedido anterior chegou depois');
  const c = seq.begin();
  assert.equal(seq.accept(c, '2026-11-25T09:00:00Z'), false, 'revisão mais antiga');
  const d = seq.begin();
  assert.equal(seq.accept(d, 'lixo'), false);
  assert.equal(seq.accept(seq.begin(), '2026-11-25T10:00:00Z'), true, 'mesma revisão é aceite');
});

test('dados desatualizados: estados de frescura', () => {
  const now = Date.parse('2026-11-25T12:00:00Z');
  assert.equal(core.freshness('2026-11-25T11:50:00Z', now).state, 'normal');
  assert.equal(core.freshness('2026-11-25T11:00:00Z', now).state, 'atrasada');
  assert.equal(core.freshness('2026-11-25T08:00:00Z', now).state, 'parada');
  assert.equal(core.freshness(null, now).state, 'desconhecido');
  assert.equal(core.freshness('2026-11-25T13:00:00Z', now).minutes, 0);
});

test('privacidade: chaves e valores privados nunca saem nos snapshots', () => {
  const r = clone(report);
  r.unparsed_samples = ['contacto nuno@example.com'];
  r.insights.narrative = ['token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'chamar @pessoa_privada', 'ok'];
  r.brain.notes = [{id: 'x', note: 'ghp_' + 'a'.repeat(36), at_utc: r.generated_at_utc, api_key: 'segredo', chat_id: 99}];
  r.brain.agenda.items[0].session = 'abc';
  const files = core.buildAll(r, prospective, protocol, AT);
  assert.deepEqual(core.privacyFindings(files), []);
  const text = JSON.stringify(files);
  for (const bad of ['nuno@example.com', 'AAAAAAAAAAAAAAAA', '@pessoa_privada', 'ghp_aaaa', 'segredo', '"chat_id"', '"session"', 'unparsed_samples', 'contacto']) assert.ok(!text.includes(bad), bad);
  assert.equal(files.summary.data.narrative[2], 'ok');
  assert.ok(core.privacyFindings({email: 'x'}).length === 1);
});

test('números publicados são copiados sem alteração (sem estatísticas inventadas)', () => {
  const files = core.buildAll(report, prospective, protocol, AT);
  const sc = files.science.data.prospective;
  for (const k of ['H1', 'H2', 'D1', 'games_registered', 'ledger_entries', 'games_with_independent_result', 'verdict']) assert.deepEqual(sc[k], prospective[k], k);
  const ref = report.questions[core.REFERENCE];
  assert.deepEqual(files.summary.data.reference.roi, ref.roi);
  assert.equal(files.summary.data.reference.units, ref.units);
  assert.deepEqual(files.performance.data.groups, report.groups);
  assert.deepEqual(files.performance.data.luck, report.insights.luck);
  const byWeek = Object.values(files.performance.data.periods.by_week).reduce((s, r) => s + r.bets, 0);
  assert.equal(byWeek, report.simulation.entries.length);
  const units = Object.values(files.performance.data.periods.by_source).reduce((s, r) => s + r.units, 0);
  assert.ok(Math.abs(units - ref.units) < 0.05, 'soma por fonte ' + units + ' vs Q7 ' + ref.units);
  assert.equal(files.science.data.verdict, core.VERDICT);
  assert.equal(files.signals.data.items.every(i => i.validated === false), true);
});

test('gerador: manifesto coerente, determinístico e sem tocar no relatório original', () => {
  const before = crypto.createHash('sha256').update(fs.readFileSync(REPORT_PATH)).digest('hex');
  const dirA = fs.mkdtempSync(path.join(os.tmpdir(), 'v6a-')), dirB = fs.mkdtempSync(path.join(os.tmpdir(), 'v6b-'));
  const opts = out => ({report: REPORT_PATH, prospective: path.join(ROOT, 'data/prospective_report.json'), protocol: path.join(ROOT, 'scangoal/prospective_protocol.json'), out});
  const m = build(opts(dirA)), m2 = build(opts(dirB));
  assert.deepEqual(m, m2);
  assert.equal(core.validateManifest(m).ok, true);
  assert.equal(m.report_sha256, before);
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(REPORT_PATH)).digest('hex'), before);
  let total = 0;
  for (const name of core.SNAPSHOTS) {
    const body = fs.readFileSync(path.join(dirA, core.FILE(name)));
    total += body.length;
    const sha = crypto.createHash('sha256').update(body).digest('hex');
    assert.deepEqual(core.checkSnapshot(name, JSON.parse(body), m, {sha256: sha}).problems, []);
  }
  assert.ok(total < m.report_bytes / 3, 'snapshots devem ser bem menores que o relatório');
  assert.ok(m.files.summary.bytes < 20000);
});

test('snapshots incompatíveis ou de outra revisão são recusados', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v6c-'));
  const m = build({report: REPORT_PATH, prospective: path.join(ROOT, 'data/prospective_report.json'), protocol: path.join(ROOT, 'scangoal/prospective_protocol.json'), out: dir});
  const snap = JSON.parse(fs.readFileSync(path.join(dir, core.FILE('learning')), 'utf8'));
  assert.match(core.checkSnapshot('learning', Object.assign({}, snap, {schema: 'goal_roi_dashboard_v5'}), m, {sha256: m.files.learning.sha256}).problems.join(), /esquema/);
  assert.match(core.checkSnapshot('science', snap, m, {sha256: m.files.science.sha256}).problems.join(), /tipo errado/);
  assert.match(core.checkSnapshot('learning', snap, m, {sha256: 'f'.repeat(64)}).problems.join(), /hash/);
  assert.match(core.checkSnapshot('learning', snap, m, {bytes: 1}).problems.join(), /tamanho/);
  assert.match(core.checkSnapshot('learning', snap, m).problems.join(), /integridade/);
  const bad = clone(m); bad.files.signals.path = '../../etc/passwd';
  assert.equal(core.validateManifest(bad).ok, false);
  assert.equal(core.validateManifest(Object.assign({}, m, {schema: 'x'})).ok, false);
});

test('atualização seletiva: conteúdos inalterados mantêm o hash entre revisões', () => {
  const r2 = clone(report);
  r2.generated_at_utc = new Date(AT + 600000).toISOString();
  const a = core.buildAll(report, prospective, protocol, AT), b = core.buildAll(r2, prospective, protocol, AT + 600000);
  assert.equal(JSON.stringify(a.learning), JSON.stringify(b.learning));
  assert.equal(JSON.stringify(a.science), JSON.stringify(b.science));
  assert.notEqual(JSON.stringify(a.summary), JSON.stringify(b.summary));
});

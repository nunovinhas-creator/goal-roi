// Separador aberto durante 8 horas (relógio simulado do Playwright): revisões novas a cada 10 min, falhas HTTP,
// relatório corrompido, snapshots que aparecem, ficam obsoletos, desaparecem e voltam. No fim a página tem de mostrar
// a revisão mais recente, sem mistura de revisões, sem erros de página e sem crescimento do DOM.
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {start, ROOT, V6_CONFIG} = require('./v6_server.cjs');
const core = require('../docs/v6/v6-core.js');
const {build} = require('../scripts/build_dashboard_snapshots.cjs');

const base = JSON.parse(fs.readFileSync(path.join(ROOT, 'docs/report.json'), 'utf8'));
const T0 = Date.parse(base.generated_at_utc);
const HOURS = Number(process.env.V6_HOURS || 8), STEP = 3 * 60000;

function reportAt(t) {
  const r = structuredClone(base);
  r.generated_at_utc = new Date(t).toISOString();
  r.audit.collector_last_run_utc = r.audit.last_collected_at_utc = new Date(t - 60000).toISOString();
  return JSON.stringify(r);
}

(async () => {
  const srv = await start();
  const browser = await chromium.launch(process.env.V6_CHROMIUM ? {executablePath: process.env.V6_CHROMIUM} : {});
  const ctx = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.clock.install({time: T0 + 60000});
  await page.addInitScript(cfg => { window.V6_CONFIG = cfg; }, Object.assign({}, V6_CONFIG, {refreshMs: STEP}));
  let rev = T0;
  const snapDir = fs.mkdtempSync(path.join(os.tmpdir(), 'v6long-'));
  const publishSnapshots = t => {
    const f = path.join(snapDir, 'r.json'); fs.writeFileSync(f, reportAt(t));
    const out = fs.mkdtempSync(path.join(snapDir, 's-'));
    const m = build({report: f, prospective: path.join(ROOT, 'data/prospective_report.json'), protocol: path.join(ROOT, 'scangoal/prospective_protocol.json'), out});
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: fs.readFileSync(path.join(out, 'dashboard-manifest.json'))});
    for (const k of core.SNAPSHOTS) srv.set('/v6/snapshots/' + core.FILE(k), {body: fs.readFileSync(path.join(out, core.FILE(k)))});
    return m;
  };
  srv.set('/report.json', {body: reportAt(rev)});
  await page.goto(srv.base + '/v6/#performance');
  await page.waitForFunction(() => window.__V6 && window.__V6.state.mode && !window.__V6.state.busy);
  const views = ['command', 'market', 'radar', 'brain', 'lab', 'performance', 'operations'];
  const visitAll = async () => { for (const v of views) { await page.evaluate(h => { location.hash = h; }, v); await page.waitForSelector('#view-' + v + ' h2'); } };
  await visitAll();
  const nodes0 = await page.evaluate(() => document.getElementsByTagName('*').length);
  const cycles = HOURS * 60 / 3;
  const seen = new Set();
  for (let i = 1; i <= cycles; i++) {
    const now = T0 + 60000 + i * STEP;
    if (i % 3 === 0) { rev = now - 30000; srv.set('/report.json', {body: reportAt(rev)}); }        // nova revisão a cada ~10 min
    if (i % 40 === 10) srv.set('/report.json', {status: 503});                                       // falha HTTP
    if (i % 40 === 11) srv.set('/report.json', {body: '{"generated_at_utc": "' + new Date(now).toISOString() + '", "truncado":'});
    if (i % 40 === 12) srv.set('/report.json', {body: reportAt(rev)});
    if (i % 40 === 20) publishSnapshots(rev);                                                         // snapshots atuais
    if (i % 40 === 26) srv.clear('/v6/snapshots/dashboard-manifest.json');                             // desaparecem
    if (i % 40 === 30) publishSnapshots(rev - 3600000);                                               // obsoletos (1 h atrás)
    if (i % 40 === 35) { for (const k of core.SNAPSHOTS) srv.clear('/v6/snapshots/' + core.FILE(k)); srv.clear('/v6/snapshots/dashboard-manifest.json'); }
    await page.clock.runFor(STEP);
    await page.waitForFunction(() => !window.__V6.state.busy, null, {timeout: 30000});
    if (i % 7 === 0) await page.evaluate(v => { location.hash = v; }, views[(i / 7) % views.length]);
    const st = await page.evaluate(() => ({mode: window.__V6.state.mode, applied: window.__V6.state.appliedRevision,
      sum: window.__V6.state.files.summary && window.__V6.state.files.summary.data.generated_at_utc}));
    seen.add(st.mode);
    // nunca mistura: o resumo aplicado é sempre da revisão aplicada
    assert.equal(Date.parse(st.sum), Date.parse(st.applied), 'mistura de revisões no ciclo ' + i);
  }
  // ciclo final estável: revisão mais recente aplicada
  srv.clear(); srv.set('/report.json', {body: reportAt(rev)});
  await page.clock.runFor(STEP);
  await page.waitForFunction(() => !window.__V6.state.busy);
  const final = await page.evaluate(() => ({applied: window.__V6.state.appliedRevision, err: document.getElementById('banner-error').hidden}));
  assert.equal(Date.parse(final.applied), rev, 'revisão final');
  assert.equal(final.err, true, 'banner de erro deve desaparecer após recuperação');
  await visitAll();
  const nodes1 = await page.evaluate(() => document.getElementsByTagName('*').length);
  assert.ok(nodes1 <= nodes0 * 1.25, 'DOM cresceu de ' + nodes0 + ' para ' + nodes1);
  assert.deepEqual(errors, []);
  assert.ok(seen.has('snapshots') && seen.has('relatório'), 'deve alternar entre modos: ' + [...seen]);
  const st = srv.state.log;
  console.log(JSON.stringify({hours: HOURS, cycles, requests: st.length, bytes: srv.state.bytes, modes: [...seen], dom: [nodes0, nodes1]}));
  await browser.close(); await srv.close();
  console.log('ok - separador aberto ' + HOURS + ' h');
})().catch(e => { console.error('not ok -', e && e.stack); process.exit(1); });

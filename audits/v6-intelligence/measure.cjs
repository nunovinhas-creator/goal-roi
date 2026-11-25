// Medição de desempenho antes (dashboard clássico) e depois (V6, modos relatório e snapshots).
//   node audits/v6-intelligence/measure.cjs > audits/v6-intelligence/performance.json
// Chromium com CPU 4× mais lenta e rede de telemóvel simulada (1,6 Mbit/s, 150 ms), servidor local com ETag.
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require('playwright');
const {start, ROOT, V6_CONFIG} = require('../../tests/v6_server.cjs');
const {build} = require('../../scripts/build_dashboard_snapshots.cjs');

const RUNS = Number(process.env.RUNS || 3), CYCLES = 10;
const NET = {offline: false, latency: 150, downloadThroughput: 1.6 * 1024 * 1024 / 8, uploadThroughput: 750 * 1024 / 8};
const median = xs => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];

async function measure(browser, srv, kind) {
  const ctx = await browser.newContext({viewport: {width: 412, height: 915}, isMobile: true, hasTouch: true});
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', NET);
  await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
  let transferred = 0;
  cdp.on('Network.loadingFinished', e => { transferred += e.encodedDataLength; });
  await page.addInitScript(cfg => {
    window.V6_CONFIG = cfg;
    window.__long = 0;
    try { new PerformanceObserver(l => { for (const e of l.getEntries()) window.__long += e.duration; }).observe({type: 'longtask', buffered: true}); } catch (e) {}
  }, V6_CONFIG);
  srv.reset();
  const t0 = Date.now();
  if (kind === 'clássico') {
    await page.goto(srv.base + '/');
    await page.waitForFunction(() => /\d/.test(document.getElementById('metric-tracked').textContent), null, {timeout: 120000});
  } else {
    await page.goto(srv.base + '/v6/');
    await page.waitForFunction(() => document.querySelector('#view-command .kpis'), null, {timeout: 120000});
  }
  const firstContent = Date.now() - t0;
  if (kind !== 'clássico') await page.waitForFunction(() => window.__V6.state.mode && !window.__V6.state.busy && !/parcial/.test(window.__V6.state.mode), null, {timeout: 120000});
  const complete = Date.now() - t0;
  const initialBytes = transferred, longTasks = await page.evaluate(() => Math.round(window.__long));
  transferred = 0;
  const r0 = srv.state.log.length;
  for (let i = 0; i < CYCLES; i++) {
    if (kind === 'clássico') await page.evaluate(() => agendaRefresh()); // eslint-disable-line no-undef
    else await page.evaluate(() => window.__V6.refresh());
  }
  const refreshLog = srv.state.log.slice(r0);
  const out = {firstContentMs: firstContent, completeMs: complete, initialTransferBytes: initialBytes, longTasksMs: longTasks,
    refreshTransferBytes: transferred, refreshRequests: refreshLog.length, refresh304: refreshLog.filter(l => l.status === 304).length};
  await ctx.close();
  return out;
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v6perf-'));
  const manifest = build({report: path.join(ROOT, 'docs/report.json'), prospective: path.join(ROOT, 'data/prospective_report.json'),
    protocol: path.join(ROOT, 'scangoal/prospective_protocol.json'), out: dir});
  const files = {'/v6/snapshots/dashboard-manifest.json': path.join(dir, 'dashboard-manifest.json')};
  for (const f of Object.values(manifest.files)) files['/v6/snapshots/' + f.path] = path.join(dir, f.path);
  const plain = await start(), withSnap = await start({files});
  const browser = await chromium.launch();
  const result = {conditions: {cpu: '4× mais lenta', network: '1,6 Mbit/s, 150 ms', viewport: '412×915 (Android)', runs: RUNS, refreshCycles: CYCLES},
    report_bytes: manifest.report_bytes, snapshot_bytes: Object.fromEntries(Object.entries(manifest.files).map(([k, f]) => [k, f.bytes])), results: {}};
  for (const [kind, srv] of [['clássico', plain], ['v6-relatório', plain], ['v6-snapshots', withSnap]]) {
    const runs = [];
    for (let i = 0; i < RUNS; i++) runs.push(await measure(browser, srv, kind));
    result.results[kind] = Object.fromEntries(Object.keys(runs[0]).map(k => [k, median(runs.map(r => r[k]))]));
  }
  await browser.close(); await plain.close(); await withSnap.close();
  console.log(JSON.stringify(result, null, 1));
})();

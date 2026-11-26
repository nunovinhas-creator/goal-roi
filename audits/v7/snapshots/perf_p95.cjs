// Mede só o modo V6 snapshots com N repetições (p50/p95), no perfil da PR #98:
// CPU 4× mais lenta, 1,6 Mbit/s, 150 ms, 412×915. Servidor local com ETag e gzip (tests/v6_server.cjs).
//   RUNS=20 node audits/v7/snapshots/perf_p95.cjs <checkout-da-pr98> > resultado.json
// Só lê o checkout indicado; os snapshots são gerados numa pasta temporária.
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(process.argv[2] || '.');
const {start, V6_CONFIG} = require(path.join(root, 'tests/v6_server.cjs'));
const {build} = require(path.join(root, 'scripts/build_dashboard_snapshots.cjs'));
const RUNS = Number(process.env.RUNS || 20);
const NET = {offline: false, latency: 150, downloadThroughput: 1.6 * 1024 * 1024 / 8, uploadThroughput: 750 * 1024 / 8};
const q = (xs, p) => { const s = xs.slice().sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)]; };

async function once(browser, srv) {
  const ctx = await browser.newContext({viewport: {width: 412, height: 915}, isMobile: true, hasTouch: true});
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', NET);
  await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
  let bytes = 0;
  cdp.on('Network.loadingFinished', e => { bytes += e.encodedDataLength; });
  await page.addInitScript(cfg => { window.V6_CONFIG = cfg; }, V6_CONFIG);
  srv.reset();
  const t0 = Date.now();
  await page.goto(srv.base + '/v6/');
  await page.waitForFunction(() => document.querySelector('#view-command .kpis'), null, {timeout: 120000});
  const first = Date.now() - t0;
  await page.waitForFunction(() => window.__V6.state.mode && !window.__V6.state.busy && !/parcial/.test(window.__V6.state.mode), null, {timeout: 120000});
  const complete = Date.now() - t0;
  const mode = await page.evaluate(() => window.__V6.state.mode);
  await ctx.close();
  return {first, complete, bytes, mode};
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v7perf-'));
  const manifest = build({report: path.join(root, 'docs/report.json'), prospective: path.join(root, 'data/prospective_report.json'),
    protocol: path.join(root, 'scangoal/prospective_protocol.json'), out: dir});
  const files = {'/v6/snapshots/dashboard-manifest.json': path.join(dir, 'dashboard-manifest.json')};
  for (const f of Object.values(manifest.files)) files['/v6/snapshots/' + f.path] = path.join(dir, f.path);
  const srv = await start({files});
  const browser = await chromium.launch(process.env.V6_CHROMIUM ? {executablePath: process.env.V6_CHROMIUM} : {});
  await once(browser, srv); // aquecimento descartado
  const runs = [];
  for (let i = 0; i < RUNS; i++) runs.push(await once(browser, srv));
  await browser.close(); await srv.close();
  const firsts = runs.map(r => r.first), completes = runs.map(r => r.complete);
  console.log(JSON.stringify({
    conditions: {cpu: '4× mais lenta', network: '1,6 Mbit/s, 150 ms', viewport: '412×915', runs: RUNS, warmup_discarded: 1},
    report_bytes: manifest.report_bytes,
    snapshot_bytes: Object.fromEntries(Object.entries(manifest.files).map(([k, f]) => [k, f.bytes])),
    modes: [...new Set(runs.map(r => r.mode))],
    firstContentMs: {p50: q(firsts, 0.5), p95: q(firsts, 0.95), max: Math.max(...firsts)},
    completeMs: {p50: q(completes, 0.5), p95: q(completes, 0.95), max: Math.max(...completes)},
    initialTransferBytes: q(runs.map(r => r.bytes), 0.5),
    samples: runs.map(r => [r.first, r.complete]),
  }, null, 1));
})();

// Ensaio de staging HTTPS local dos snapshots (PR #103 → V7.2 da PR #105). Custo 0; nada sai da máquina; nada é publicado.
//
//   node ops/staging/drill.mjs [--out ops/staging/resultados-staging.json]
//
// Os mesmos cenários correm para três desenhos:
//   v1     — publicador da PR #103 (2 ranhuras, ?v=) com client_integrity.mjs
//   slots3 — publisher_v2 com 3 ranhuras e client_integrity_v2.mjs
//   cas    — publisher_v2 com objetos imutáveis e client_integrity_v2.mjs (desenho recomendado)
// Cada cenário: origem temporária nova, builder real a partir de cópias do relatório, HTTPS com CA efémera,
// CDN simulada (max-age=600, chave com ou sem query string, relógio controlado).
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import {fileURLToPath} from 'node:url';
import * as v1 from '../../audits/v7/snapshots/client_integrity.mjs';
import * as v2 from '../../audits/v7/snapshots/client_integrity_v2.mjs';

const require = createRequire(import.meta.url);
const {start} = require('./https_cdn.cjs');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const POINTER = 'dashboard-manifest.json';
export const DESIGNS = ['v1', 'slots3', 'cas'];
const NAMES = '(summary|signals|performance|science|operations|learning)';
const PUBLISHABLE = new RegExp('^(dashboard-manifest\\.json|objects/[0-9a-f]{64}/dashboard-' + NAMES + '\\.json|s[0-9]/dashboard-' + NAMES + '\\.json)$');

function publisher(design, origin, cdn, lockDir) {
  return rev => JSON.parse(execFileSync('python3', [path.join(HERE, 'publish_rev.py'), '--out', origin, '--rev', String(rev),
    '--design', design, '--now', String(cdn.now()), '--lock', path.join(lockDir, 'lock')],
  {cwd: ROOT, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, timeout: 120000}).toString());
}

// gap: segundos simulados entre obter o ponteiro e pedir os corpos (latência real de um cliente).
async function read(design, cdn, gap = 0) {
  const pointer = JSON.parse(await cdn.get(POINTER));
  cdn.advance(gap);
  const got = design === 'v1'
    ? await v1.loadVerified(pointer, p => cdn.get(p))
    : await v2.loadVerified(pointer, p => cdn.get(p), undefined, async () => JSON.parse(await cdn.get(POINTER)));
  if (!got) return {which: 'full_report'};
  const which = design === 'v1' ? got.which : (got.which === 'current' ? 'current' : 'previous');
  return {which, generated: got.revision.report_generated_at_utc};
}

async function scenario(design, name, keyMode, fn) {
  const origin = fs.mkdtempSync(path.join(os.tmpdir(), 'goal-roi-origin-'));
  const lockDir = fs.mkdtempSync(path.join(os.tmpdir(), 'goal-roi-lock-'));
  const cdn = await start({root: origin, ttl: 600, keyMode});
  try {
    const res = await fn(cdn, publisher(design, origin, cdn, lockDir), origin);
    const files = [];
    const walk = d => { for (const e of fs.readdirSync(d, {withFileTypes: true})) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else files.push(path.relative(origin, p).split(path.sep).join('/')); } };
    walk(origin);
    return {design, name, keyMode, ...res, stats: {hits: cdn.stats.hits, misses: cdn.stats.misses, poisoned_keys: cdn.stats.poisoned_keys},
      unpublishable_files: design === 'v1' ? null : files.filter(f => !PUBLISHABLE.test(f))};
  } finally { await cdn.close(); for (const d of [origin, lockDir]) fs.rmSync(d, {recursive: true, force: true}); }
}

const untrusted = base => new Promise(resolve => {
  https.get(base + '/' + POINTER, {rejectUnauthorized: true}, () => resolve('aceite')).on('error', e => resolve(e.code || 'erro'));
});

export async function drill(designs = DESIGNS) {
  const results = [];
  for (const d of designs) {
    results.push(await scenario(d, 'S1', 'full', async (cdn, pub) => {
      pub(1);
      return {tls_without_ca: await untrusted(cdn.base), read: await read(d, cdn)};
    }));
    results.push(await scenario(d, 'S2', 'full', async (cdn, pub) => {   // ponteiro em cache, 1 publicação
      pub(1); cdn.advance(60); pub(2);
      const before = await read(d, cdn);
      cdn.advance(120); pub(3); cdn.advance(120);
      return {before, after: await read(d, cdn)};
    }));
    results.push(await scenario(d, 'S3', 'full', async (cdn, pub) => {   // mesma cache, 2 publicações (A-B-A)
      pub(1); cdn.advance(60); pub(2);
      const before = await read(d, cdn);
      cdn.advance(120); pub(3); cdn.advance(120); pub(4); cdn.advance(120);
      const during = await read(d, cdn);
      cdn.advance(600);
      return {before, during, after_ttl: await read(d, cdn)};
    }));
    results.push(await scenario(d, 'S3b', 'full', async (cdn, pub) => {  // níveis de cache diferentes, 3 publicações
      pub(1); cdn.advance(60); pub(2);
      const before = await read(d, cdn);
      cdn.purge(k => k === '/' + POINTER);   // ponteiro mantido num nível; corpos despejados (outro nó, LRU)
      cdn.advance(100); pub(3); cdn.advance(100); pub(4); cdn.advance(100); pub(5); cdn.advance(100);
      const during = await read(d, cdn);
      const second_reader = await read(d, cdn);
      cdn.advance(600);
      return {before, during, second_reader, after_ttl: await read(d, cdn)};
    }));
    for (const keyMode of ['full', 'path']) {
      results.push(await scenario(d, 'S4-' + keyMode, keyMode, async (cdn, pub) => {  // CDN com/sem query string na chave
        pub(1);
        const before = await read(d, cdn, 10);   // t=0 ponteiro; t=10 corpos
        cdn.advance(30); pub(2); cdn.advance(30); pub(3);
        cdn.advance(530);                        // t=600: ponteiro expirado, corpos ainda frescos até t=610
        return {before, after_pointer_expiry: await read(d, cdn)};
      }));
    }
    results.push(await scenario(d, 'S5', 'full', async (cdn, pub, origin) => {  // corpo atual corrompido na origem
      pub(1); cdn.advance(60); pub(2);
      const pointer = JSON.parse(fs.readFileSync(path.join(origin, POINTER)));
      const corrupted = pointer.current.files.signals.path;
      fs.writeFileSync(path.join(origin, corrupted), '{}');
      const shared = pointer.previous && pointer.previous.files.signals.path === corrupted;
      const first = await read(d, cdn);
      cdn.purge();                              // a CDN pode ter guardado o corpo corrompido: isolar a reparação
      cdn.advance(60); pub(3);
      return {read: first, shared_with_previous: !!shared, after_next_publish: await read(d, cdn)};
    }));
  }
  return results;
}

if (import.meta.url === 'file://' + process.argv[1]) {
  const i = process.argv.indexOf('--out');
  const out = i > 0 ? process.argv[i + 1] : path.join(HERE, 'resultados-staging.json');
  const res = await drill();
  fs.writeFileSync(out, JSON.stringify({generated_by: 'ops/staging/drill.mjs', ttl_s: 600, results: res}, null, 1) + '\n');
  for (const r of res) console.log(JSON.stringify(r));
}

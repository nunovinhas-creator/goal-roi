// Ensaio de staging HTTPS local dos snapshots V7 (PR #103). Custo 0; nada sai da máquina; nada é publicado.
//
//   node ops/staging/drill.mjs [--out ops/staging/resultados-staging.json]
//
// Cada cenário usa uma origem temporária nova, publicada com o publicador V7 e o builder real, servida por HTTPS
// (CA efémera) através de uma CDN simulada (max-age=600). O cliente é client_integrity.mjs, com SHA-256.
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import {fileURLToPath} from 'node:url';
import {loadVerified} from '../../audits/v7/snapshots/client_integrity.mjs';

const require = createRequire(import.meta.url);
const {start} = require('./https_cdn.cjs');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

function publish(origin, rev) {
  const r = execFileSync('python3', [path.join(HERE, 'publish_rev.py'), '--out', origin, '--rev', String(rev)],
    {cwd: ROOT, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, timeout: 120000});
  return JSON.parse(r.toString());
}

// gap: segundos simulados entre obter o ponteiro e pedir os corpos (latência real de um cliente).
async function read(cdn, gap = 0) {
  const pointer = JSON.parse(await cdn.get('dashboard-manifest.json'));
  cdn.advance(gap);
  const got = await loadVerified(pointer, p => cdn.get(p));
  return got ? {which: got.which, slot: got.revision.slot, generated: got.revision.report_generated_at_utc} : {which: 'full_report'};
}

async function scenario(name, keyMode, fn) {
  const origin = fs.mkdtempSync(path.join(os.tmpdir(), 'goal-roi-origin-'));
  const cdn = await start({root: origin, ttl: 600, keyMode});
  try { return {name, keyMode, ...(await fn(cdn, origin)), stats: {hits: cdn.stats.hits, misses: cdn.stats.misses, poisoned_keys: cdn.stats.poisoned_keys}}; }
  finally { await cdn.close(); fs.rmSync(origin, {recursive: true, force: true}); }
}

const untrusted = base => new Promise(resolve => {
  https.get(base + '/dashboard-manifest.json', {rejectUnauthorized: true}, () => resolve('aceite')).on('error', e => resolve(e.code || 'erro'));
});

export async function drill() {
  const results = [];
  results.push(await scenario('S1 TLS e leitura base', 'full', async (cdn, origin) => {
    publish(origin, 1);
    return {tls_without_ca: await untrusted(cdn.base), read: await read(cdn)};
  }));
  results.push(await scenario('S2 ponteiro em cache, 1 publicação dentro do TTL', 'full', async (cdn, origin) => {
    publish(origin, 1); publish(origin, 2);
    const before = await read(cdn);
    cdn.advance(120); publish(origin, 3); cdn.advance(120);
    return {before, after: await read(cdn)};
  }));
  results.push(await scenario('S3 ponteiro e corpos na mesma cache, 2 publicações dentro do TTL (A-B-A)', 'full', async (cdn, origin) => {
    publish(origin, 1); publish(origin, 2);
    const before = await read(cdn);
    cdn.advance(120); publish(origin, 3); cdn.advance(120); publish(origin, 4); cdn.advance(120);
    const during = await read(cdn);
    cdn.advance(600);
    return {before, during, after_ttl: await read(cdn)};
  }));
  results.push(await scenario('S3b ponteiro atrasado noutro nível de cache (corpos sem cache), 2 publicações', 'full', async (cdn, origin) => {
    publish(origin, 1); publish(origin, 2);
    const before = await read(cdn);
    // Ponteiro mantido (ex.: cache do navegador ou outro nó), corpos despejados (outro nó da CDN ou LRU).
    cdn.purge(k => k === '/dashboard-manifest.json');
    cdn.advance(120); publish(origin, 3); cdn.advance(120); publish(origin, 4); cdn.advance(120);
    const during = await read(cdn);
    const poisoned_after_during = cdn.stats.poisoned_keys;
    const second_reader = await read(cdn);         // outro leitor com o mesmo ponteiro: chave já envenenada
    cdn.advance(600);
    return {before, during, second_reader, poisoned_after_during, after_ttl: await read(cdn)};
  }));
  for (const keyMode of ['full', 'path']) {
    // Ponteiro e corpos guardados em instantes diferentes (10 s): o ponteiro expira primeiro.
    results.push(await scenario('S4 chave da CDN ' + (keyMode === 'full' ? 'com' : 'sem') + ' query string', keyMode, async (cdn, origin) => {
      publish(origin, 1);
      const before = await read(cdn, 10);          // t=0 ponteiro r1 (a/); t=10 corpos a/ r1
      publish(origin, 2); publish(origin, 3);      // r3 volta a a/
      cdn.advance(590);                            // t=600: ponteiro expirado, corpos a/ frescos até t=610
      return {before, after_pointer_expiry: await read(cdn)};
    }));
  }
  results.push(await scenario('S5 corpo atual corrompido na origem: recuo para a anterior', 'full', async (cdn, origin) => {
    publish(origin, 1); publish(origin, 2);
    const pointer = JSON.parse(fs.readFileSync(path.join(origin, 'dashboard-manifest.json')));
    fs.writeFileSync(path.join(origin, pointer.current.files.signals.path), '{}');
    return {read: await read(cdn)};
  }));
  return results;
}

if (import.meta.url === 'file://' + process.argv[1]) {
  const i = process.argv.indexOf('--out');
  const out = i > 0 ? process.argv[i + 1] : path.join(HERE, 'resultados-staging.json');
  const res = await drill();
  fs.writeFileSync(out, JSON.stringify({generated_by: 'ops/staging/drill.mjs', ttl_s: 600, results: res}, null, 1) + '\n');
  console.log(JSON.stringify(res, null, 1));
}

// Auditoria adversarial do cliente (PR #105): C1–C3 contra o cliente V7.2 (client_integrity_v2.mjs). Sem "todo".
//   node --test tests/test_v7_adversarial_client.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadVerified, NAMES, createMonotonicReader} from '../audits/v7/snapshots/client_integrity_v2.mjs';
import {sha256Hex} from '../audits/v7/snapshots/client_integrity.mjs';

async function casRevision(tag, store) {
  const files = {};
  for (const n of NAMES) {
    const body = JSON.stringify({n, tag});
    const sha = await sha256Hex(body);
    const path = 'objects/' + sha + '/dashboard-' + n + '.json';
    store.set(path, body);
    files[n] = {path, sha256: sha, bytes: body.length};
  }
  return {layout: 'cas', files};
}

const fetcher = (store, requested = []) => async p => { requested.push(p); if (!store.has(p)) throw new Error('404 ' + p); return store.get(p); };

// C1 — caminhos restritos ao layout e ao SHA-256 declarado; nada fora disso é pedido.
test('C1 caminhos do manifesto restritos a objects/<sha256>/dashboard-<nome>.json', async () => {
  for (const evil of ['https://example.invalid/x.json', '../report.json', 'objects/' + '0'.repeat(64) + '/dashboard-summary.json',
    'objects/%s/dashboard-signals.json', 'objects/abc/dashboard-summary.json?v=1']) {
    const store = new Map();
    const rev = await casRevision(1, store);
    rev.files.summary.path = evil.replace('%s', rev.files.signals.sha256);
    const requested = [];
    const got = await loadVerified({history: [rev]}, fetcher(store, requested));
    assert.equal(got, null, evil);
    assert.deepEqual(requested, [], 'pedido feito apesar do caminho inválido: ' + evil);
  }
  const store = new Map();
  const rev = await casRevision(1, store);
  const requested = [];
  assert.ok(await loadVerified({history: [rev]}, fetcher(store, requested)));
  assert.ok(requested.every(p => /^objects\/[0-9a-f]{64}\/dashboard-[a-z]+\.json$/.test(p)), requested[0]);
});

// C2 — ponteiro atrasado duas (ou mais) publicações: lê o que o ponteiro descreve; se já foi recolhido, relê o ponteiro.
test('C2 ponteiro atrasado: recupera sem relatório completo', async () => {
  const store = new Map();
  const r1 = await casRevision(1, store), r2 = await casRevision(2, store);
  const stale = {history: [r2, r1]};
  const r3 = await casRevision(3, store), r4 = await casRevision(4, store);
  const fresh = {history: [r4, r3]};
  const kept = await loadVerified(stale, fetcher(store));
  assert.equal(kept.which, 'current');
  assert.equal(kept.refetched, false);
  for (const r of [r1, r2]) for (const n of NAMES) store.delete(r.files[n].path); // recolha de lixo depois da retenção
  assert.equal(await loadVerified(stale, fetcher(store)), null);                   // sem releitura: relatório completo
  const got = await loadVerified(stale, fetcher(store), undefined, async () => fresh);
  assert.equal(got.refetched, true);
  assert.equal(got.files.summary.tag, 4);
});

// C3 — corpo de outra revisão no mesmo caminho é recusado (tudo-ou-nada), e o recuo usa a anterior íntegra.
test('C3 corpo adulterado é recusado e o recuo usa a anterior', async () => {
  const store = new Map();
  const r1 = await casRevision(1, store), r2 = await casRevision(2, store);
  store.set(r2.files.science.path, JSON.stringify({n: 'science', tag: 99}));
  const got = await loadVerified({history: [r2, r1]}, fetcher(store));
  assert.equal(got.which, 'older');
  assert.equal(got.files.science.tag, 1);
});

test('C4 paths with trailing newline and invalid metadata rejected before requests', async () => {
  for (const mutate of [
    r => { r.files.summary.path += '\n'; },
    r => { r.files.summary.bytes = -1; },
    r => { r.files.summary.sha256 = [r.files.summary.sha256]; },
    r => { r.files.extra = r.files.summary; }
  ]) {
    const store = new Map(), requested = [];
    const revision = await casRevision(1, store);
    mutate(revision);
    assert.equal(await loadVerified({history: [revision]}, fetcher(store, requested)), null);
    assert.deepEqual(requested, []);
  }
});

test('C5 declared byte count must match even with a valid digest', async () => {
  const store = new Map();
  const rev = await casRevision(1, store);
  rev.files.summary.bytes += 1;
  assert.equal(await loadVerified({history: [rev]}, fetcher(store)), null);
});


// Exact chronological revision labels from staging run 45836535343, 26 Nov 2026.
// Fixture bodies are synthetic; this replays observed ordering, not network timing.
const observed = [6,7,7,7,7,7,7,7,7,7,7,8,7,7,8,8,8,8,8,8,8,9,8,9,9,9,9,9,9,9,9,10,10,9,10,10,10,10,10,10,10,11,10,11,10,11,11,11,11,11,11,11,12,11,12,12,12,12,12,12,12,13,13,13,13,13,13,13,13,13,13,14,14,14,14,14,14,14,14,14,14,15,15,15,14,14,15,15,14,15,14,15,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16,16];
function stamp(r, n) {
  return Object.assign(r, {published_at: n * 600,
    report_generated_at_utc: new Date(Date.UTC(2030, 0, 1) + n * 1200000).toISOString()});
}
test('C6 replay 120 CDN observations: reject all 11 regressions without losing verified content', async () => {
  const store = new Map(), revisions = new Map();
  for (let n = 6; n <= 16; n++) revisions.set(n, stamp(await casRevision(n, store), n));
  const reader = createMonotonicReader();
  let max = 0, retained = 0;
  for (const n of observed) {
    const got = await reader.read({history: [revisions.get(n)]}, fetcher(store), undefined,
      async () => ({history: [revisions.get(n)]}));
    max = Math.max(max, n);
    assert.equal(got.files.summary.tag, max);
    assert.equal(got.revision.published_at, max * 600);
    assert.equal(got.observed_published_at, n * 600);
    if (n < max) {
      retained++;
      assert.equal(got.which, 'retained');
      assert.equal(got.stale, true);
      assert.equal(got.reason, 'regression');
    }
  }
  assert.equal(observed.length, 120);
  assert.equal(retained, 11);
});
test('C7 stale pointer refetch recovers; equal timestamp conflict and corrupt content never replace accepted bundle', async () => {
  const store = new Map();
  const r1 = stamp(await casRevision(1, store), 1), r2 = stamp(await casRevision(2, store), 2),
    r3 = stamp(await casRevision(3, store), 3);
  const reader = createMonotonicReader();
  await reader.read({history: [r2]}, fetcher(store));
  const got = await reader.read({history: [r1]}, fetcher(store), undefined, async () => ({history: [r3]}));
  assert.equal(got.files.summary.tag, 3);
  assert.equal(got.refetched, true);
  const conflict = stamp(await casRevision(99, store), 3);
  assert.equal((await reader.read({history: [conflict]}, fetcher(store))).reason, 'conflict');
  const r4 = stamp(await casRevision(4, store), 4);
  store.delete(r4.files.science.path);
  assert.equal((await reader.read({history: [r4]}, fetcher(store))).files.summary.tag, 3);
});
test('C8 reload revalidates stored bundle; offline retention is labelled stale', async () => {
  const map = new Map(), storage = {getItem: k => map.get(k) ?? null, setItem: (k,v) => map.set(k,v)};
  const store = new Map(), r2 = stamp(await casRevision(2, store), 2);
  const first = createMonotonicReader({storage});
  const got = await first.read({history: [r2]}, fetcher(store));
  got.files.summary.tag = 999;
  const next = createMonotonicReader({storage});
  const restored = await next.read(null, fetcher(new Map()));
  assert.equal(restored.files.summary.tag, 2);
  assert.equal(restored.which, 'retained');
  assert.equal(restored.stale, true);
  const [key, raw] = [...map][0];
  const bad = JSON.parse(raw); bad.bodies[r2.files.summary.path] = '{}';
  map.set(key, JSON.stringify(bad));
  const broken = await createMonotonicReader({storage}).read(null, fetcher(store));
  assert.equal(broken.which, 'unavailable');
  assert.equal(broken.storage_error, true);
});
test('C9 concurrent reads and source timestamp regression cannot roll back visible data', async () => {
  const store = new Map(), r1 = stamp(await casRevision(1, store), 1), r2 = stamp(await casRevision(2, store), 2);
  const reader = createMonotonicReader();
  const [a,b] = await Promise.all([reader.read({history:[r2]},fetcher(store)),reader.read({history:[r1]},fetcher(store))]);
  assert.equal(a.files.summary.tag, 2); assert.equal(b.files.summary.tag, 2);
  const r3 = stamp(await casRevision(3, store), 3);
  r3.report_generated_at_utc = r1.report_generated_at_utc;
  assert.equal((await reader.read({history:[r3]},fetcher(store))).reason, 'regression');
});


test('C10 HTTPS probe reports observed regression separately from retained visible revision', async () => {
  const {sampleVisible} = await import('../ops/staging/remote/measure_remote.mjs');
  const store = new Map(), r1 = stamp(await casRevision(1, store), 1), r2 = stamp(await casRevision(2, store), 2);
  let remote = r2, reloads = 0;
  const fetchImpl = async (url, options) => {
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit');
    if (options.cache === 'reload') reloads++;
    const path = url.split('/snapshots/')[1];
    return path === 'dashboard-manifest.json' ? new Response(JSON.stringify({history:[remote]})) :
      new Response(store.get(path), {status: store.has(path) ? 200 : 404});
  };
  const reader = createMonotonicReader(), base = 'https://example.github.io/goal-roi-staging';
  const first = await sampleVisible(base, reader, fetchImpl);
  assert.equal(first.visible_published_at, 1200);
  remote = r1;
  const second = await sampleVisible(base, reader, fetchImpl);
  assert.equal(second.observed_published_at, 600);
  assert.equal(second.visible_published_at, 1200);
  assert.equal(second.which, 'retained');
  assert.equal(second.stale, true);
  assert.equal(reloads, 1);
});


test('C11 shared storage is reread under lock; older tabs cannot overwrite newer bundle', async () => {
  const map = new Map(), storage = {getItem: k => map.get(k) ?? null, setItem: (k,v) => map.set(k,v)};
  let tail = Promise.resolve();
  const lock = fn => { const job = tail.then(fn); tail = job.catch(() => {}); return job; };
  const store = new Map(), r1 = stamp(await casRevision(1, store), 1), r2 = stamp(await casRevision(2, store), 2);
  const a = createMonotonicReader({storage, lock}), b = createMonotonicReader({storage, lock});
  await a.read({history:[r1]}, fetcher(store));
  await b.read({history:[r2]}, fetcher(store));
  const got = await a.read({history:[r1]}, fetcher(store));
  assert.equal(got.files.summary.tag, 2);
  assert.equal(got.stale, true);
  const fresh = await createMonotonicReader({storage, lock}).read(null, fetcher(store));
  assert.equal(fresh.files.summary.tag, 2);
});

test('C12 storage failure degrades explicitly; lock failure does not write unprotected', async () => {
  const store = new Map(), r1 = stamp(await casRevision(1, store), 1);
  const broken = {getItem: () => null, setItem: () => { throw Error('quota'); }};
  const got = await createMonotonicReader({storage:broken}).read({history:[r1]}, fetcher(store));
  assert.equal(got.storage_error, true);
  assert.equal(got.freshness, 'unverified');
  let writes = 0;
  const unavailable = await createMonotonicReader({
    storage:{getItem:()=>null,setItem:()=>writes++}, lock: async () => { throw Error('lock'); }
  }).read({history:[r1]}, fetcher(store));
  assert.equal(unavailable.which, 'unavailable');
  assert.equal(unavailable.storage_error, true);
  assert.equal(writes, 0);
});

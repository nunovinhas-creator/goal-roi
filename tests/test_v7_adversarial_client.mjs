// Auditoria adversarial do cliente (PR #105): C1–C3 contra o cliente V7.2 (client_integrity_v2.mjs). Sem "todo".
//   node --test tests/test_v7_adversarial_client.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadVerified, NAMES} from '../audits/v7/snapshots/client_integrity_v2.mjs';
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

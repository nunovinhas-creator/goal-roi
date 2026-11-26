// Auditoria adversarial do cliente de integridade da PR #103 (audits/v7/snapshots/client_integrity.mjs).
// Testes "todo" documentam defeitos: correm, mas não fazem falhar a suite. Quando corrigidos, passam a "ok"
// e retira-se a marca todo.
//   node --test tests/test_v7_adversarial_client.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {loadVerified, sha256Hex, NAMES} from '../audits/v7/snapshots/client_integrity.mjs';

async function revision(slot, tag, store) {
  const files = {};
  for (const n of NAMES) {
    const body = JSON.stringify({n, tag});
    const path = slot + '/dashboard-' + n + '.json';
    store.set(path, body);
    files[n] = {path, sha256: await sha256Hex(body), bytes: body.length};
  }
  return {slot, files};
}

// C1 — o caminho vem do manifesto e é pedido tal como está: URL absoluta ou "../" são aceites.
test('C1 caminhos do manifesto restritos a a|b/dashboard-<nome>.json', {todo: 'cliente pede qualquer caminho'}, async () => {
  const store = new Map();
  const rev = await revision('a', 1, store);
  const requested = [];
  rev.files.summary.path = 'https://example.invalid/x.json';
  const fetchText = async p => { requested.push(p); const k = p.split('?')[0]; if (!store.has(k)) throw new Error('404'); return store.get(k); };
  await loadVerified({current: rev, previous: null}, fetchText);
  assert.ok(requested.every(p => /^[ab]\/dashboard-[a-z]+\.json\?v=[0-9a-f]{16}$/.test(p)), 'pedido fora do layout: ' + requested[0]);
});

// C2 — A-B-A: o ponteiro em cache CDN descreve ranhuras já reescritas; o cliente não volta a pedir o ponteiro.
test('C2 ponteiro atrasado duas publicações: recupera relendo o ponteiro', {todo: 'loadVerified não relê o ponteiro'}, async () => {
  const store = new Map();
  const r1 = await revision('a', 1, store), r2 = await revision('b', 2, store);
  const stale = {current: r2, previous: r1};
  const r3 = await revision('a', 3, store), r4 = await revision('b', 4, store);
  const fresh = {current: r4, previous: r3};
  const fetchText = async p => store.get(p.split('?')[0]);
  const got = await loadVerified(stale, fetchText);
  assert.ok(got, 'sem revisão: o painel descarrega o relatório completo (~4 MB) apesar de existir ' + JSON.stringify(Object.keys(fresh)));
});

// C3 — invariante que hoje se verifica: corpo de outra revisão com o mesmo caminho é sempre recusado.
test('C3 corpo de outra revisão no mesmo caminho é recusado (tudo-ou-nada)', async () => {
  const store = new Map();
  const r1 = await revision('a', 1, store);
  await revision('a', 3, store); // sobrescreve a/
  const got = await loadVerified({current: r1, previous: null}, async p => store.get(p.split('?')[0]));
  assert.equal(got, null);
});

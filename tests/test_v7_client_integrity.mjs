import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {sha256Pure, sha256Hex, loadVerified, NAMES} from '../audits/v7/snapshots/client_integrity.mjs';

const hex = s => createHash('sha256').update(s).digest('hex');

test('implementação pura coincide com node:crypto', () => {
  for (const s of ['', 'abc', 'x'.repeat(55), 'x'.repeat(56), 'x'.repeat(64), 'ç€😀'.repeat(1000)]) {
    assert.equal(sha256Pure(new TextEncoder().encode(s)), hex(s));
  }
});

function fixture() {
  const bodies = {}, mk = (slot, rev) => ({slot, files: Object.fromEntries(NAMES.map(n => {
    const path = `${slot}/dashboard-${n}.json`, body = JSON.stringify({kind: n, rev});
    bodies[path] = body; return [n, {path, sha256: hex(body), bytes: body.length}];
  }))});
  const pointer = {current: mk('b', 2), previous: mk('a', 1)};
  return {pointer, bodies, fetchText: async p => { const k = p.split('?')[0]; if (!(k in bodies)) throw new Error('404'); return bodies[k]; }};
}

test('sem crypto.subtle continua a verificar por hash e recusa adulteração de igual comprimento', async () => {
  const fx = fixture();
  const ok = await loadVerified(fx.pointer, fx.fetchText, null);
  assert.equal(ok.which, 'current');
  const p = 'b/dashboard-summary.json';
  fx.bodies[p] = fx.bodies[p].replace('"rev":2', '"rev":3');
  assert.equal(fx.bodies[p].length, JSON.stringify({kind: 'summary', rev: 2}).length);
  const fb = await loadVerified(fx.pointer, fx.fetchText, null);
  assert.equal(fb.which, 'previous'); // tudo-ou-nada: recua para a revisão anterior íntegra
  assert.equal(fb.files.summary.rev, 1);
});

test('sem revisão íntegra devolve null (relatório completo)', async () => {
  const fx = fixture();
  for (const k of Object.keys(fx.bodies)) if (k.endsWith('learning.json')) fx.bodies[k] = '{}';
  assert.equal(await loadVerified(fx.pointer, fx.fetchText, globalThis.crypto.subtle), null);
});

test('subtle e implementação pura dão o mesmo resultado', async () => {
  const s = 'snapshot'.repeat(5000);
  assert.equal(await sha256Hex(s, globalThis.crypto.subtle), await sha256Hex(s, null));
});

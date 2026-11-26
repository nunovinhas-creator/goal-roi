// Cliente V7.2 (proposta, não ligada ao V6.1): verificação dos snapshots do publisher_v2.py só por SHA-256.
// Corrige C1 e C2 da auditoria adversarial (PR #105):
// - C1: cada caminho do manifesto tem de ser objects/<sha256>/dashboard-<nome>.json (ou s<i>/dashboard-<nome>.json) e, em cas,
//   o SHA-256 do nome tem de ser o declarado. URLs absolutas, "../" e query strings são recusadas antes de qualquer pedido.
// - C2: percorre o histórico do ponteiro (current, previous, …); se nada for íntegro, volta a pedir o ponteiro uma vez
//   (refetchPointer, com cache: 'no-cache' no navegador) antes de recorrer ao relatório completo.
import {sha256Hex} from './client_integrity.mjs';

export const NAMES = ['summary', 'signals', 'performance', 'science', 'operations', 'learning'];
const N = NAMES.join('|');
const CAS = new RegExp('^objects/([0-9a-f]{64})/dashboard-(' + N + ')\\.json$');
const SLOT = new RegExp('^s[0-9]/dashboard-(' + N + ')\\.json$');

export function safePath(revision, name) {
  const entry = revision && revision.files && revision.files[name];
  if (!entry || typeof entry.path !== 'string' || typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256) || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0) return null;
  if (revision.layout === 'cas') {
    const m = CAS.exec(entry.path);
    return m && m[0] === entry.path && m[2] === name && m[1] === entry.sha256 ? entry.path : null;
  }
  if (revision.layout === 'slots') {
    const m = SLOT.exec(entry.path);
    return m && m[0] === entry.path && m[1] === name ? entry.path : null;
  }
  return null;
}

export async function verifyRevision(revision, fetchText, subtle) {
  if (!revision || typeof revision !== 'object') return null;
  if (!revision.files || Object.keys(revision.files).length !== NAMES.length) return null;
  const paths = {};
  for (const name of NAMES) {                      // valida todos os caminhos antes do primeiro pedido
    const p = safePath(revision, name);
    if (!p) return null;
    paths[name] = p;
  }
  if (revision.layout === 'slots' && new Set(Object.values(paths).map(p => p.split('/')[0])).size !== 1) return null;
  const out = {};
  for (const name of NAMES) {
    let body;
    try { body = await fetchText(paths[name]); } catch (e) { return null; }
    if (typeof body !== 'string' || new TextEncoder().encode(body).length !== revision.files[name].bytes || await sha256Hex(body, subtle) !== revision.files[name].sha256) return null;
    try { out[name] = JSON.parse(body); } catch (e) { return null; }
  }
  return out;
}

async function firstIntact(pointer, fetchText, subtle) {
  const history = pointer && Array.isArray(pointer.history) ? pointer.history : [pointer && pointer.current, pointer && pointer.previous];
  for (let i = 0; i < history.length; i++) {
    const files = await verifyRevision(history[i], fetchText, subtle);
    if (files) return {which: i === 0 ? 'current' : 'older', index: i, revision: history[i], files};
  }
  return null;
}

// refetchPointer: () => Promise<pointer>; no navegador, fetch('dashboard-manifest.json', {cache: 'no-cache'}).
export async function loadVerified(pointer, fetchText, subtle, refetchPointer) {
  const got = await firstIntact(pointer, fetchText, subtle);
  if (got || !refetchPointer) return got ? Object.assign(got, {refetched: false}) : null;
  let fresh;
  try { fresh = await refetchPointer(); } catch (e) { return null; }
  const again = await firstIntact(fresh, fetchText, subtle);
  return again ? Object.assign(again, {refetched: true}) : null; // null: usar o relatório completo
}


// V7 staging: session-scoped high-water mark, not a guarantee of CDN freshness.
// Store the complete verified bundle, never combine files from different revisions.
// Retained data is explicitly stale; a cold session cannot know unseen revisions.
export function createMonotonicReader({storage = null, key = 'v7-staging-verified-v1', lock = null} = {}) {
  let accepted = null, hydrated = false, tail = Promise.resolve(), storageError = false;
  const identity = r => JSON.stringify([r.published_at, r.report_generated_at_utc,
    ...NAMES.map(n => [r.files[n].sha256, r.files[n].bytes])]);
  const metadata = r => r && typeof r.published_at === 'number' &&
    Number.isFinite(r.published_at) && r.published_at >= 0 &&
    typeof r.report_generated_at_utc === 'string' &&
    Number.isFinite(Date.parse(r.report_generated_at_utc));
  const compare = r => {
    if (!metadata(r)) return 'invalid';
    if (!accepted) return 'new';
    const a = accepted.revision;
    if (r.published_at < a.published_at ||
        Date.parse(r.report_generated_at_utc) < Date.parse(a.report_generated_at_utc)) return 'regression';
    if (r.published_at === a.published_at) return identity(r) === identity(a) ? 'same' : 'conflict';
    return 'new';
  };
  async function restore(subtle) {
    if (hydrated && !lock) return;
    hydrated = true;
    if (!storage) return;
    try {
      const raw = storage.getItem(key);
      if (raw === null) return;
      const saved = JSON.parse(raw);
      if (!metadata(saved.revision)) throw new Error('invalid cached metadata');
      const files = await verifyRevision(saved.revision, async p => saved.bodies[p], subtle);
      if (!files) throw new Error('invalid cached bundle');
      const state = compare(saved.revision);
      if (state === 'new' || state === 'same') accepted = {revision: saved.revision, files, bodies: saved.bodies};
      else if (state === 'conflict') storageError = true;
    } catch { storageError = true; }
  }
  async function candidate(pointer, fetchText, subtle) {
    const history = pointer && Array.isArray(pointer.history) ? pointer.history :
      [pointer && pointer.current, pointer && pointer.previous];
    for (let i = 0; i < history.length; i++) {
      const revision = history[i];
      if (!metadata(revision)) continue;
      const bodies = Object.create(null);
      let files;
      try {
        files = await verifyRevision(revision, async p => {
          const body = await fetchText(p); bodies[p] = body; return body;
        }, subtle);
      } catch { continue; }
      if (!files) continue;
      const state = compare(revision);
      if (state === 'regression' || state === 'conflict') return {state, observed: revision.published_at};
      if (state === 'invalid') continue;
      return {state, observed: revision.published_at, entry: {revision, files, bodies}, index: i};
    }
    return {state: 'unavailable', observed: null};
  }
  function expose(which, observed, refetched, reason) {
    return {which, observed_published_at: observed, refetched, reason, storage_error: storageError,
      stale: which !== 'current', freshness: 'unverified', revision: accepted ? structuredClone(accepted.revision) : null,
      files: accepted ? structuredClone(accepted.files) : null};
  }
  async function run(pointer, fetchText, subtle, refetchPointer) {
    await restore(subtle);
    const initial = await candidate(pointer, fetchText, subtle);
    let got = initial, refetched = false;
    // Retry on a valid but regressed/conflicting pointer as well as an invalid one.
    if (!got.entry && refetchPointer) {
      refetched = true;
      try { got = await candidate(await refetchPointer(), fetchText, subtle); }
      catch { got = {state: 'unavailable', observed: null}; }
    }
    if (!got.entry) return expose(accepted ? 'retained' : 'unavailable', initial.observed,
      refetched, initial.state);
    accepted = structuredClone(got.entry);
    if (storage) {
      try {
        storage.setItem(key, JSON.stringify({revision: accepted.revision, bodies: accepted.bodies}));
      } catch { storageError = true; }
    }
    return expose(got.index === 0 ? 'current' : 'older', initial.observed, refetched,
      refetched ? initial.state : null);
  }
  return {
    read(pointer, fetchText, subtle, refetchPointer) {
      // Serialize overlapping refreshes: a slow older request cannot undo a newer one.
      const work = tail.then(async () => {
        if (!lock) return run(pointer, fetchText, subtle, refetchPointer);
        try { return await lock(() => run(pointer, fetchText, subtle, refetchPointer)); }
        catch {
          storageError = true;
          // Never write shared storage without the lock. Keep a verified in-memory bundle if available.
          return expose(accepted ? 'retained' : 'unavailable', null, false, 'lock_unavailable');
        }
      });
      tail = work.catch(() => {});
      return work;
    }
  };
}

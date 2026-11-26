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

// Proposta V7 (não ligada ao V6.1 nem à PR #98): verificação de snapshots no cliente só por SHA-256.
// Corrige o caminho da PR #98 que, sem crypto.subtle (contexto não seguro), aceitava um corpo pelo comprimento.
// - sha256Hex usa crypto.subtle quando existe e, caso contrário, uma implementação pura (FIPS 180-4).
// - verifyRevision aplica tudo-ou-nada: os seis corpos têm de coincidir; senão tenta a revisão anterior do ponteiro;
//   senão devolve null e o painel usa o relatório completo.

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

export function sha256Pure(bytes) {
  const len = bytes.length, total = ((len + 9 + 63) >> 6) << 6;
  const buf = new Uint8Array(total);
  buf.set(bytes); buf[len] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(total - 8, Math.floor(len / 0x20000000)); view.setUint32(total - 4, (len << 3) >>> 0);
  const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const W = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) W[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
      const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
  }
  return [...H].map(x => x.toString(16).padStart(8, '0')).join('');
}

export async function sha256Hex(text, subtle = globalThis.crypto && globalThis.crypto.subtle) {
  const bytes = new TextEncoder().encode(text);
  if (subtle) {
    try {
      const buf = await subtle.digest('SHA-256', bytes);
      return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
    } catch (e) { /* recorre à implementação pura */ }
  }
  return sha256Pure(bytes);
}

export const NAMES = ['summary', 'signals', 'performance', 'science', 'operations', 'learning'];

// fetchText(path) devolve o corpo; base é a pasta do ponteiro. Nunca aceita comprimento sem hash.
export async function verifyRevision(revision, fetchText, subtle) {
  if (!revision || typeof revision !== 'object' || !revision.files) return null;
  const out = {};
  for (const name of NAMES) {
    const entry = revision.files[name];
    if (!entry || typeof entry.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(entry.sha256)) return null;
    let body;
    try { body = await fetchText(entry.path + '?v=' + entry.sha256.slice(0, 16)); } catch (e) { return null; }
    if (await sha256Hex(body, subtle) !== entry.sha256) return null;
    try { out[name] = JSON.parse(body); } catch (e) { return null; }
  }
  return out;
}

export async function loadVerified(pointer, fetchText, subtle) {
  for (const key of ['current', 'previous']) {
    const files = await verifyRevision(pointer && pointer[key], fetchText, subtle);
    if (files) return {which: key, revision: pointer[key], files};
  }
  return null; // usar o relatório completo
}

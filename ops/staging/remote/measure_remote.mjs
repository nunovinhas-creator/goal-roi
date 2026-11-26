// Medição do staging remoto (opção A). SÓ depois de autorização e publicação do repositório de staging.
//   node ops/staging/remote/measure_remote.mjs --base https://<utilizador>.github.io/<repo-staging> [--minutes 120] [--every 60]
// Regista, a cada intervalo: cabeçalhos de cache do ponteiro (age, x-cache, x-served-by, cache-control, etag), o resultado do
// cliente V7.2 (current/previous/full_report, releitura) e a resposta da CDN a uma query string nova num objeto (a chave
// inclui a query? compara x-cache/age). Só pedidos GET ao repositório de staging; sem credenciais.
import {loadVerified} from '../../../audits/v7/snapshots/client_integrity_v2.mjs';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const base = arg('base');
if (!base || !/^https:\/\/[a-z0-9-]+\.github\.io\//.test(base + '/')) { console.error('indique --base https://<utilizador>.github.io/<repo>'); process.exit(2); }
const minutes = Number(arg('minutes', 120)), every = Number(arg('every', 60));
const head = r => Object.fromEntries(['age', 'x-cache', 'x-served-by', 'cache-control', 'etag'].map(h => [h, r.headers.get(h)]));
const get = async p => { const r = await fetch(base + '/snapshots/' + p); if (!r.ok) throw new Error(r.status + ' ' + p); return {text: await r.text(), h: head(r)}; };
for (let t = 0; t <= minutes * 60; t += every) {
  const t0 = Date.now();
  const ptr = await get('dashboard-manifest.json');
  const pointer = JSON.parse(ptr.text);
  const got = await loadVerified(pointer, async p => (await get(p)).text, undefined, async () => JSON.parse((await get('dashboard-manifest.json')).text));
  const obj = pointer.current.files.summary.path;
  const a = await get(obj), b = await get(obj + '?probe=' + t0);
  console.log(JSON.stringify({at: new Date(t0).toISOString(), pointer_headers: ptr.h, which: got ? got.which : 'full_report',
    refetched: got ? got.refetched : null, ms: Date.now() - t0, object_headers: a.h, query_variant_headers: b.h}));
  await new Promise(r => setTimeout(r, every * 1000));
}

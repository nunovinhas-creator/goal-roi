// Medidor de staging exclusivamente sintético. GET sem credenciais, sem redirects.
import {loadVerified} from '../../../audits/v7/snapshots/client_integrity_v2.mjs';
import {pathToFileURL} from 'node:url';

export function validateBase(value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || !/^[a-z0-9-]+\.github\.io$/.test(u.hostname) ||
      u.port || u.username || u.password || u.search || u.hash ||
      !/^\/[a-z0-9-]*staging[a-z0-9-]*\/?$/.test(u.pathname)) {
    throw new Error('indique um repositório separado de staging em github.io');
  }
  return u.origin + u.pathname.replace(/\/$/, '');
}

const head = r => Object.fromEntries(['age', 'x-cache', 'x-served-by', 'cache-control', 'etag'].map(h => [h, r.headers.get(h)]));

export async function sample(base, fetchImpl = fetch) {
  const t0 = Date.now(), errors = [];
  const result = {at: new Date(t0).toISOString(), which: 'unavailable', refetched: null, errors};
  const get = async (p, reload = false) => {
    const r = await fetchImpl(base + '/snapshots/' + p, {
      redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(10000),
      ...(reload ? {cache: 'no-cache'} : {})
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return {text: await r.text(), h: head(r)};
  };
  try {
    const ptr = await get('dashboard-manifest.json');
    result.pointer_headers = ptr.h;
    const pointer = JSON.parse(ptr.text);
    const got = await loadVerified(pointer, async p => {
      try { return (await get(p)).text; }
      catch (e) { errors.push('object: ' + e.message); throw e; }
    }, undefined, async () => {
      try { return JSON.parse((await get('dashboard-manifest.json', true)).text); }
      catch (e) { errors.push('refetch: ' + e.message); throw e; }
    });
    result.which = got ? got.which : 'full_report_required';
    result.refetched = got ? got.refetched : null;
    result.published_at = got ? got.revision.published_at : null;
    result.generated_at = got ? got.revision.report_generated_at_utc : null;
    // Só sondar caminhos que o cliente já tenha validado integralmente.
    if (got) {
      try {
        const obj = got.revision.files.summary.path;
        const a = await get(obj), b = await get(obj + '?probe=' + t0);
        result.object_headers = a.h;
        result.query_variant_headers = b.h;
      } catch (e) { errors.push('cache_probe: ' + e.message); }
    }
  } catch (e) { errors.push('sample: ' + e.message); }
  result.ms = Date.now() - t0;
  return result;
}


// Separate session-visible measurement. Do not substitute this for the raw CDN gate.
// A caller must retain the same reader for the entire measurement window.
export async function sampleVisible(base, reader, fetchImpl = fetch) {
  base = validateBase(base);
  const t0 = Date.now(), errors = [];
  const get = async (path, reload = false) => {
    const response = await fetchImpl(base + '/snapshots/' + path, {
      redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(10000),
      ...(reload ? {cache: 'reload'} : {})
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.text();
  };
  const pointer = async (reload = false) => {
    try { return JSON.parse(await get('dashboard-manifest.json', reload)); }
    catch (e) { errors.push('pointer: ' + e.message); return null; }
  };
  const got = await reader.read(await pointer(), async path => {
    try { return await get(path); }
    catch (e) { errors.push('object: ' + e.message); throw e; }
  }, undefined, () => pointer(true));
  return {at: new Date(t0).toISOString(), which: got.which, stale: got.stale,
    reason: got.reason, refetched: got.refetched,
    observed_published_at: got.observed_published_at,
    visible_published_at: got.revision?.published_at ?? null,
    generated_at: got.revision?.report_generated_at_utc ?? null,
    errors, ms: Date.now() - t0};
}

async function main() {
  const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
  let base;
  try { base = validateBase(arg('base')); }
  catch (e) { console.error(e.message); process.exitCode = 2; return; }
  const minutes = Number(arg('minutes', 120)), every = Number(arg('every', 60));
  if (!Number.isFinite(minutes) || minutes <= 0 || minutes > 120 ||
      !Number.isFinite(every) || every < 10 || every > 3600) {
    console.error('duração/intervalo inválidos'); process.exitCode = 2; return;
  }
  const end = Date.now() + minutes * 60000;
  do {
    console.log(JSON.stringify(await sample(base)));
    const remaining = end - Date.now();
    if (remaining <= 0) break;
    await new Promise(r => setTimeout(r, Math.min(every * 1000, remaining)));
  } while (Date.now() < end);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();

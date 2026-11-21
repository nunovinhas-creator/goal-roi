const repo = 'https://api.github.com/repos/nunovinhas-creator/goal-roi';
const workflow = repo + '/actions/workflows/collect.yml';
const threshold = 30 * 60;
const journalBytes = 1_800_000;
const journalKeys = new Set(['publication', 'ingestion']);
// Fail closed: both independent configuration switches must explicitly allow recovery.
const recoveryEnabled = env => env.RECOVERY_MODE === 'active' && env.RECOVERY_ENABLED === 'true';

export async function inspect(token, now = Date.now(), request = fetch) {
  const headers = {'User-Agent':'goal-roi-continuity'};
  if (token) headers.Authorization = 'Bearer ' + token;
  async function read(url, accept = 'application/vnd.github+json') {
    const response = await request(url, {method:'GET', headers:{...headers, Accept:accept},
      redirect:'manual', cache:'no-store', signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw Error('GitHub lookup HTTP ' + response.status);
    return response.json();
  }
  function age(value) {
    const seconds = Math.floor((now - Date.parse(value)) / 1000);
    if (!Number.isFinite(seconds) || seconds < 0) throw Error('Invalid GitHub timestamp');
    return seconds;
  }
  const state = await read(repo + '/contents/data/state.json?ref=main', 'application/vnd.github.raw+json');
  const {workflow_runs:runs} = await read(workflow + '/runs?branch=main&per_page=20');
  if (!runs.length) throw Error('No collector metadata');
  const latest = runs[0];
  if (!Number.isSafeInteger(latest.id)) throw Error('Invalid collector ID');
  const report = {collector_id:latest.id, last_read_utc:state.last_run_utc,
    age_seconds:age(state.last_run_utc), decision:'wait', reason:'recent_read'};
  if (report.age_seconds < threshold) return report;
  for (const run of runs) {
    if (!['completed','queued','pending','requested','waiting','in_progress'].includes(run.status))
      throw Error('Unknown collector state');
    if (age(run.updated_at) < threshold || age(run.run_started_at || run.created_at) < threshold)
      return {...report, reason:'recent_run'};
  }
  for (const run of runs.filter(r => r.status === 'in_progress')) {
    const {jobs} = await read(repo + '/actions/runs/' + run.id + '/jobs?filter=latest');
    if (!jobs.length) throw Error('Missing job metadata');
    if (jobs.some(j => age(j.completed_at || j.started_at || j.created_at) < threshold))
      return {...report, reason:'recent_job'};
  }
  return {...report, decision:'would_recover', reason:'stale_read_and_runs'};
}

// SQLite-backed singleton; a committed intent survives eviction and an ambiguous POST.
export class RecoveryGuard {
  constructor(ctx, env) { this.sql = ctx.storage.sql; this.env = env; }
  async fetch(request) {
    if (request && new URL(request.url).pathname === '/journal') return this.journal(request);
    if (!recoveryEnabled(this.env) || !this.env.GITHUB_ACTIONS_TOKEN)
      return Response.json({decision:'disabled'});
    const report = await inspect(this.env.GITHUB_ACTIONS_TOKEN);
    if (report.decision !== 'would_recover') return Response.json(report);
    this.sql.exec('CREATE TABLE IF NOT EXISTS intent (collector INTEGER PRIMARY KEY)');
    const claimed = this.sql.exec('INSERT OR IGNORE INTO intent VALUES (?) RETURNING collector', report.collector_id).toArray();
    if (!claimed.length) return Response.json({...report, decision:'already_requested'});
    const response = await fetch(workflow + '/dispatches', {method:'POST', redirect:'manual',
      headers:{Authorization:'Bearer ' + this.env.GITHUB_ACTIONS_TOKEN, 'User-Agent':'goal-roi-continuity',
        'Content-Type':'application/json'}, body:JSON.stringify({ref:'main'}), signal:AbortSignal.timeout(8000)});
    if ([400, 401, 403, 404, 422, 429].includes(response.status))
      this.sql.exec('DELETE FROM intent WHERE collector=?', report.collector_id);
    if (!response.ok) throw Error('Dispatch HTTP ' + response.status);
    return Response.json({...report, decision:'requested'});
  }

  async journal(request) {
    const headers = {'Cache-Control':'no-store'};
    const bad = status => new Response(null, {status, headers});
    let query = {key:new URL(request.url).searchParams.get('key')};
    if (request.method === 'PUT') {
      const size = Number(request.headers.get('Content-Length'));
      if (size > journalBytes) return bad(413);
      try {
        const body = await request.arrayBuffer();
        if (body.byteLength > journalBytes) return bad(413);
        query = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(body));
      } catch {
        return bad(400);
      }
      if (!query || !Number.isSafeInteger(query.revision) || query.revision < 0 ||
          !query.value || typeof query.value !== 'object' || Array.isArray(query.value)) return bad(400);
    } else if (request.method !== 'GET') return bad(405);
    if (!journalKeys.has(query.key)) return bad(400);

    this.sql.exec('CREATE TABLE IF NOT EXISTS journal (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL)');
    const current = this.sql.exec('SELECT revision, value FROM journal WHERE key = ?', query.key).toArray()[0];
    const revision = current?.revision ?? 0;
    if (request.method === 'GET') {
      return Response.json({revision, value:current ? JSON.parse(current.value) : null}, {headers});
    }
    if (query.revision !== revision) return bad(409);
    // The read and update run without an intervening await in the same Durable Object.
    this.sql.exec('INSERT OR REPLACE INTO journal VALUES (?, ?, ?)', query.key, revision + 1, JSON.stringify(query.value));
    return Response.json({revision:revision + 1}, {headers});
  }
}

function journalAuthorized(request, token) {
  if (!token) return false;
  const expected = 'Bearer ' + token;
  const actual = request.headers.get('Authorization') || '';
  if (actual.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= actual.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

export default {async fetch(request, env) {
  const path = new URL(request.url).pathname;
  if (path !== '/journal') return new Response(null, {status:404});
  if (!journalAuthorized(request, env.GOAL_ROI_DURABLE_TOKEN)) return new Response(null, {status:403});
  const journal = env.GUARD.get(env.GUARD.idFromName('goal-roi-journal-main'));
  const headers = new Headers(request.headers);
  headers.delete('Authorization');
  return journal.fetch(new Request(request, {headers}));
}, async scheduled(event, env) {
  const id = new Date(event.scheduledTime).toISOString();
  const report = await inspect(env.GITHUB_ACTIONS_TOKEN);
  let outcome = report;
  if (recoveryEnabled(env) && report.decision === 'would_recover') {
    const guard = env.GUARD.get(env.GUARD.idFromName('goal-roi-main'));
    const response = await guard.fetch('https://guard.internal/check');
    if (!response.ok) throw Error('Recovery guard failed');
    outcome = await response.json();
  }
  console.log(JSON.stringify({scheduled_utc:id, observed_utc:new Date().toISOString(),
    mode:recoveryEnabled(env) ? 'active' : 'observe', ...outcome}));
}};

// A fresh queued successor also acknowledges a dispatch whose HTTP response was lost.
const workflow = 'https://api.github.com/repos/nunovinhas-creator/goal-roi/actions/workflows/collect.yml';
const runsApi = 'https://api.github.com/repos/nunovinhas-creator/goal-roi/actions/runs';
const graceMs = (18 + 12) * 60_000;

export async function check(token, now = Date.now(), request = fetch) {
  const headers = {Authorization: 'Bearer ' + token, 'User-Agent': 'goal-roi-continuity'};
  const call = (url, options = {}) => request(url, {headers, redirect: 'error', signal: AbortSignal.timeout(8000), ...options});
  async function read(url) {
    const response = await call(url);
    if (!response.ok) throw Error('GitHub lookup HTTP ' + response.status);
    return response.json();
  }
  function expired(timestamp) {
    const time = Date.parse(timestamp);
    if (!Number.isFinite(time)) throw Error('Missing GitHub run timestamp');
    return now - time >= graceMs;
  }

  const {workflow_runs: runs} = await read(workflow + '/runs?branch=main&per_page=20');
  if (!runs.length) return;
  for (const run of runs) {
    if (run.status === 'completed') {
      if (!expired(run.updated_at)) return;
    } else if (['queued', 'pending', 'requested', 'waiting', 'in_progress'].includes(run.status)) {
      // A rerun can have a much earlier original creation time.
      if (!expired(run.updated_at) || !expired(run.run_started_at || run.created_at)) return;
    } else {
      throw Error('Unrecognised workflow run status');
    }
  }
  for (const run of runs.filter(run => run.status === 'in_progress')) {
    // Time spent waiting for a runner does not consume the collector job's 18 minutes.
    const {jobs} = await read(runsApi + '/' + run.id + '/jobs?filter=latest');
    if (jobs.some(job => !expired(job.completed_at || job.started_at || job.created_at))) return;
  }
  const stuck = runs.filter(run => run.status !== 'completed').map(run => ({id:run.id, status:run.status}));
  console.log(JSON.stringify({event:'collection_restart_requested', stuck}));
  const dispatch = await call(workflow + '/dispatches', {method: 'POST', body: JSON.stringify({ref: 'main'}),
    headers: {...headers, 'Content-Type': 'application/json'}});
  if (!dispatch.ok) throw Error('Dispatch HTTP ' + dispatch.status);
}

export default {async scheduled(event, env) { await check(env.GITHUB_ACTIONS_TOKEN); }};

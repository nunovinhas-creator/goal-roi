// Independent clock. A failed probe or an ambiguous dispatch is retried on the next tick.
const workflow = 'https://api.github.com/repos/nunovinhas-creator/goal-roi/actions/workflows/collect.yml';
export async function check(token, now = Date.now(), request = fetch) {
  const headers = {Authorization: 'Bearer ' + token, 'User-Agent': 'goal-roi-continuity'};
  const call = (url, options = {}) => request(url, {headers, redirect: 'error', signal: AbortSignal.timeout(8000), ...options});
  const response = await call(workflow + '/runs?branch=main&per_page=20');
  if (!response.ok) throw Error('Run lookup HTTP ' + response.status);
  const {workflow_runs: runs} = await response.json();
  if (!runs.length || runs.some(run => run.status !== 'completed')) return;
  const latest = Math.max(...runs.map(run => Date.parse(run.updated_at)));
  if (!Number.isFinite(latest) || now - latest < 30 * 60_000) return;
  const dispatch = await call(workflow + '/dispatches', {method: 'POST', body: JSON.stringify({ref: 'main'}),
    headers: {...headers, 'Content-Type': 'application/json'}});
  if (!dispatch.ok) throw Error('Dispatch HTTP ' + dispatch.status);
}

export default {async scheduled(event, env) { await check(env.GITHUB_ACTIONS_TOKEN); }};

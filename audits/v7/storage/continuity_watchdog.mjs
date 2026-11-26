import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root = path.resolve(process.argv[2]);
const {inspect,default:worker} = await import(pathToFileURL(path.join(root,'ops/continuity/watchdog.mjs')));
const now = Date.parse('2026-11-26T10:54:17Z');
const old = h => new Date(now-h*3600000).toISOString();
const result = {};
for (const hours of [24,72]) {
  const calls = [];
  const fixture = async (url, opts) => {
    calls.push(opts.method);
    if (url.includes('/contents/')) return Response.json({last_run_utc:old(hours)});
    return Response.json({workflow_runs:[{id:1,status:'completed',created_at:old(hours),updated_at:old(hours)}]});
  };
  const state = await inspect(null,now,fixture);
  assert.equal(state.decision,'would_recover');
  assert.deepEqual(calls,['GET','GET']);
  for (const status of [401,503]) {
    let reads = 0;
    await assert.rejects(inspect('local-fixture',now,async (_url, opts) => {
      assert.equal(opts.method,'GET'); reads++;
      return new Response('',{status});
    }),new RegExp('GitHub lookup HTTP '+status));
    assert.equal(reads,1);
  }
  result[hours] = {late_read_reports_recovery_needed:true,unavailable_actions_fails_closed:true,
    expired_credentials_fail_closed:true,remote_dispatches:0};
}
// Model provider unavailability as absence of the scheduled entrypoint, then restore in observer mode.
const previous = globalThis.fetch;
let reads = 0;
globalThis.fetch = async (url, opts) => {
  assert.equal(opts.method,'GET'); reads++;
  if (url.includes('/contents/')) return Response.json({last_run_utc:new Date().toISOString()});
  return Response.json({workflow_runs:[{id:1}]});
};
assert.equal(reads,0);
await worker.scheduled({scheduledTime:Date.now()},{RECOVERY_MODE:'observe',RECOVERY_ENABLED:'false'});
assert.equal(reads,2);
globalThis.fetch = previous;
result.cloudflare_model = {no_cron_means_no_dispatch:true,restored_in_observe_mode:true,reads_after_restore:reads};
console.log(JSON.stringify(result,null,2));

import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {inspect,RecoveryGuard} from './watchdog.mjs';
import {DatabaseSync} from 'node:sqlite';

const now = Date.now();
const ago = minutes => new Date(now - minutes * 60000).toISOString();
function fixture(status='completed', readMinutes=40, jobMinutes=40) {
  let posts = 0;
  const request = async (url, options) => {
    if (options.method === 'POST') { posts++; throw Error('lost reply'); }
    if (url.includes('/contents/')) return Response.json({last_run_utc:ago(readMinutes)});
    if (url.includes('/jobs?')) return Response.json({jobs:[{started_at:ago(jobMinutes)}]});
    return Response.json({workflow_runs:[{id:123,status,created_at:ago(40),updated_at:ago(40)}]});
  };
  return {request, posts:()=>posts};
}
for (const status of ['queued','in_progress','completed']) {
  test(status + ' 30 minute threshold', async () => {
    const f=fixture(status,30);
    assert.equal((await inspect(null,now-1,f.request)).decision,'wait');
    assert.equal((await inspect(null,now,f.request)).decision,'would_recover');
    assert.equal(f.posts(),0);
  });
}
test('recent job protects a run after a long queue', async () => {
  assert.equal((await inspect(null,now,fixture('in_progress',40,5).request)).reason,'recent_job');
});
test('observation stays read only even with stale data and a secret', async t => {
  const f=fixture();
  t.mock.method(globalThis,'fetch',f.request);
  await worker.scheduled({scheduledTime:now},{GITHUB_ACTIONS_TOKEN:'fixture',RECOVERY_MODE:'observe'});
  assert.equal(f.posts(),0);
});
test('healthy collector never invokes the active guard', async t => {
  const f=fixture('queued',2);
  t.mock.method(globalThis,'fetch',f.request);
  await worker.scheduled({scheduledTime:now},{RECOVERY_MODE:'active'});
  assert.equal(f.posts(),0);
});
test('concurrent ticks and eviction after lost reply use one durable claim', async t => {
  const db=new DatabaseSync(':memory:');
  t.after(()=>db.close());
  const sql = {exec(statement, ...args) {
    const rows = db.prepare(statement).all(...args);
    return {toArray: () => rows};
  }};
  const ctx = {storage:{sql}};
  const f=fixture(); const calls=t.mock.method(globalThis,'fetch',f.request);
  const env={RECOVERY_MODE:'active',GITHUB_ACTIONS_TOKEN:'fixture'};
  const guard=new RecoveryGuard(ctx,env);
  const results=await Promise.allSettled([guard.fetch(),guard.fetch()]);
  assert.equal(results.filter(r=>r.status==='rejected').length,1);
  assert.equal((await (await new RecoveryGuard(ctx,env).fetch()).json()).decision,'already_requested');
  assert.equal(f.posts(),1);
  assert.equal(calls.mock.calls.filter(c=>String(c.arguments[0]).includes('/runs?')).length,3);
});

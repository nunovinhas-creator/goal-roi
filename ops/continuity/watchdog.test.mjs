import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {inspect,RecoveryGuard} from './watchdog.mjs';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const now = Date.now();

test('journal rejects unauthenticated access without touching durable storage', async () => {
  const env = {GOAL_ROI_DURABLE_TOKEN:'synthetic', GUARD:{get(){throw Error('accessed without authentication');}}};
  const url = 'https://test.invalid/journal?key=publication';
  for (const headers of [{}, {Authorization:'Bearer incorrect'}]) {
    assert.equal((await worker.fetch(new Request(url, {headers}), env)).status, 403);
  }
  assert.equal((await worker.fetch(new Request('https://test.invalid/check'), env)).status, 404);
});

test('journal acknowledges a single compare-and-set and retains it after eviction', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'goal-roi-journal-'));
  let db;
  try {
    function open() {
      db = new DatabaseSync(join(directory, 'journal.sqlite'));
      return new RecoveryGuard({storage:{sql:{exec(query, ...args) {
        const stmt = db.prepare(query);
        if (query.startsWith('SELECT')) return {toArray:() => stmt.all(...args)};
        stmt.run(...args);
      }}}}, {});
    }
    let guard = open();
    const env = {RECOVERY_MODE:'observe', RECOVERY_ENABLED:'false', GOAL_ROI_DURABLE_TOKEN:'synthetic',
      GUARD:{idFromName(name){assert.equal(name, 'goal-roi-journal-main'); return name;}, get(){return guard;}}};
    const url = 'https://test.invalid/journal';
    const headers = {Authorization:'Bearer synthetic'};
    const read = () => worker.fetch(new Request(url + '?key=publication', {headers}), env);
    const write = value => worker.fetch(new Request(url, {method:'PUT', headers,
      body:JSON.stringify({key:'publication', revision:0, value})}), env);
    assert.deepEqual(await (await read()).json(), {revision:0, value:null});
    const receipts = [{phase:'persisted', bundle:'copy preserved after eviction'}, {phase:'different receipt'}];
    const responses = await Promise.all(receipts.map(write));
    assert.deepEqual(responses.map(r => r.status).sort(), [200, 409]);
    const accepted = responses.findIndex(response => response.ok);
    assert.deepEqual(await responses[accepted].json(), {revision:1});
    db.close();
    db = undefined;
    guard = open();
    assert.deepEqual(await (await read()).json(), {revision:1, value:receipts[accepted]});
  } finally {
    db?.close();
    rmSync(directory, {recursive:true, force:true});
  }
});

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
test('GitHub API reads use Cloudflare-compatible manual redirects', async () => {
  const f=fixture('completed',2);
  let reads=0;
  const request=async (url, options) => {
    assert.equal(options.method,'GET');
    assert.equal(options.redirect,'manual');
    reads++;
    return f.request(url,options);
  };
  assert.equal((await inspect(null,now,request)).decision,'wait');
  assert.equal(reads,2);
});
test('GitHub redirects are rejected, not followed', async () => {
  let calls=0;
  const request=async (url, options) => {
    assert.equal(options.redirect,'manual');
    calls++;
    return Response.redirect('https://example.invalid/unsafe',302);
  };
  await assert.rejects(inspect(null,now,request),/GitHub lookup HTTP 302/);
  assert.equal(calls,1);
});
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
test('active mode with disabled recovery flag stays read only at both entry points', async t => {
  const f=fixture();
  t.mock.method(globalThis,'fetch',f.request);
  const env={RECOVERY_MODE:'active',RECOVERY_ENABLED:'false',GITHUB_ACTIONS_TOKEN:'fixture',
    GUARD:{get(){throw Error('guard must remain disabled');}}};
  await worker.scheduled({scheduledTime:now},env);
  const guard=new RecoveryGuard({storage:{sql:{exec(){throw Error('must not touch storage');}}}},env);
  assert.equal((await (await guard.fetch()).json()).decision,'disabled');
  assert.equal(f.posts(),0);
});
test('missing explicit recovery flag is also read only', async t => {
  const f=fixture();
  t.mock.method(globalThis,'fetch',f.request);
  const env={RECOVERY_MODE:'active',GITHUB_ACTIONS_TOKEN:'fixture',
    GUARD:{get(){throw Error('guard must remain disabled');}}};
  await worker.scheduled({scheduledTime:now},env);
  assert.equal((await (await new RecoveryGuard({storage:{sql:{}}},env).fetch()).json()).decision,'disabled');
  assert.equal(f.posts(),0);
});
test('observe mode overrides even an explicitly true recovery flag', async t => {
  const f=fixture();
  t.mock.method(globalThis,'fetch',f.request);
  const env={RECOVERY_MODE:'observe',RECOVERY_ENABLED:'true',GITHUB_ACTIONS_TOKEN:'fixture',
    GUARD:{get(){throw Error('guard must remain disabled');}}};
  await worker.scheduled({scheduledTime:now},env);
  assert.equal((await (await new RecoveryGuard({storage:{sql:{}}},env).fetch()).json()).decision,'disabled');
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
  const env={RECOVERY_MODE:'active',RECOVERY_ENABLED:'true',GITHUB_ACTIONS_TOKEN:'fixture'};
  const guard=new RecoveryGuard(ctx,env);
  const results=await Promise.allSettled([guard.fetch(),guard.fetch()]);
  assert.equal(results.filter(r=>r.status==='rejected').length,1);
  assert.equal((await (await new RecoveryGuard(ctx,env).fetch()).json()).decision,'already_requested');
  assert.equal(f.posts(),1);
  assert.equal(calls.mock.calls.find(c=>c.arguments[1]?.method==='POST')?.arguments[1]?.redirect,'manual');
  assert.equal(calls.mock.calls.filter(c=>String(c.arguments[0]).includes('/runs?')).length,3);
});

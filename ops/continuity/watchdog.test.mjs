import assert from 'node:assert/strict';
import test from 'node:test';
import {check} from './watchdog.mjs';

const now = Date.UTC(2026, 10, 19, 9);
const ago = minutes => new Date(now - minutes * 60000).toISOString();
function run(status, minutes, id = 1) {
  return {id, status, created_at:ago(minutes), updated_at:ago(minutes), run_started_at:ago(minutes)};
}
function fixture(runs, jobs = [], loseReply = false) {
  let posts = 0;
  async function request(url, options) {
    if (options.method === 'POST') {
      assert.ok(url.endsWith('/collect.yml/dispatches'));
      assert.deepEqual(JSON.parse(options.body), {ref:'main'});
      posts++;
      runs.unshift(run('queued', 0, 2));
      if (loseReply) throw Error('lost response');
      return {ok:true};
    }
    return {ok:true, json:async () => url.includes('/jobs?') ? {jobs} : {workflow_runs:runs}};
  }
  return {tick:(at = now) => check('fixture', at, request), posts:() => posts};
}

for (const status of ['completed', 'queued', 'in_progress']) {
  test(status + ' at threshold, accepted dispatch with lost reply', async () => {
    const runs = [run(status, 30)];
    const f = fixture(runs, [{started_at:ago(30)}], true);
    await f.tick(now - 1);
    assert.equal(f.posts(), 0);
    await assert.rejects(f.tick(), /lost response/);
    await f.tick(now + 15 * 60000);
    runs[0] = run('completed', 0, 2);
    await f.tick(now + 15 * 60000);
    assert.equal(f.posts(), 1);
  });
}
test('old workflow acquired its runner recently', async () => {
  const f = fixture([run('in_progress', 70)], [{started_at:ago(5)}]);
  await f.tick();
  assert.equal(f.posts(), 0);
});
test('recent successor protects old queued and running executions', async () => {
  const f = fixture([run('queued', 3, 3), run('queued', 75, 2), run('in_progress', 60)]);
  await f.tick();
  assert.equal(f.posts(), 0);
});
test('ambiguous metadata prevents a dispatch', async () => {
  const f = fixture([run('in_progress', 60)], [{}]);
  await assert.rejects(f.tick(), /timestamp/);
  assert.equal(f.posts(), 0);
});

import assert from 'node:assert/strict';
import test from 'node:test';
import {check} from './watchdog.mjs';
test('outage, lost acknowledgement, recovery and next tick', async () => {
  const now = Date.now();
  const runs = [{status:'completed', updated_at:new Date(now - 4 * 3600_000).toISOString()}];
  let posts = 0;
  const request = async (_, options) => {
    if (options.method === 'POST') {
      posts++;
      runs.unshift({status:'queued'});
      throw Error('lost response');
    }
    return {ok:true, json:async () => ({workflow_runs:runs})};
  };
  await assert.rejects(check('fixture', now, request), /lost response/);
  await check('fixture', now, request);
  runs[0] = {status:'completed', updated_at:new Date(now).toISOString()};
  await check('fixture', now, request);
  assert.equal(posts, 1);
});

import assert from 'node:assert/strict';
import {inspect, RecoveryGuard} from '../../ops/continuity/watchdog.mjs';

const clock = Date.now();
const old = new Date(clock - 3600000).toISOString();
const fetchReal = globalThis.fetch;
const rows = new Set();

function simulateSql(command, id) {
  if (command.startsWith('CREATE TABLE')) return;
  if (command.startsWith('DELETE FROM')) return rows.delete(id);
  assert.ok(command.startsWith('INSERT OR IGNORE'));
  if (rows.has(id)) return {toArray: () => []};
  rows.add(id);
  return {toArray: () => [{collector:id}]};
}

async function oldCollection(request) {
  if (request.includes('/contents/')) return Response.json({last_run_utc:old});
  if (request.includes('/runs?')) return Response.json({workflow_runs:[
    {id:1, status:'completed', created_at:old, updated_at:old}
  ]});
  throw new Error('Unexpected GET in synthetic fixture');
}

const scenarios = [
  ['O01', 'GitHub indisponível produz estado sem decisão', async () => {
    await assert.rejects(inspect(undefined, clock, async () => Response.json({}, {status:503})),
                         /GitHub lookup HTTP 503/);
  }],
  ['O02', 'recuperação desabilitada bloqueia o despacho', async () => {
    let attempts = 0;
    globalThis.fetch = async () => { attempts++; throw new Error('unexpected request'); };
    const guard = new RecoveryGuard({storage:{sql:{exec:simulateSql}}}, {RECOVERY_MODE:'observe'});
    assert.equal((await (await guard.fetch()).json()).decision, 'disabled');
    assert.equal(attempts, 0);
  }],
  ['O03', 'erro confirmado após intenção admite retoma', async () => {
    let posts = 0;
    globalThis.fetch = async (url, options) => {
      if (options.method === 'POST') {
        posts++;
        return new Response(null, {status:posts === 1 ? 401 : 204});
      }
      return oldCollection(url);
    };
    const settings = {RECOVERY_MODE:'active', RECOVERY_ENABLED:'true', GITHUB_ACTIONS_TOKEN:'synthetic'};
    const guard = new RecoveryGuard({storage:{sql:{exec:simulateSql}}}, settings);
    await assert.rejects(guard.fetch(), /Dispatch HTTP 401/);
    const after = await (await guard.fetch()).json();
    assert.equal(after.decision, 'requested',
                 'o pedido recusado manteve a intenção, posts=' + posts + ', decisão=' + after.decision);
  }],
  ['O04', 'um pedido recente impede a recuperação', async () => {
    const response = await inspect(undefined, clock, url => {
      if (url.includes('/contents/')) return oldCollection(url);
      return Response.json({workflow_runs:[
        {id:2, status:'pending', created_at:old, updated_at:new Date(clock-5000).toISOString()}
      ]});
    });
    assert.equal(response.reason, 'recent_run');
  }]
];

try {
  for (const [id, title, run] of scenarios) {
    try {
      await run();
      console.log(id, 'PASSOU', title);
    } catch (error) {
      console.log(id, error instanceof assert.AssertionError ? 'FALHOU' : 'NÃO VERIFICADO',
                  title + ' — ' + error.message.replace(/\s+/g, ' '));
      process.exitCode = 1;
    }
  }
} finally {
  globalThis.fetch = fetchReal;
}

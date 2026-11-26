// Chromium real, servidor local, só pacote sintético. Nenhum pedido externo.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(process.argv[2]);
const manifest = JSON.parse(fs.readFileSync(path.join(root,'snapshots/dashboard-manifest.json')));
let revision = manifest.current, fail = false;
const newer = manifest.current, older = manifest.history[1];
assert.ok(older && newer.published_at > older.published_at);
const server = http.createServer((req,res) => {
  const rel = new URL(req.url,'http://localhost').pathname.replace(/^\/+/,'') || 'index.html';
  if (rel.startsWith('snapshots/') && fail) { res.writeHead(503); return res.end('offline'); }
  if (rel === 'snapshots/dashboard-manifest.json') {
    res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
    return res.end(JSON.stringify({history:[revision],current:revision}));
  }
  const p = path.resolve(root,rel);
  if (!p.startsWith(root+path.sep) || !fs.existsSync(p) || !fs.statSync(p).isFile()) {
    res.writeHead(404); return res.end();
  }
  res.writeHead(200,{'Content-Type':rel.endsWith('.mjs')?'text/javascript':rel.endsWith('.json')?'application/json':'text/html','Cache-Control':'no-store'});
  res.end(fs.readFileSync(p));
});
async function result(page,base) {
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(() => {try{return !!JSON.parse(document.getElementById('out').textContent).which}catch{return false}},null,{timeout:10000});
  return JSON.parse(await page.locator('#out').innerText());
}
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base='http://127.0.0.1:'+server.address().port;
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'v7-browser-'));
  let ctx;
  try {
    ctx=await chromium.launchPersistentContext(profile,{headless:true});
    const a=ctx.pages()[0]||await ctx.newPage();
    let got=await result(a,base);
    assert.equal(got.storage_mode,'shared');
    assert.equal(got.visible_published_at,newer.published_at);
    assert.equal(got.storage_error,false);
    revision=older;
    got=await result(a,base);
    assert.equal(got.which,'retained'); assert.equal(got.stale,true);
    assert.equal(got.visible_published_at,newer.published_at);
    const b=await ctx.newPage();
    const both=await Promise.all([result(a,base),result(b,base)]);
    for(const row of both) assert.equal(row.visible_published_at,newer.published_at);
    await ctx.close(); ctx=null;
    ctx=await chromium.launchPersistentContext(profile,{headless:true});
    const restarted=ctx.pages()[0]||await ctx.newPage();
    got=await result(restarted,base);
    assert.equal(got.visible_published_at,newer.published_at);
    assert.equal(got.stale,true);
    fail=true;
    got=await result(restarted,base);
    assert.equal(got.which,'retained'); assert.equal(got.visible_published_at,newer.published_at);
    fail=false; revision=newer;
    got=await result(restarted,base);
    assert.equal(got.which,'current'); assert.equal(got.stale,false);
    await restarted.evaluate(()=>localStorage.clear());
    revision=older;
    got=await result(restarted,base);
    assert.equal(got.visible_published_at,older.published_at); // cold session limitation
    assert.equal(got.freshness,'unverified');
    const quota=await ctx.newPage();
    await quota.addInitScript(()=>{Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError')}});
    revision=newer;
    got=await result(quota,base);
    assert.equal(got.storage_error,true); assert.equal(got.visible_published_at,newer.published_at);
    const blocked=await ctx.newPage();
    await blocked.addInitScript(()=>{
      for(const name of ['localStorage','sessionStorage']) Object.defineProperty(window,name,{get(){throw new DOMException('blocked','SecurityError')}});
    });
    got=await result(blocked,base);
    assert.equal(got.storage_mode,'memory'); assert.equal(got.visible_published_at,newer.published_at);
    console.log('BROWSER PASS: reload, two tabs, profile restart, offline endpoint, reconnect, cold session, quota, blocked storage');
  } finally {
    if(ctx) await ctx.close();
    fs.rmSync(profile,{recursive:true,force:true});
    server.close();
  }
})().catch(e=>{console.error(e);server.close();process.exitCode=1});

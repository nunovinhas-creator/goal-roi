// Dashboard refresh in a real browser (Playwright/Chromium), fully offline: every request is answered by page.route.
// One coherent snapshot of report.json across all sections; a new report redraws everything without duplicates and
// keeps the reader's sort, filter and simulator choices; a failed refresh keeps the last snapshot, flagged as stale.
// Usage: node tests/dashboard_refresh.cjs [path/to/index.html] [path/to/report.json]
const fs=require('node:fs'), assert=require('node:assert/strict');
const {chromium}=require('playwright');
const html=fs.readFileSync(process.argv[2]||'docs/index.html','utf8');
const base=JSON.parse(fs.readFileSync(process.argv[3]||'docs/report.json','utf8'));

function snapshot(minutesAgo,extra){
  const d=structuredClone(base);
  d.audit=Object.assign({},d.audit,{collector_last_run_utc:new Date(Date.now()-minutesAgo*60000).toISOString().replace(/\.\d+Z$/,'+00:00')});
  return Object.assign(d,extra||{});
}

(async()=>{
  const A=snapshot(5), tl=A.timeline||[];
  const B=snapshot(1,{messages:A.messages+7,settled_bets:A.settled_bets+1,timeline:tl.concat([Object.assign({},tl[tl.length-1]||{t:'OPEN_GAME',o:2,w:1,g:1,s:'x',m:'Novo'},{u:((tl[tl.length-1]||{}).u||0)+1})])});
  let reply={status:200,body:JSON.stringify(A)}, requests=0;
  const browser=await chromium.launch();
  try{
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/*',route=>{const url=route.request().url();
      if(url==='http://goal-roi.test/')return route.fulfill({status:200,contentType:'text/html',body:html});
      if(url.startsWith('http://goal-roi.test/report.json')){requests++;return reply.abort?route.abort('internetdisconnected'):route.fulfill({status:reply.status,contentType:'application/json',body:reply.body})}
      return route.abort()});                                                 // fonts and anything else: offline
    await page.goto('http://goal-roi.test/');
    await page.waitForFunction(()=>document.getElementById('facts').children.length>0);
    const facts=()=>page.$eval('#facts',e=>e.textContent), fresh=()=>page.$eval('#fresh',e=>[e.textContent,e.classList.contains('stale')]);
    const counts=()=>page.evaluate(()=>Object.fromEntries(['facts','signals','filters','simtypes','braincounts','groups','qc','calib','risk','move','says','best','avoid','boardopen','wf','entry','llm','notes','watch','uns','daily','questions']
      .map(id=>[id,document.getElementById(id)?document.getElementById(id).children.length:null]).concat([['charts',document.querySelectorAll('#chart svg').length],['main',document.querySelectorAll('main').length],['tips',document.querySelectorAll('#tip').length]])));
    assert.match(await facts(),new RegExp(`^${A.messages} mensagens lidas`));
    assert.match((await fresh())[0],/Atualizado em/);assert.equal((await fresh())[1],false);
    const first=await counts();

    // The reader's choices: a sort, a filter, the simulator.
    const sortTh=await page.$$('table:has(#hyps) thead button.sort');
    const hasSort=sortTh.length>1; if(hasSort)await sortTh[1].click();
    const sortState=()=>page.evaluate(()=>[...document.getElementById('hyps').closest('table').querySelectorAll('thead th')].map(t=>t.getAttribute('aria-sort')));
    const sorted=await sortState();
    await page.click('#filters button:nth-child(2)');
    await page.fill('#simbank','500');await page.dispatchEvent('#simbank','input');
    const types=await page.$$('#simtypes input');const hasTypes=types.length>0;if(hasTypes)await types[0].uncheck();
    const sim=await page.$eval('#simout',e=>e.textContent);

    // Same report again: nothing redrawn (marker survives), only agenda and indicator.
    await page.evaluate(()=>{document.getElementById('facts').firstElementChild.dataset.mark='kept'});
    const asked=requests;await page.evaluate(()=>agendaRefresh());
    await page.waitForTimeout(300);assert.equal(requests,asked+1);
    assert.equal(await page.$eval('#facts',e=>e.firstElementChild.dataset.mark||''),'kept');

    // New report: every section from B, nothing duplicated, choices kept.
    reply={status:200,body:JSON.stringify(B)};
    await page.evaluate(()=>agendaRefresh());
    await page.waitForFunction(n=>document.getElementById('facts').textContent.startsWith(n+' mensagens'),B.messages);
    assert.match(await facts(),new RegExp(`${B.settled_bets} com resultado`));
    assert.deepEqual(await counts(),first);                                  // same structure, no duplicate rows/nodes/buttons
    if(B.timeline.length>=2)assert.match(await page.$eval('#chart svg',s=>s.getAttribute('aria-label')),new RegExp(`ao longo de ${B.timeline.length} apostas`));
    assert.match((await fresh())[0],/Atualizado em/);assert.equal((await fresh())[1],false);
    assert.deepEqual(await sortState(),sorted);
    assert.equal(await page.$eval('#filters button:nth-child(2)',b=>b.getAttribute('aria-pressed')),'true');
    assert.equal(await page.$eval('#simbank',e=>e.value),'500');
    if(hasTypes)assert.equal(await page.$eval('#simtypes input',e=>e.checked),false);
    if(hasSort){                                                              // one listener per button: one click toggles once
      const before=await sortState();await (await page.$$('table:has(#hyps) thead button.sort'))[1].click();
      assert.notDeepEqual(await sortState(),before)}
    const simAfter=await page.$eval('#simout',e=>e.textContent);
    assert.ok(simAfter.length>0&&(B.timeline.length!==A.timeline.length||simAfter===sim));

    // Network error, HTTP error, invalid JSON: last snapshot kept, flagged as stale.
    for(const r of [{abort:true},{status:500,body:'{}'},{status:200,body:'{"truncated'}]){
      reply=r;await page.evaluate(()=>agendaRefresh());
      await page.waitForFunction(()=>document.getElementById('fresh').classList.contains('stale'));
      assert.match((await fresh())[0],/^Falha ao atualizar; dados de /);
      assert.match(await facts(),new RegExp(`^${B.messages} mensagens lidas`));
      assert.deepEqual(await counts(),first);
      reply={status:200,body:JSON.stringify(B)};await page.evaluate(()=>agendaRefresh());
      await page.waitForFunction(()=>!document.getElementById('fresh').classList.contains('stale'));
    }
    assert.deepEqual(errors,[]);
    console.log('Dashboard refresh regressions passed');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});

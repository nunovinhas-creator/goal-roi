// Static dashboard end-to-end regressions. Report, CSS and JS are served by browser
// routes so the test uses the exact GitHub Pages entry point without external services.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const indexPath = process.argv[2] || 'docs/index.html';
const base = JSON.parse(fs.readFileSync(process.argv[3] || 'docs/report.json','utf8'));
const docs = path.dirname(indexPath);
const root = 'http://goal-roi.test/';
const date = new Date(base.generated_at_utc);
const percent = (value, places=1) => (value * 100).toFixed(places).replace('.',',') + ' %';
const units = n => (n > 0 ? '+' : '') + String(n).replace('.',',');
const count = n => new Intl.NumberFormat('pt-PT').format(n);
const euro = value => (Math.round(value * 100) / 100).toLocaleString('pt-PT', {
  minimumFractionDigits:2, maximumFractionDigits:2
}) + ' €';
const sizes = [[375,812],[768,1024],[1440,1000]];
const Q7 = 'Q7 Todas as apostas (referência)';
const assets = {
  '/': ['text/html', indexPath],
  '/dashboard.js': ['text/javascript', path.join(docs,'dashboard.js')],
  '/dashboard.css': ['text/css', path.join(docs,'dashboard.css')]
};
const files = Object.fromEntries(Object.entries(assets).map(([name,[contentType,file]]) =>
  [name,{status:200,contentType,body:fs.readFileSync(file,'utf8')}]));

function snapshot(minutesAgo, extra={}) {
  const d = structuredClone(base);
  d.audit = {...d.audit, collector_last_run_utc:new Date(date - minutesAgo*60000).toISOString()};
  Object.assign(d,extra);
  if (extra.unique_bets !== undefined) {
    const added = d.unique_bets - base.unique_bets;
    Object.values(d.groups)[0].bets += added;
    d.questions[Q7].bets += added;
    d.daily.at(-1).bets += added;
  }
  return d;
}

async function setup(page, reply, useTimers = false) {
  const errors = [], held = [];
  let requests = 0;
  page.on('pageerror', error => errors.push(String(error)));
  if (useTimers) await page.clock.install({time:date});
  else await page.clock.setFixedTime(date);
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(root).origin) return route.abort();
    if (url.pathname === '/report.json') {
      requests++;
      if (reply?.hold) { held.push(route); return; }
      return reply?.abort ? route.abort('internetdisconnected') : route.fulfill({
        status:reply?.status || 200, contentType:'application/json',
        body:reply?.body ?? JSON.stringify(reply?.report ?? base)
      });
    }
    return files[url.pathname] ? route.fulfill(files[url.pathname]) : route.abort();
  });
  return {held,errors,requests:() => requests, respond:value => { reply=value; }};
}

async function ready(page, expected) {
  await page.waitForFunction(n => document.querySelector('#conteudo').getAttribute('aria-busy') === 'false' &&
    (n === null || document.querySelector('#metric-tracked').textContent === n), expected == null ? null : count(expected));
}

async function go(page, view) {
  const link = page.locator(`nav [href="#${view}"]`);
  await link.click();
  await page.waitForFunction(id => !document.getElementById(id).hidden &&
    document.querySelector(`nav [href="#${id}"]`).getAttribute('aria-current') === 'page', view);
}

async function expand(page, id) {
  const details = page.locator('#'+id);
  await details.locator('summary').click();
  await page.waitForFunction(id => document.getElementById(id).open, id);
}

async function refresh(page) {
  await page.evaluate(() => agendaRefresh());
}

async function hasText(page, selector, search) {
  await page.waitForFunction(([selector, search]) => document.querySelector(selector)?.textContent.includes(search), [selector, search]);
}

async function noOverflow(page) {
  const sizes = await page.evaluate(() => [document.documentElement.scrollWidth,document.documentElement.clientWidth]);
  assert.ok(sizes[0] <= sizes[1], `page overflow: ${sizes[0]}px in ${sizes[1]}px`);
}

async function refreshRegression(page) {
  const A = snapshot(5);
  const B = snapshot(1,{generated_at_utc:new Date(+date+1000).toISOString(),messages:A.messages+7,unique_bets:A.unique_bets+1});
  const C = snapshot(0,{generated_at_utc:new Date(+date+2000).toISOString(),messages:B.messages+2,unique_bets:B.unique_bets+2});
  const remote = await setup(page,{report:A});
  await page.goto(root);
  await ready(page,A.unique_bets);
  const facts=()=>page.locator('#facts').textContent();
  const isStale=()=>page.locator('#fresh').evaluate(e=>e.classList.contains('stale'));
  assert.match(await facts(),new RegExp(`^${A.messages} mensagens lidas`));
  await hasText(page,'#fresh','Atualizado em');
  assert.equal(await isStale(),false);

  await go(page,'resultados');
  await expand(page,'simulador');
  await expand(page,'dados-resultados');
  await page.locator('#filters button').nth(1).click();
  await page.selectOption('#simmode','pct');
  await page.fill('#simbank','500');
  await page.fill('#simval','2.5');
  await page.selectOption('#simcut','0.02');
  const type=page.locator('#simtypes input').first();
  const hasTypes=await type.count();
  if(hasTypes) await type.uncheck();
  const sim=await page.locator('#simout').textContent();
  const filters=await page.locator('#filters button').count();

  await go(page,'laboratorio');
  await expand(page,'hipoteses');
  const sort = page.locator('#hipoteses thead button').nth(1);
  await sort.click();
  const sortState=()=>page.locator('#hipoteses thead').innerHTML();
  const sorted=await sortState();
  const rows=await page.locator('#hyps tr').count();

  await go(page,'sinais');
  await page.locator('[name="signal-phase"][value="por começar"] + span').click();
  const source = 'Stake', state = 'misto';
  await page.selectOption('#signal-source',source);
  await page.selectOption('#signal-status',state);
  const firstCard=page.locator('.signal-card').first();
  const cardCount=await page.locator('.signal-card').count();
  assert.ok(cardCount > 0);
  await firstCard.locator('summary').click();

  await page.locator('#facts').evaluate(node=>node.firstElementChild.dataset.marker='kept');
  const requests=remote.requests();
  await refresh(page);
  assert.equal(remote.requests(),requests+1);
  assert.equal(await page.locator('#facts > :first-child').getAttribute('data-marker'),'kept');

  const incomplete = {generated_at_utc:new Date(+date+1000).toISOString()};
  const readSignals = () => page.locator('#agendalist').innerHTML();
  const before = await readSignals();
  remote.respond({report:incomplete});
  await refresh(page);
  assert.equal(await readSignals(),before,'A → incomplete B must preserve the filtered signals');
  assert.equal(await page.locator('#facts > :first-child').getAttribute('data-marker'),'kept');
  assert.match(await page.locator('#fresh').textContent(),/Falha ao atualizar/);
  assert.equal(await page.inputValue('#signal-source'),source);
  assert.equal(await page.inputValue('#signal-status'),state);
  assert.equal(await firstCard.locator('details').evaluate(node=>node.open),true);
  assert.equal(await firstCard.locator('summary').evaluate(node=>node===document.activeElement),true);

  remote.respond({report:B});
  await refresh(page);
  await ready(page,B.unique_bets);
  assert.match(await page.locator('#fresh').textContent(),/Atualizado em/);
  assert.equal(await page.locator('nav [aria-current]').getAttribute('href'),'#sinais');
  assert.equal(await page.locator('[name="signal-phase"]:checked').inputValue(),'por começar');
  assert.equal(await page.inputValue('#signal-source'),source);
  assert.equal(await page.inputValue('#signal-status'),state);
  assert.equal(await page.locator('.signal-card').count(),cardCount);
  assert.equal(await firstCard.locator('details').evaluate(node=>node.open),true);
  assert.equal(await firstCard.locator('summary').evaluate(node=>node===document.activeElement),true);
  assert.match(await facts(),new RegExp(`^${B.messages} mensagens lidas`));
  assert.match(await facts(),new RegExp(`${B.settled_bets} com resultado`));

  await go(page,'laboratorio');
  assert.equal(await sortState(),sorted);
  assert.equal(await page.locator('#hyps tr').count(),rows);
  await sort.click();
  assert.notEqual(await sortState(),sorted);
  await go(page,'resultados');
  assert.equal(await page.locator('#filters button').nth(1).getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#filters button').count(),filters);
  for(const [id,value] of [['simbank','500'],['simmode','pct'],['simval','2.5'],['simcut','0.02']]) {
    assert.equal(await page.inputValue('#'+id),value);
  }
  if(hasTypes) assert.equal(await type.isChecked(),false);
  assert.equal(await page.locator('#simout').textContent(),sim);
  assert.equal(await page.locator('main').count(),1);
  assert.equal(await page.locator('#tip').count(),1);
  assert.equal(await page.locator('#chart svg').count(),A.timeline.length>=2 ? 1 : 0);

  const reject = [
    {abort:true}, {status:500,body:'{}'}, {body:'{"truncated'}, {body:'{}'},
    {report:{...C,generated_at_utc:new Date(+date+86400000).toISOString()}},
    {report:incomplete}, {report:{...B,timeline:[]}}, {report:{...B,timeline:[...B.timeline.slice(0,-1),{}]}},
    {report:{...B,questions:{}}}, {report:{...B,brain:{...B.brain,agenda:{...B.brain.agenda,items:{}}}}},
    {report:{...B,brain:{...B.brain,agenda:undefined}}}, {report:{...B,brain:undefined}}
  ];
  await page.locator('#simval').focus();
  const readResults = () => page.evaluate(() => ({
    metrics:document.querySelector('.metrics').textContent,
    balance:document.querySelector('#balance-total').textContent,
    chart:document.querySelector('#chart svg').outerHTML,
    outcomes:document.querySelector('#results-outcomes').innerHTML,
    simulator:document.querySelector('#simout').innerHTML,
    curve:document.querySelector('#simchart').innerHTML,
    controls:['simbank','simmode','simval','simcut'].map(id=>document.getElementById(id).value),
    open:[...document.querySelectorAll('#resultados details[open][id]')].map(node=>node.id),
    focused:document.activeElement.id,
    route:location.hash
  }));
  const expected = await readResults();
  for(const reply of reject) {
    remote.respond(reply);
    await refresh(page);
    assert.match(await page.locator('#fresh').textContent(),/Falha ao atualizar/);
    assert.deepEqual(await readResults(),expected,'An invalid response must preserve the rendered results, simulator and focus');
    assert.equal(await isStale(),true);
    assert.match(await facts(),new RegExp(`^${B.messages} mensagens lidas`));
    remote.respond({report:B});
    await refresh(page);
    assert.equal(await isStale(),false);
  }

  for(const late of [{body:JSON.stringify(A)},{abort:true}]) {
    remote.respond({hold:true});
    remote.held.length=0;
    const pending=page.evaluate(()=>Promise.all([agendaRefresh(),agendaRefresh()]));
    for(let attempt=0; remote.held.length<2 && attempt<100; attempt++) await new Promise(r=>setTimeout(r,10));
    assert.equal(remote.held.length,2);
    await remote.held[1].fulfill({contentType:'application/json',body:JSON.stringify(C)});
    await ready(page,C.unique_bets);
    if(late.abort) await remote.held[0].abort('internetdisconnected');
    else await remote.held[0].fulfill({contentType:'application/json',body:late.body});
    await pending;
    assert.match(await facts(),new RegExp(`^${C.messages} mensagens lidas`));
    assert.equal(await isStale(),false);
    remote.respond({report:C});
  }
  const older = {...A, generated_at_utc:new Date(+date-1000).toISOString()};
  remote.respond({report:older});
  await refresh(page);
  assert.match(await facts(),new RegExp(`^${C.messages} mensagens lidas`));
  assert.match(await page.locator('#fresh').textContent(),/Falha ao atualizar/);

  remote.respond({report:snapshot(46,{generated_at_utc:new Date(+date+3000).toISOString()})});
  await refresh(page);
  await hasText(page,'#fresh','Recolha parada desde');
  assert.equal(await isStale(),true);
  assert.deepEqual(remote.errors,[]);
}

async function collectionWarning(page, width, height) {
  const last = new Date(+date - 45 * 60000 + 5000);
  const A = snapshot(0);
  A.audit.collector_last_run_utc = last.toISOString();
  // The signal stays valid across both ticks of the warning and recovery scenario.
  A.brain.agenda.items = [{
    match_id:'collection-warning-fixture', match:'Equipa A — Equipa B', family:'Betfair', type:'BETFAIR',
    market:'1X2', selection:'1', odds:2, when:'por começar', phase:'pre', date_utc:date.toISOString(),
    kickoff_utc:new Date(+date + 3600000).toISOString(), verdict:'sem evidência', validated:false, good:[], bad:[]
  }];
  const remote = await setup(page,{report:A},true);
  await page.goto(root);
  await ready(page,A.unique_bets);
  const banner = page.locator('#collection-warning');
  assert.equal(await banner.isVisible(),false);
  await page.clock.fastForward(15000);
  assert.equal(await banner.isVisible(),true,'The warning must appear as collection crosses 45 minutes');
  const lisbon = new Intl.DateTimeFormat('pt-PT',{timeZone:'Europe/Lisbon',day:'2-digit',month:'2-digit',
    year:'numeric',hour:'2-digit',minute:'2-digit'}).format(last);
  assert.ok((await banner.textContent()).includes(lisbon));
  assert.match(await banner.textContent(),/\(Lisboa\).*Confirme as odds e a disponibilidade/);
  const warningPosition = await banner.boundingBox();
  assert.ok(warningPosition.y >= 0 && warningPosition.y + warningPosition.height < height);
  await noOverflow(page);
  const captures = process.env.DASHBOARD_CAPTURE_DIR;
  if (captures && width !== 768) {
    fs.mkdirSync(captures,{recursive:true});
    await page.screenshot({path:path.join(captures,`stale-${width}.png`)});
  }
  await go(page,'sinais');
  await page.locator('[name="signal-phase"][value="por começar"] + span').click();
  const cards = page.locator('.signal-card');
  const before = await cards.count();
  assert.ok(before > 0);
  assert.equal(await page.locator('.signal-stale:visible').count(),before);
  assert.equal(await banner.isVisible(),true);
  await noOverflow(page);
  const validity = await page.locator('.signal-validity').allTextContents();
  const verdicts = await page.locator('.signal-foot .chip:not(.signal-stale)').allTextContents();

  // The next actual polling tick recovers without navigation or manual refresh.
  const C = snapshot(0,{generated_at_utc:new Date(+date+3*60000).toISOString()});
  C.audit.collector_last_run_utc = C.generated_at_utc;
  C.brain.agenda = A.brain.agenda;
  remote.respond({report:C});
  await page.clock.fastForward(3*60000);
  await hasText(page,'#fresh','Atualizado em');
  assert.equal(await banner.isVisible(),false);
  assert.equal(await page.locator('.signal-stale:visible').count(),0);
  assert.equal(await cards.count(),before);
  assert.deepEqual(await page.locator('.signal-validity').allTextContents(),validity);
  assert.deepEqual(await page.locator('.signal-foot .chip:not(.signal-stale)').allTextContents(),verdicts);
  await go(page,'overview');
  assert.equal(await banner.isVisible(),false);
  remote.respond({report:{...C,brain:{...C.brain,agenda:{...C.brain.agenda,items:[]}}}});
  await refresh(page);
  await go(page,'sinais');
  assert.equal(await cards.count(),0);
  await hasText(page,'#agendalist','Sem sinais na janela de observação');
  assert.deepEqual(remote.errors,[]);
}

async function realReport(browser) {
  for (const [width,height] of sizes) {
    const page=await browser.newPage({viewport:{width,height},isMobile:width===375,hasTouch:width===375,
      colorScheme:width===768?'dark':'light'});
    const remote=await setup(page);
    await page.goto(root);
    await ready(page,base.unique_bets);
    await noOverflow(page);
    assert.equal(await page.locator('#overview').isVisible(),true);
    assert.equal(await page.locator('#hyps tr').count(),0);
    assert.equal(await page.locator('main [role="status"]:visible').count(),0);
    assert.equal(await page.getByRole('heading',{level:1,name:'GOAL-ROI'}).count(),1);
    assert.match(await page.locator('#overview').ariaSnapshot(),/Apostas acompanhadas/);
    assert.equal(await page.locator('html').getAttribute('data-theme'),width===768?'dark':'light');

    if(width===375) {
      await page.keyboard.press('Tab');
      const focused=await page.locator(':focus').textContent();
      assert.match(focused,/Saltar para o conteúdo/);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(':focus').getAttribute('id'),'conteudo');
    }
    // Representative foreground/underlay pairs across both color schemes.
    for(let i=0;i<2;i++) {
      const ratios=await page.evaluate(() => {
        const l = s => {
          const a = document.createElement('span'); a.style.color=s; document.body.append(a);
          const rgb=getComputedStyle(a).color.match(/\d+/g).slice(0,3).map(n=>{
            const x=n/255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4;
          });a.remove();return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
        };
        return [['.metric dt','.metric'],['.finding p','.finding'],['.hero-copy > p','.hero'],
          ['nav a[aria-current]','nav a[aria-current]'],['.fresh','.topbar']].map(([f,b])=>{
          const x=l(getComputedStyle(document.querySelector(f)).color), y=l(getComputedStyle(document.querySelector(b)).backgroundColor);
          return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
        });
      });
      assert.ok(ratios.every(r=>r>=4.5),JSON.stringify(ratios));
      await page.locator('#theme').click();
    }

    if(width===375) {
      const link=page.getByRole('navigation').getByRole('link',{name:'Sinais',exact:true});
      await link.focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.activeElement?.id === 'agendah');
      assert.equal(await link.getAttribute('aria-current'),'page');
    } else await go(page,'sinais');
    await noOverflow(page);
    const agenda=base.brain?.agenda;
    if(agenda) {
      const active=agenda.items.filter(a=>a.when==='por começar' ? Date.parse(a.kickoff_utc)>date :
        a.when==='a decorrer' && date-Date.parse(a.date_utc)>=0 && date-Date.parse(a.date_utc)<=agenda.rules.live_max_min*60000);
      assert.equal(await page.locator('.signal-card').count(),active.length);
      if(active.length) {
        const first=page.locator('.signal-card').first();
        assert.equal(await first.locator('h2').textContent(),active[0].match);
        for(const value of [active[0].family,active[0].market,active[0].odds.toFixed(2).replace('.',',')]) {
          assert.ok((await first.textContent()).includes(value));
        }
        const disclosure=first.locator('summary');
        await disclosure.focus();
        await page.keyboard.press('Tab');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await disclosure.evaluate(node=>node.matches(':focus-visible') && getComputedStyle(node).outlineStyle==='solid'),true);
        await page.keyboard.press('Enter');
        assert.equal(await first.locator('details').evaluate(e=>e.open),true);
      }
      if(active.some(a=>a.verdict==='contraditório')) await hasText(page,'#agendalist','Sinais contraditórios');
    }

    await go(page,'resultados');
    await noOverflow(page);
    const reference=base.questions?.[Q7];
    if(reference?.roi) {
      assert.equal(await page.locator('#metric-roi').textContent(),percent(reference.roi['odd-0%'].roi));
      await hasText(page,'#balance-note',percent(reference.roi['odd-0%'].roi));
      assert.deepEqual(await page.locator('#results-odds li b').allTextContents(),
        Object.values(reference.roi).map(v=>percent(v.roi)));
      assert.deepEqual(await page.locator('#results-outcomes li b').allTextContents(),
        [reference.wins,reference.settled-reference.wins-reference.pushes,reference.pushes,reference.bets-reference.settled].map(count));
    }
    if(base.timeline.length>=2) {
      const chart=page.locator('#chart').getByRole('img');
      assert.match(await chart.getAttribute('aria-label'),new RegExp(`ao longo de ${base.timeline.length} apostas`));
      assert.equal((await chart.locator('polyline').getAttribute('points')).split(' ').length,base.timeline.length+1);
      const slider=page.getByRole('slider',{name:'Consultar aposta no gráfico'});
      await slider.press('End');
      await hasText(page,'#chart-point',`saldo ${units(base.timeline.at(-1).u)} un.`);
      await slider.press('Home');
      await hasText(page,'#chart-point',`saldo ${units(base.timeline[0].u)} un.`);
      await noOverflow(page);
    }
    await expand(page,'dados-resultados');
    await page.waitForSelector('#groups tr');
    await noOverflow(page);
    const groups=Object.values(base.groups);
    assert.deepEqual(await page.locator('#groups tr td:nth-child(6)').allTextContents(),groups.map(g=>g.roi ? percent(g.roi['odd-0%'].roi) : '—'));
    await expand(page,'simulador');
    await page.waitForSelector('#simout dd');
    await verifySimulator(page,base.timeline);
    await noOverflow(page);

    await go(page,'laboratorio');
    for(const [id,selector] of [
      ['taticas','#best li'],['hipoteses','#hyps tr'],['estudo-chronologico','#wf tr'],
      ['propostas','#llm tr'],['prospetivo','#prospective tr'],['notas','#notes p'],['entradas','#entry tr'],
      ['sinais-hipoteses','#watch p'],['exploracao','#signals .row'],['faixas-odd','#calib > *'],
      ['comportamento','#regime tr'],['segmentos','#questions tr']
    ]) {
      await expand(page,id);
      await page.waitForSelector(selector);
      await noOverflow(page);
    }
    assert.equal(await page.locator('#wf tr').count(),base.brain.walk_forward.not_reconstructible?3:2);
    await go(page,'metodologia');
    for(const [id,child] of [['qualidade','#qc dd'],['por-liquidar','#uns tr'],['evidencia-operacional','#facts span']]){
      await expand(page,id);
      await page.waitForSelector(child);
      await noOverflow(page);
    }
    assert.equal(await page.getByRole('link',{name:'Descarregar CSV'}).getAttribute('href'),'bets.csv');
    assert.equal(await page.getByRole('link',{name:'Abrir JSON'}).getAttribute('href'),'report.json');
    assert.deepEqual(remote.errors,[]);
    await page.close();
  }
}

// The legacy fixture has no settlement clocks. All commitments reserve cash
// before any payout is available, then the observed outcomes are applied.
async function verifySimulator(page, rows) {
  async function compare(start, value, cut, fractional, excluded) {
    let uncommitted=start;
    const positions=[];
    for(const row of rows) {
      if(row.t===excluded || row.w==null) continue;
      const stake=Math.min(uncommitted,fractional ? uncommitted*value/100 : value);
      if(stake<=0) break;
      uncommitted -= stake;
      positions.push([row,stake]);
    }
    const n=positions.length, staked=start-uncommitted;
    let bank=start, peak=start, drawdown=0, low=start;
    for(const [row,stake] of positions) {
      bank += stake * (row.w>0 ? row.w*(row.o*(1-cut)-1) : row.w);
      peak=Math.max(peak,bank);
      low=Math.min(low,bank);
      drawdown=Math.max(drawdown,peak-bank);
    }
    assert.deepEqual(await page.locator('#simout dd').allTextContents(),[
      String(n),euro(bank),(bank>=start?'+':'')+euro(bank-start),
      staked ? percent((bank-start)/staked) : '—',euro(drawdown),euro(low)
    ]);
    if(n>=2) assert.equal((await page.locator('#simchart polyline').getAttribute('points')).split(' ').length,n+1);
  }
  await compare(1000,10,0,false);
  await page.fill('#simbank','257');
  await page.selectOption('#simmode','pct');
  await page.fill('#simval','5.5');
  await page.selectOption('#simcut','0.05');
  const first=page.locator('#simtypes input').first(), hasType=await first.count();
  const exclusion=hasType ? await first.inputValue() : undefined;
  if(hasType) await first.uncheck();
  await compare(257,5.5,.05,true,exclusion);
}

async function missingAndCertificates(browser, width, height) {
  const page=await browser.newPage({viewport:{width,height},timezoneId:'America/New_York'});
  const remote=await setup(page,{hold:true});
  await page.goto(root+'#simulador');
  await page.waitForSelector('#fresh');
  assert.match(await page.locator('#fresh').textContent(),/A carregar/);
  assert.equal(await page.locator('#conteudo').getAttribute('aria-busy'),'true');
  await page.waitForFunction(()=>document.querySelector('#simulador').open);
  assert.equal(remote.held.length,1);
  await remote.held[0].fulfill({status:500,body:'{}'});
  await ready(page);
  await hasText(page,'#load-state','Não foi possível carregar');
  await hasText(page,'#simchart','Nenhuma aposta liquidada');
  await noOverflow(page);

  const empty = {
    generated_at_utc:base.generated_at_utc, messages:0, unique_bets:0, settled_bets:0, first_signal_utc:null,
    min_settled:base.min_settled, audit:{last_collected_at_utc:null}, groups:{}, accuracy:{},
    timeline:[], daily:[], recent_bets:[],
    questions:{[Q7]:{bets:0,settled:0,wins:0,pushes:0,units:0,avg_odds:null,roi:null}}
  };
  remote.respond({report:empty});
  await page.getByRole('button',{name:'Tentar novamente'}).click();
  await ready(page);
  assert.equal(await page.locator('#load-state').isVisible(),false);
  await go(page,'sinais');
  await hasText(page,'#agendalist','não disponibiliza a agenda');
  await go(page,'overview');
  assert.equal(await page.locator('#metric-settled').textContent(),'0');
  assert.equal(await page.locator('#metric-roi').textContent(),'—');
  assert.equal(await page.locator('#metric-science').textContent(),'Indisponível');
  await go(page,'laboratorio');
  await expand(page,'hipoteses');
  await hasText(page,'#hyps','Nenhuma hipótese');
  await noOverflow(page);

  const hypothesis={...base.brain.top[0],id:'fixture-1',status:'forte'};
  const signal={
    match_id:'x',match:'Equipa A — Equipa B',market:'1X2',selection:'Equipa A',family:'Betfair',type:'BETFAIR',odds:1.85,bad:[],
    when:'por começar',phase:'pre',kickoff_utc:new Date(+date+3600000).toISOString(),
    date_utc:date.toISOString(),verdict:'favorável',validated:true,good:[hypothesis]
  };
  const live={...signal,match_id:'live',match:'Equipa C — Equipa D',kickoff_utc:null,when:'a decorrer',
    phase:'live',minute:33,verdict:'sem evidência',validated:false,good:[]};
  const expired={...live,match_id:'old',match:'Jogo expirado',date_utc:new Date(date-31*60000).toISOString()};
  const validation={method:'eprocess_online_alpha_v1',state:'active',ledger_valid:true,certified:['fixture-1']};
  const make = (v,h=[]) => snapshot(0,{brain:{counts:{forte:1},top:h,validation:v,
    agenda:{items:[signal,live,expired],rules:{min_results:10,live_max_min:30},generated_utc:date.toISOString()}}});
  const proof={...hypothesis,confirmation:{certifiable:true,rank:1,certified_utc:date.toISOString()}};

  for(const fixture of [make({...validation,state:'not_activated'},[proof]),make(validation,[hypothesis]),
      make(validation,[{...proof,id:'outra'}]),make({...validation,ledger_valid:false},[proof])]) {
    remote.respond({report:fixture});
    await refresh(page);
    assert.equal(await page.locator('#hyps .chip.ok').count(),0);
    assert.doesNotMatch(await page.locator('#metric-science-note').textContent(),/1 tática/);
    await go(page,'sinais');
    await hasText(page,'#agendalist','Indício favorável');
    assert.equal(await page.locator('#agendalist .chip.ok').count(),0);
    assert.equal(await page.locator('.signal-card').count(),2);
    assert.doesNotMatch(await page.locator('#agendalist').textContent(),/Jogo expirado/);
    await go(page,'laboratorio');
  }
  remote.respond({report:make(validation,[proof])});
  await refresh(page);
  await hasText(page,'#metric-science-note','1 tática certificada');
  assert.match(await page.locator('#hyps .chip.ok').textContent(),/Forte/);
  await go(page,'sinais');
  await hasText(page,'#agendalist','Tática certificada');
  await page.locator('[name="signal-phase"][value="a decorrer"] + span').click();
  assert.equal(await page.locator('.signal-card').count(),1);
  await hasText(page,'#agendalist','Ao vivo');
  await page.selectOption('#signal-status','certificado');
  await hasText(page,'#agendalist','Nenhum sinal corresponde');
  await page.selectOption('#signal-status','');
  await page.clock.setFixedTime(new Date(+date+31*60000));
  await refresh(page);
  assert.equal(await page.locator('.signal-card').count(),0);
  await page.locator('[name="signal-phase"][value=""] + span').click();
  assert.equal(await page.locator('.signal-card').count(),1);
  await hasText(page,'#agendalist','Equipa A');
  assert.deepEqual(remote.errors,[]);
  await page.close();
}

async function simultaneousExposure(browser) {
  const page = await browser.newPage();
  try {
    const stamp = minutes => new Date(+date + minutes * 60000).toISOString();
    const bet = (placed, paid) => ({d:stamp(placed), z:stamp(paid), o:2, w:1, t:'Teste'});
    const simulation = {total_bets:3, timed_settlements:3, committed_games:2, max_units_per_game:2,
      entries:[bet(-90,-60), bet(-90,-60), bet(-30,-10)]};
    const report = snapshot(1, {simulation});
    await setup(page, {report});
    await page.goto(root + '#simulador');
    await ready(page, report.unique_bets);
    await page.locator('#simbank').fill('100');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('100');
    assert.equal(await page.locator('#simout dd').first().textContent(), '2');
    assert.equal(await page.locator('#simout dd').nth(1).textContent(), euro(400));
    await hasText(page, '#sim-exposure', '1 oportunidades sem saldo disponível');
  } finally { await page.close(); }
}

async function w09TimedEntries(browser) {
  const page = await browser.newPage();
  try {
    const stamp = minutes => new Date(+date + minutes * 60_000).toISOString();
    const entry = {d:stamp(-90), z:stamp(-60), o:2, w:1, t:'Caso auditado'};
    const other = {...entry, d:stamp(-45), z:stamp(-30), t:'Restantes'};
    const simulation = {total_bets:3, timed_settlements:3, committed_games:3, max_units_per_game:1,
      entries:[entry, {...entry}, other]};
    const timeline = base.timeline.map(row => ({...row, t:'Apenas no formato histórico'}));
    const report = snapshot(1, {simulation, timeline});
    report.audit.ingestion_gaps = {count:1};
    const remote = await setup(page, {report});
    await page.goto(root + '#simulador');
    await ready(page, report.unique_bets);
    assert.equal(await page.locator('#simtypes input[value="Caso auditado"]').count(), 1);
    assert.equal(await page.locator('#simtypes input[value="Apenas no formato histórico"]').count(), 0);
    await page.locator('#simtypes input[value="Restantes"]').uncheck();
    await page.locator('#simbank').fill('100');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('100');
    assert.equal(await page.locator('#simout dd').first().textContent(), '1');
    assert.equal(await page.locator('#simout dd').nth(1).textContent(), euro(200));
    await hasText(page, '#sim-exposure', '1 oportunidades sem saldo disponível');

    const corrected = {...simulation, entries:[{...entry, w:-1, z:stamp(-55)}, {...entry}, other]};
    remote.respond({report:{...report, simulation:corrected}});
    await refresh(page);
    assert.equal(await page.locator('#simout dd').first().textContent(), '1');
    assert.equal(await page.locator('#simout dd').nth(1).textContent(), euro(0));
    await hasText(page, '#sim-exposure', '1 oportunidades sem saldo disponível');
    assert.deepEqual(remote.errors, []);
    await go(page, 'metodologia');
    await expand(page, 'qualidade');
    await hasText(page, '#qc', 'lacunas de ingestão com histórico Telegram por confirmar');
    console.log('W09 simulation.entries passed (one reserve, original and corrected payouts)');
  } finally { await page.close(); }
}

async function monetaryPrecision(browser) {
  const page = await browser.newPage();
  try {
    const stamp = minutes => new Date(+date + minutes * 60_000).toISOString();
    const losing = {d:stamp(-90), z:stamp(-60), o:3, w:-1, t:'Precisão'};
    const simulation = {total_bets:1, timed_settlements:1, committed_games:1, max_units_per_game:1, entries:[losing]};
    const report = snapshot(1, {simulation});
    const remote = await setup(page, {report});
    await page.goto(root + '#simulador');
    await ready(page, report.unique_bets);
    await page.locator('#simbank').fill('257');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('5.5');
    await page.locator('#simcut').selectOption('0.05');
    const values = page.locator('#simout dd');
    assert.deepEqual(await values.allTextContents(), ['1', '242,87 €', '-14,14 €', '-100,0 %', '14,14 €', '242,87 €']);
    await hasText(page, '#sim-exposure', 'Saldo máximo reservado 14,14 € · em aberto no fim 0,00 €');

    const corrected = {...simulation, entries:[{...losing, w:1, z:stamp(-55)}]};
    remote.respond({report:{...report, simulation:corrected}});
    await refresh(page);
    assert.deepEqual(await values.allTextContents(), ['1', '283,15 €', '+26,15 €', '185,0 %', '0,00 €', '257,00 €']);
    assert.deepEqual(await page.evaluate(() => ['0.004999', '0.005', '0.005001', '-0.004999', '-0.005', '-0.005001']
      .map(value => simulationEuros(SimulationDecimal.from(value)))),
    ['0,00 €', '0,01 €', '0,01 €', '0,00 €', '-0,01 €', '-0,01 €']);
    assert.deepEqual(remote.errors, []);
    console.log('Monetary precision passed (positive and negative half-cent boundaries, reserve and revised payout)');
  } finally { await page.close(); }
}

async function settlementCorrections(browser) {
  // Adversarial FT corrections: every figure below is worked out by hand from a 100 € bank,
  // 100 % stakes, decimal odds 2 and no odds cut.
  const page = await browser.newPage();
  try {
    const stamp = minutes => new Date(+date + minutes * 60_000).toISOString();
    const remote = await setup(page, {report:snapshot(0)});
    await page.goto(root + '#simulador');
    await ready(page, base.unique_bets);
    const run = (rows, until) => page.evaluate(([rows, until]) => {
      const r = simulatePortfolio(rows, {start:'100', mode:'pct', val:'100', cut:'0'}, until === null ? Infinity : Date.parse(until));
      const e = v => (Number(v.rounded(2)) / 100).toFixed(2);
      return {cash:e(r.cash), reserved:e(r.reserved), realized:e(r.realized), n:r.n, skipped:r.skipped,
              revisions:r.revisions, curve:r.curve.map(p => [p.at, e(p.balance)])};
    }, [rows, until]);
    const A = s => ({d:stamp(-90), z:s.at(-1)[1] === null ? null : s.at(-1)[0], w:s.at(-1)[1], o:2, t:'A', s});
    const B = {d:stamp(-40), z:stamp(-30), w:1, o:2, t:'B'};

    // 1. Won, then corrected to lost. At -40 the system knew A had won: B is staked with 200 € and wins.
    const winToLoss = A([[stamp(-60), 1], [stamp(-20), -1]]);
    let r = await run([winToLoss, B], null);
    assert.deepEqual([r.realized, r.cash, r.reserved, r.n, r.skipped, r.revisions], ['200.00', '200.00', '0.00', 2, 0, 1]);
    // The same report cut before the correction equals the report published before it existed.
    const published = {...winToLoss, z:stamp(-60), w:1}; delete published.s;
    const before = await run([published, B], stamp(-25)), cut = await run([winToLoss, B], stamp(-25));
    assert.deepEqual(cut, before);
    assert.equal(before.realized, '400.00');
    assert.deepEqual(r.curve.slice(0, before.curve.length), before.curve, 'a correction must not rewrite the past curve');

    // 2. Lost, then corrected to won. At -40 nothing was available: B is skipped; the win arrives at -20.
    r = await run([A([[stamp(-60), -1], [stamp(-20), 1]]), B], null);
    assert.deepEqual([r.realized, r.cash, r.reserved, r.n, r.skipped, r.revisions], ['200.00', '200.00', '0.00', 1, 1, 1]);

    // 3. Settled, then contradictory (quarantine), then lost: the stake is reserved again, never paid twice.
    const quarantined = A([[stamp(-60), 1], [stamp(-20), null], [stamp(-10), -1]]);
    assert.deepEqual(await run([quarantined], stamp(-15)).then(x => [x.cash, x.reserved, x.realized]), ['0.00', '100.00', '100.00']);
    r = await run([quarantined], null);
    assert.deepEqual([r.cash, r.reserved, r.realized], ['0.00', '0.00', '0.00']);
    const stillOpen = await run([A([[stamp(-60), 1], [stamp(-20), null]])], null);
    assert.deepEqual([stillOpen.cash, stillOpen.reserved, stillOpen.realized], ['0.00', '100.00', '100.00']);

    // 4. Winnings already staked, then reversed: the available balance goes negative and blocks new stakes;
    // the realized balance counts the open stake at cost.
    const C = {d:stamp(-15), z:stamp(-5), w:1, o:2, t:'C'};
    const pendingB = {...B, z:null, w:null};
    r = await run([winToLoss, pendingB, C], null);
    assert.deepEqual([r.cash, r.reserved, r.realized, r.n, r.skipped], ['-200.00', '200.00', '0.00', 2, 1]);

    // 5. A payoff known at the very instant of another decision is not visible to it.
    r = await run([{...winToLoss, s:[[stamp(-40), 1]], z:stamp(-40), w:1}, B], null);
    assert.deepEqual([r.n, r.skipped, r.realized], [1, 1, '200.00']);

    // 6. Deterministic: repeated and reordered replays (distinct instants) give the same result.
    const rows = [winToLoss, B, C];
    const once = await run(rows, null);
    assert.deepEqual(await run([...rows].reverse(), null), once);
    assert.deepEqual(await run(rows, null), once);

    // 7. The dashboard shows realized, reserved and available balances, and the correction count.
    const simulation = {total_bets:2, timed_settlements:2, committed_games:2, max_units_per_game:1, entries:[winToLoss, B]};
    const report = snapshot(0, {simulation});
    remote.respond({report});
    await refresh(page);
    await page.locator('#simbank').fill('100');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('100');
    assert.equal(await page.locator('#simout dd').nth(1).textContent(), euro(200));
    await hasText(page, '#sim-exposure', 'Saldo realizado 200,00 € · capital reservado 0,00 € · saldo disponível 200,00 €');
    await hasText(page, '#sim-exposure', '1 correções de resultado aplicadas na data em que foram conhecidas');

    // 8. Histories that do not match the payoff in force, go backwards or repeat a payoff are rejected.
    for (const bad of [{...winToLoss, w:1, z:stamp(-60)}, {...winToLoss, s:[[stamp(-20), 1], [stamp(-60), -1]]},
                       {...winToLoss, s:[[stamp(-60), -1], [stamp(-20), -1]]}, {...winToLoss, s:[[stamp(-95), -1]], z:stamp(-95)},
                       {...winToLoss, s:[[stamp(-60), 1], [stamp(10), -1]], z:stamp(10)}]) {
      remote.respond({report:{...report, simulation:{...simulation, entries:[bad, B]}}});
      await refresh(page);
      await hasText(page, '#fresh', 'Falha ao atualizar');
      assert.equal(await page.locator('#simout dd').nth(1).textContent(), euro(200));
    }
    assert.deepEqual(remote.errors, []);
    console.log('Settlement corrections passed (win to loss, loss to win, quarantine, negative balance, invariance)');
  } finally { await page.close(); }
}

async function simulationReportClock(browser) {
  const page = await browser.newPage();
  try {
    const stamp = minutes => new Date(+date + minutes * 60_000).toISOString();
    const settled = {d:stamp(-30), z:stamp(-20), w:1, o:2, t:'Teste'};
    const simulation = {total_bets:1, timed_settlements:1, committed_games:1, max_units_per_game:1, entries:[settled]};
    const report = snapshot(0, {simulation});
    const remote = await setup(page, {report});
    await page.goto(root + '#simulador');
    await ready(page, report.unique_bets);
    await page.locator('#simbank').fill('100');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('100');
    const balance = () => page.locator('#simout dd').nth(1).textContent();
    const legacy = {...report};
    delete legacy.simulation;
    const invalid = [
      {...report, simulation:{...simulation, entries:[{...settled, z:stamp(10)}]}},
      {...report, simulation:{...simulation, timed_settlements:0, entries:[{...settled, d:stamp(10), z:null, w:null}]}},
      legacy,
    ];
    for (const rejected of invalid) {
      assert.equal(await balance(), euro(200));
      remote.respond({report:rejected});
      await refresh(page);
      await hasText(page, '#fresh', 'Falha ao atualizar');
      await hasText(page, '#sim-evidence', '1 liquidações com hora comprovada');
      assert.equal(await balance(), euro(200));
      remote.respond({report});
      await refresh(page);
      await hasText(page, '#fresh', 'Atualizado em');
    }
    assert.deepEqual(remote.errors, []);
  } finally { await page.close(); }
}

(async () => {
  const browser=await chromium.launch(process.env.DASHBOARD_CHROMIUM ?
    {executablePath:process.env.DASHBOARD_CHROMIUM} : {});
  try {
    if (process.env.GOAL_ROI_W09_TIMED_ONLY === '1') {
      assert.ok(base.simulation?.entries.length, 'the regenerated report must contain simulation.entries');
      await w09TimedEntries(browser);
      return;
    }
    const regressions = [];
    for (const [width,height] of sizes) {
      for (const check of [refreshRegression, collectionWarning]) {
        const page = await browser.newPage({viewport:{width,height},isMobile:width===375,hasTouch:width===375,
          timezoneId:'America/New_York',colorScheme:width===768?'dark':'light'});
        try { await check(page,width,height); }
        catch (error) { regressions.push(new Error(`${check.name} at ${width}px`, {cause:error})); }
        finally { await page.close(); }
      }
    }
    if (regressions.length) throw new AggregateError(regressions,'Dashboard audit regressions failed');
    await realReport(browser);
    await simultaneousExposure(browser);
    await w09TimedEntries(browser);
    await monetaryPrecision(browser);
    await settlementCorrections(browser);
    await simulationReportClock(browser);
    for (const [width,height] of sizes) await missingAndCertificates(browser,width,height);
    console.log('Dashboard refresh regressions passed');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});

// Regenerate the comparative captures and viewport word counts with a fixed clock.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const repo = path.resolve(__dirname,'../..');
const base = process.argv[2] || '20a24d8a43485e5043b5ac79d3986109de6a6962';
const original = name => execFileSync('git',['show',`${base}:docs/${name}`],{cwd:repo});
const assets = {
  'index.html':'text/html', 'dashboard.css':'text/css', 'dashboard.js':'text/javascript',
  'report.json':'application/json'
};
const data = original('report.json');
const report = JSON.parse(data);
const origin = 'http://goal-roi.test/';

async function openPage(browser, before, viewport) {
  const page = await browser.newPage({
    viewport, colorScheme:'light', isMobile:viewport.width===375, hasTouch:viewport.width===375
  });
  await page.clock.setFixedTime(new Date(report.generated_at_utc));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const name = url.pathname.slice(1) || 'index.html';
    if (url.origin !== new URL(origin).origin || !Object.hasOwn(assets,name)) return route.abort();
    if (before && name !== 'report.json' && name !== 'index.html') return route.abort();
    const body = name === 'report.json' ? data : before ? original(name) : fs.readFileSync(path.join(repo,'docs',name));
    return route.fulfill({contentType:assets[name], body});
  });
  await page.goto(origin);
  await page.waitForFunction(() => document.querySelector('#facts')?.children.length);
  return page;
}

async function measure(page) {
  return page.evaluate(() => {
    const word = /[\p{L}\p{N}]+(?:[.,-][\p{L}\p{N}]+)*/gu;
    const walk = document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let visible=0, viewport=0;
    while (walk.nextNode()) {
      const node=walk.currentNode, parent=node.parentElement;
      if (!node.textContent.trim() || parent.closest('script,style,noscript,.sr-only,.skip-link,[hidden]')) continue;
      const closed=parent.closest('details:not([open])');
      if (closed && !parent.closest('summary')) continue;
      const range=document.createRange();
      range.selectNodeContents(node);
      if (![...range.getClientRects()].some(r=>r.width&&r.height)) continue;
      for (const match of node.textContent.matchAll(word)) {
        visible++;
        range.setStart(node,match.index);
        range.setEnd(node,match.index+match[0].length);
        if ([...range.getClientRects()].some(r=>r.width&&r.height&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth)) viewport++;
      }
    }
    return {visible,viewport};
  });
}

async function capture(page, prefix, section) {
  console.log(prefix,section,await measure(page));
  await page.screenshot({path:path.join(__dirname,'captures',`${prefix}-${section}.png`),animations:'disabled'});
}

async function comparison(before, after) {
  await after.locator('nav a[href="#resultados"]').click();
  await after.locator('#simulador summary').click();
  await after.locator('#dados-resultados summary').click();
  await after.waitForSelector('#groups tr');
  await after.waitForSelector('#simout dd');
  for (const id of ['groups','daily']) {
    assert.deepEqual(await after.locator(`#${id} td`).allTextContents(),
      await before.locator(`#${id} td`).allTextContents());
  }
  for (const config of [null,{bank:'257',mode:'pct',value:'5.5',cut:'0.05'}]) {
    for (const page of [before,after]) {
      if (!config) continue;
      await page.fill('#simbank',config.bank);
      await page.selectOption('#simmode',config.mode);
      await page.fill('#simval',config.value);
      await page.selectOption('#simcut',config.cut);
      await page.locator('#simtypes input').first().uncheck();
    }
    assert.deepEqual(await after.locator('#simout dd').allTextContents(),await before.locator('#simout dd').allTextContents());
    const y = async page => (await page.locator('#simchart polyline').getAttribute('points')).split(' ').map(p=>p.split(',')[1]);
    assert.deepEqual(await y(after),await y(before));
  }
  console.log('Parity with original dashboard: group and daily tables, simulator results and curve');
}

(async () => {
  fs.mkdirSync(path.join(__dirname,'captures'),{recursive:true});
  const browser = await chromium.launch(process.env.DASHBOARD_CHROMIUM ? {executablePath:process.env.DASHBOARD_CHROMIUM} : {});
  try {
    for (const [device,width,height] of [['desktop',1440,1000],['mobile',375,812]]) {
      const before = await openPage(browser,true,{width,height});
      const after = await openPage(browser,false,{width,height});
      await capture(before,`before-${device}`,'overview');
      await capture(after,`after-${device}`,'overview');
      for (const section of ['sinais','resultados','laboratorio']) {
        await after.locator(`nav a[href="#${section}"]`).click();
        await after.waitForFunction(id => !document.getElementById(id).hidden, section);
        await capture(after,`after-${device}`,section);
      }
      if (device === 'desktop') await comparison(before, after);
      await before.close();
      await after.close();
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});

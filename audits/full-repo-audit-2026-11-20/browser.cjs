// Playwright audit. Every request is fulfilled from disk or aborted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {performance} = require('node:perf_hooks');
const {chromium} = require('playwright');

const root = 'https://goal-roi-audit.invalid/';
const docs = path.resolve(__dirname, '../../docs');
const types = {'index.html':'text/html', 'dashboard.css':'text/css',
               'dashboard.js':'application/javascript', 'report.json':'application/json'};
const reference = 'Q7 Todas as apostas (referência)';
const label = (value, digits) => (value * 100).toFixed(digits).replace('.', ',') + ' %';
const count = value => new Intl.NumberFormat('pt-PT').format(value);
const load = directory => JSON.parse(fs.readFileSync(path.join(directory, 'report.json'), 'utf8'));

async function pageOf(browser, directory, {width=1440, body, dark=false, now, start=''}={}) {
  const page = await browser.newPage({viewport:{width, height:900},
                                     colorScheme:dark ? 'dark' : 'light'});
  const doc = load(directory);
  const clock = new Date(now ?? doc.generated_at_utc);
  await page.clock.install({time:clock});
  await page.clock.pauseAt(new Date(+clock + 100));
  const changes = {body, errors:[], reads:0};
  page.on('pageerror', error => changes.errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const filename = url.pathname.slice(1) || 'index.html';
    if (url.origin !== new URL(root).origin || !Object.hasOwn(types, filename)) {
      return route.abort();
    }
    if (filename === 'report.json') {
      changes.reads++;
      if (changes.body !== undefined) {
        return route.fulfill({contentType:types[filename], body:changes.body});
      }
    }
    return route.fulfill({contentType:types[filename], path:path.join(directory,filename)});
  });
  const began = performance.now();
  await page.goto(root + start, {waitUntil:'domcontentloaded'});
  return {page, doc, changes, began};
}

async function ready(page) {
  await page.waitForFunction(() => document.querySelector('main').getAttribute('aria-busy') === 'false',
                             undefined, {timeout:5000});
}

async function display(browser, directory) {
  const {page, doc, changes, began} = await pageOf(browser, directory);
  try {
    await ready(page);
    const summary = doc.questions[reference];
    assert.equal(await page.locator('#metric-tracked').innerText(), count(doc.unique_bets));
    assert.equal(await page.locator('#metric-settled').innerText(), count(doc.settled_bets));
    assert.equal(await page.locator('#metric-roi').innerText(), label(summary.roi['odd-0%'].roi, 1));
    assert.equal(await page.locator('#metric-science').innerText(), 'Inativa');
    assert.match(await page.locator('#overview-conclusion').innerText(), /por demonstrar/);
    assert.equal(changes.errors.length, 0);
    return 'apostas=' + doc.unique_bets + ', liquidadas=' + doc.settled_bets +
           ', ROI=' + label(summary.roi['odd-0%'].roi, 1) + ', DOM em ' +
           Math.round(performance.now() - began) + ' ms (só local)';
  } finally {
    await page.close();
  }
}

async function layout(browser) {
  for (const width of [320, 375, 1440]) {
    const {page, changes} = await pageOf(browser, docs, {width});
    try {
      await ready(page);
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.getAttribute('href')), '#conteudo');
      await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'conteudo');
      for (const view of ['sinais','resultados','laboratorio','metodologia']) {
        await page.locator('nav a[href="#' + view + '"]').click();
        await page.locator('#' + view).waitFor({state:'visible'});
        assert.equal(await page.locator('nav a[aria-current="page"]').getAttribute('href'), '#' + view);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
                     'overflow horizontal na página ' + view + ' a ' + width + ' px');
        if (view === 'sinais') {
          assert.equal(await page.locator('[name="signal-phase"]').count(), 3);
          await page.locator('.phase-picker label').last().click();
        }
        if (view === 'resultados') {
          await page.locator('#chart-position').focus();
          assert.match(await page.locator('#chart-point').textContent(), /Aposta/i);
          await page.locator('#simulador summary').click();
          await page.locator('#simout dt').first().waitFor({timeout:3000});
          assert.equal(await page.locator('#simout dt').count(), 6);
        }
      }
      assert.equal(changes.errors.length, 0);
    } finally {
      await page.close();
    }
  }
  return '320, 375 e 1440 px, teclado, filtros, gráfico e simulador';
}

async function interrupted(browser) {
  const {page, changes} = await pageOf(browser, docs);
  try {
    await ready(page);
    const old = await page.locator('#metric-roi').innerText();
    changes.body = '{"generated_at_utc":';
    await page.clock.fastForward(181000);
    await page.waitForFunction(() => document.querySelector('#fresh').textContent.includes('Falha ao atualizar'),
                               undefined, {timeout:5000});
    assert.equal(await page.locator('#metric-roi').innerText(), old);
    assert.equal(changes.reads, 2);
  } finally {
    await page.close();
  }
}

async function oldReport(browser, future) {
  const doc = load(docs);
  const now = Date.parse(doc.generated_at_utc);
  doc.audit.collector_last_run_utc = new Date(now + (future ? 86400000 : -60000)).toISOString();
  if (!future) doc.generated_at_utc = new Date(now - 6 * 3600000).toISOString();
  const {page} = await pageOf(browser, docs, {now, body:JSON.stringify(doc)});
  try {
    await ready(page);
    const freshness = (await page.locator('#fresh').innerText()).trim();
    assert.ok(!freshness.startsWith('Atualizado em'), 'estado incorreto: ' + freshness);
  } finally {
    await page.close();
  }
}

async function malformedDeepSection(browser) {
  const doc = load(docs);
  const row = {id:'audit', conditions:[], family:'Audit', level:'', n:8, hits:5, roi:.1, break_even:.5};
  doc.brain.board.best = [row];
  delete doc.brain.board.min_results;
  const {page, changes} = await pageOf(browser, docs, {body:JSON.stringify(doc), start:'#laboratorio'});
  try {
    await ready(page);
    await page.locator('a[href="#metodologia"]').first().click();
    await page.evaluate(() => { location.hash = 'taticas'; });
    await page.locator('#taticas[open]').waitFor({timeout:3000});
    await page.clock.runFor(100);
    assert.equal(changes.errors.length, 0, 'erro de renderização: ' + changes.errors[0]);
    assert.ok(!(await page.locator('#boardnote').innerText()).includes('undefined'),
              'ranking mostra «undefined jogos independentes» com secção interna incompleta');
    assert.match(await page.locator('#fresh').innerText(), /Falha|indisponível/i);
  } finally {
    await page.close();
  }
}

async function contrast(browser) {
  let minimum = Infinity;
  for (const dark of [false, true]) {
    const {page} = await pageOf(browser, docs, {dark});
    try {
      await ready(page);
      const ratios = await page.evaluate(() => {
        const selectors = ['#fresh span', '.metrics dt', '.metrics dd', '#overview-conclusion', 'nav a'];
        function channels(text) { return text.match(/[\d.]+/g).map(Number); }
        function luminance(rgb) {
          return rgb.slice(0, 3).map(v => (v /= 255) <= .04045 ? v / 12.92 : ((v + .055) / 1.055)**2.4)
            .reduce((sum, v, i) => sum + [0.2126, 0.7152, 0.0722][i] * v, 0);
        }
        return selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(node => {
          const fg = channels(getComputedStyle(node).color);
          let bgNode = node;
          let bg = channels(getComputedStyle(bgNode).backgroundColor);
          while (bg[3] === 0 && bgNode.parentElement) {
            bgNode = bgNode.parentElement;
            bg = channels(getComputedStyle(bgNode).backgroundColor);
          }
          if (bg[3] !== undefined) return null;
          const levels = [luminance(fg), luminance(bg)];
          return (Math.max(...levels) + .05) / (Math.min(...levels) + .05);
        })).filter(Number.isFinite);
      });
      assert.ok(ratios.length >= 10);
      minimum = Math.min(minimum, ...ratios);
    } finally {
      await page.close();
    }
  }
  assert.ok(minimum >= 4.5, 'contraste mínimo: ' + minimum.toFixed(2));
  return 'mínimo ' + minimum.toFixed(2) + ':1 nos seletores medidos';
}

async function overlappingBets(browser) {
  const doc = load(docs);
  doc.timeline = doc.timeline.map((entry, i) => i < 2 ?
    {...entry, t:'Caso auditado', d:'2026-11-20T12:00:00Z', o:2, w:1, g:1, u:i+1} :
    {...entry, t:'Restantes'});
  const {page} = await pageOf(browser, docs, {body:JSON.stringify(doc), start:'#simulador'});
  try {
    await ready(page);
    await page.locator('#simout dt').first().waitFor({timeout:3000});
    await page.locator('#simtypes input[value="Restantes"]').uncheck();
    await page.locator('#simbank').fill('100');
    await page.locator('#simmode').selectOption('pct');
    await page.locator('#simval').fill('100');
    const simulated = (await page.locator('#simout dd').nth(1).innerText()).trim();
    assert.ok(!simulated.startsWith('400'), 'duas apostas simultâneas com banca de 100 € produziram ' + simulated);
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch(process.env.DASHBOARD_CHROMIUM ?
    {executablePath:process.env.DASHBOARD_CHROMIUM, args:['--disable-dev-shm-usage']} : {});
  const cases = [
    ['W01', 'indicadores e certificação do SHA fixado', () => display(browser,docs)],
    ['W02', 'navegação responsiva e acesso por teclado', () => layout(browser)],
    ['W03', 'JSON cortado durante atualização automática', () => interrupted(browser)],
    ['W04', 'relatório com seis horas e carimbo de recolha recente', () => oldReport(browser,false)],
    ['W05', 'carimbo de recolha no futuro', () => oldReport(browser,true)],
    ['W06', 'secção interna incompleta do JSON', () => malformedDeepSection(browser)],
    ['W07', 'contraste em texto selecionado', () => contrast(browser)],
    ['W09', 'banca bloqueada para apostas simultâneas', () => overlappingBets(browser)]
  ];
  if (process.env.GOAL_ROI_PUBLIC_SNAPSHOT) {
    cases.splice(7, 0, ['W08', 'indicadores do JSON publicado capturado',
                        () => display(browser,process.env.GOAL_ROI_PUBLIC_SNAPSHOT)]);
  }
  let failed = false;
  try {
    for (const [id, title, check] of cases) {
      try {
        const evidence = await check();
        console.log(id, 'PASSOU', title + (evidence ? ' — ' + evidence : ''));
      } catch (error) {
        const status = error instanceof assert.AssertionError ? 'FALHOU' : 'NÃO VERIFICADO';
        console.log(id, status, title + ' — ' + error.message.replace(/\s+/g,' ').slice(0,300));
        if (process.env.GOAL_ROI_AUDIT_TRACE) console.error(error.stack);
        failed = true;
      }
    }
  } finally {
    await browser.close();
  }
  process.exitCode = Number(failed);
})().catch(error => { console.error(error.message); process.exitCode = 2; });

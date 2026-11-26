// Regressões de navegador do Goal-ROI Intelligence V6 (Chromium/Playwright, servidor local, sem rede externa).
//   node tests/v6_dashboard.cjs            -> testes funcionais e adversariais
//   V6_SHOTS=dir node tests/v6_dashboard.cjs -> também grava capturas (telemóvel, tablet, desktop × claro, escuro)
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {start, ROOT, V6_CONFIG} = require('./v6_server.cjs');
const core = require('../docs/v6/v6-core.js');
const {build} = require('../scripts/build_dashboard_snapshots.cjs');

const REPORT_PATH = process.env.V6_REPORT || path.join(ROOT, 'docs/report.json');
const reportText = fs.readFileSync(REPORT_PATH, 'utf8');
const base = JSON.parse(reportText);
const prospective = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/prospective_report.json'), 'utf8'));
const VIEWS = ['command', 'market', 'radar', 'brain', 'lab', 'performance', 'operations', 'knowledge'];
const SIZES = {telemovel: {width: 375, height: 812, isMobile: true, hasTouch: true}, tablet: {width: 768, height: 1024}, desktop: {width: 1440, height: 1000}};
const SHOTS = process.env.V6_SHOTS;
// Relógio fixo dez minutos depois da geração do relatório: a frescura e as idades ficam estáveis.
const NOW = Date.parse(base.generated_at_utc) + 10 * 60000;

function variant(minutesAgo, mutate) {
  const r = structuredClone(base);
  const t = new Date(NOW - minutesAgo * 60000).toISOString();
  r.audit.collector_last_run_utc = t; r.audit.last_collected_at_utc = t;
  if (mutate) mutate(r);
  return JSON.stringify(r);
}

async function open(browser, srv, opts = {}) {
  const ctx = await browser.newContext(Object.assign({viewport: {width: 1440, height: 1000}, colorScheme: opts.scheme || 'light'}, opts.device || {}));
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Violações de CSP e outros erros de consola contam como falhas (os 404 de rede esperados não).
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push('consola: ' + m.text()); });
  await page.addInitScript(([cfg, now]) => {
    window.V6_CONFIG = cfg;
    const RealDate = Date, offset = now - RealDate.now();
    class FixedDate extends RealDate { constructor(...a) { super(...(a.length ? a : [RealDate.now() + offset])); } static now() { return RealDate.now() + offset; } }
    window.Date = FixedDate;
  }, [Object.assign({}, V6_CONFIG, opts.config || {}), NOW]);
  if (opts.wideFonts) await page.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
    const st = document.createElement('style');
    st.textContent = ':root{--sans:"DejaVu Sans",Verdana,sans-serif !important;--mono:"DejaVu Sans Mono",monospace !important}';
    document.head.appendChild(st);
  }));
  await page.goto(srv.base + '/v6/' + (opts.hash ? '#' + opts.hash : ''));
  if (opts.wait !== false) await page.waitForFunction(() => window.__V6 && window.__V6.state.mode && !window.__V6.state.busy, null, {timeout: 30000});
  return {ctx, page, errors};
}
const mode = page => page.evaluate(() => window.__V6.state.mode);
const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
// Em emulação móvel o viewport de layout alarga-se ao conteúdo: compara-se com a largura real do dispositivo.
const fits = (page, width) => page.evaluate(w => document.documentElement.scrollWidth <= w + 1 && window.innerWidth <= w + 1, width);

async function visual(browser, srv) {
  for (const [name, device] of Object.entries(SIZES)) for (const scheme of ['light', 'dark']) {
    const {ctx, page, errors} = await open(browser, srv, {device: {viewport: {width: device.width, height: device.height}, isMobile: device.isMobile, hasTouch: device.hasTouch, deviceScaleFactor: SHOTS ? 2 : 1}, scheme});
    for (const v of VIEWS) {
      await page.evaluate(h => { location.hash = h; }, v);
      await page.waitForFunction(h => !document.getElementById('view-' + h).hidden && document.querySelector('#view-' + h + ' h2'), v);
      assert.ok(await fits(page, device.width), 'scroll horizontal em ' + v + ' ' + name);
      const text = await page.locator('#view-' + v).innerText();
      assert.ok(!/undefined|NaN|\[object Object\]/.test(text), 'texto inválido em ' + v + ' ' + name + ': ' + (text.match(/.{0,40}(undefined|NaN|\[object Object\]).{0,20}/) || [''])[0]);
      if (SHOTS) await page.screenshot({path: path.join(SHOTS, `${v}-${name}-${scheme === 'light' ? 'claro' : 'escuro'}.png`), fullPage: false});
    }
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')), scheme);
    assert.deepEqual(errors, []);
    await ctx.close();
  }
}

// Android estreito: nenhuma vista, gaveta ou separador pode criar scroll horizontal entre 320 e 430 px.
async function narrow(browser, srv) {
  for (const width of [320, 340, 360, 375, 390, 412, 430]) for (const scheme of ['light', 'dark']) {
    // No modo escuro usam-se fontes largas (DejaVu, como num runner Ubuntu): o resultado não pode depender das fontes locais.
    const {ctx, page, errors} = await open(browser, srv, {device: {viewport: {width, height: 800}, isMobile: true, hasTouch: true}, scheme,
      config: {protocol: '/__raw/prospective_protocol.json'}, wideFonts: scheme === 'dark'});
    for (const v of VIEWS) {
      await page.evaluate(h => { location.hash = h; }, v);
      await page.waitForSelector('#view-' + v + ' h2');
      if (v === 'brain') for (const t of ['diario', 'llm', 'promissoras', 'replicacao', 'rejeitadas', 'seguimento', 'limites']) {
        await page.click('[data-tab="' + t + '"]');
        assert.ok(await fits(page, width), 'overflow em brain/' + t + ' a ' + width + ' px ' + scheme);
      }
      assert.ok(await fits(page, width), 'overflow em ' + v + ' a ' + width + ' px ' + scheme + ': ' + await page.evaluate(() => document.documentElement.scrollWidth) + ' px');
      // nenhum elemento visível ultrapassa a largura do ecrã fora de uma região com scroll próprio
      const wide = await page.evaluate(w => [...document.querySelectorAll('body *')].filter(el => {
        if (el.closest('thead, caption, .sr, .skip, [hidden]')) return false; // só para leitores de ecrã (recortados a 1 px)
        const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > w + 1 || r.left < -1);
      }).slice(0, 3).map(el => el.tagName + '.' + el.className + ' «' + (el.textContent || '').trim().slice(0, 30) + '»'), width);
      assert.deepEqual(wide, [], 'elementos fora do ecrã em ' + v + ' a ' + width + ' px');
      // nenhuma região com scroll horizontal (tabelas empilham-se, ticker e separadores quebram linha)
      const scrollers = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => {
        const cs = getComputedStyle(el); if (el.closest('[hidden]') || cs.display === 'none') return false;
        return (cs.overflowX === 'auto' || cs.overflowX === 'scroll') && el.scrollWidth > el.clientWidth + 1;
      }).slice(0, 3).map(el => el.tagName + '.' + el.className));
      assert.deepEqual(scrollers, [], 'scroll horizontal interno em ' + v + ' a ' + width + ' px');
    }
    await page.evaluate(() => { location.hash = 'performance'; });
    await page.waitForSelector('#view-performance .chart[data-chart]');
    const chart = page.locator('#view-performance .chart[data-chart]').first();
    await chart.focus();
    for (const key of ['End', 'ArrowLeft', 'ArrowRight']) await page.keyboard.press(key === 'End' ? 'ArrowRight' : key);
    assert.ok(await chart.locator('.tooltip').isVisible());
    assert.ok(await fits(page, width), 'tooltip cria scroll horizontal a ' + width + ' px');
    await page.evaluate(() => { location.hash = 'radar'; });
    await page.waitForSelector('#radar-filters');
    // nenhum texto dos cartões espremido nem sobreposto: o título ocupa a largura útil do cartão
    const squeezed = await page.evaluate(() => [...document.querySelectorAll('.game > button, .pick')].filter(b => {
      const t = b.querySelector('.title'), col = t && (t.closest('.pick') ? t : t.parentElement);
      return col && col.getBoundingClientRect().width < b.getBoundingClientRect().width * 0.6;
    }).length);
    assert.equal(squeezed, 0, 'cartões do radar com o título espremido a ' + width + ' px');
    if (await page.locator('[data-game]').count()) {
      await page.locator('[data-game]').first().click();
      await page.waitForSelector('#drawer:not([hidden])');
      assert.ok(await page.evaluate(() => document.getElementById('drawer').scrollWidth <= document.getElementById('drawer').clientWidth + 1), 'gaveta com scroll horizontal a ' + width + ' px');
    }
    assert.deepEqual(errors, []);
    await ctx.close();
  }
}

async function functional(browser, srv) {
  // Recurso: sem manifesto, relatório completo derivado no navegador.
  let {ctx, page, errors} = await open(browser, srv);
  assert.equal(await mode(page), 'relatório');
  assert.match(await page.locator('#ticker').innerText(), /Acompanhadas\s+[\d\s.]+/i);
  assert.match(await page.locator('#view-command').innerText(), /RENTABILIDADE NÃO DEMONSTRADA/i);
  // Radar: filtros e ficha com evidência a favor e contra.
  await page.evaluate(() => { location.hash = 'radar'; });
  await page.waitForSelector('#radar-filters');
  const total = await page.locator('[data-game]').count();
  assert.ok(total > 0, 'radar sem jogos');
  await page.fill('#radar-filters input[name="q"]', 'zzzz-sem-correspondência');
  assert.equal(await page.locator('[data-game]').count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.name), 'q', 'o filtro deve manter o foco');
  await page.fill('#radar-filters input[name="q"]', '');
  assert.doesNotMatch(await page.locator('#view-radar').innerText(), /contraditório/i, 'rótulo do relatório sem explicação');
  await page.locator('[data-game]').first().click();
  await page.waitForSelector('#drawer:not([hidden])');
  assert.match(await page.locator('#drawer').innerText(), /A favor[\s\S]*Contra/i);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#drawer').isHidden(), true);
  // Market: seleção de jogo pelo teclado e gráfico com tooltip pelo teclado.
  await page.evaluate(() => { location.hash = 'market'; });
  await page.waitForSelector('[data-market]');
  const second = page.locator('[data-market]').nth(1);
  const id = await second.getAttribute('data-market');
  await second.focus(); await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => window.__V6.state.ui.market), id);
  const chart = page.locator('#view-market .chart[data-chart]').first();
  if (await chart.count()) { await chart.focus(); await page.keyboard.press('ArrowLeft'); assert.equal(await chart.locator('.tooltip').isVisible(), true); }
  // Tema persistente.
  await page.click('#theme');
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
  await page.reload(); await page.waitForFunction(() => window.__V6 && window.__V6.state.mode);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
  assert.deepEqual(errors, []);
  // Revalidação: relatório inalterado custa 304, não um novo download.
  srv.reset();
  await page.evaluate(() => window.__V6.refresh());
  const rep = srv.state.log.filter(l => l.path === '/report.json');
  assert.deepEqual(rep.map(l => l.status), [206], 'relatório inalterado: só o pedido parcial da revisão');
  assert.ok(rep[0].bytes <= 512);
  await ctx.close();

  // Sem snapshots publicados: o manifesto não é pedido em cada ciclo.
  ({ctx, page, errors} = await open(browser, srv));
  srv.reset();
  await page.evaluate(() => window.__V6.refresh()); await page.evaluate(() => window.__V6.refresh());
  assert.equal(srv.state.log.filter(l => l.path === '/v6/snapshots/dashboard-manifest.json').length, 0, 'manifesto pedido apesar do 404 recente');
  await page.evaluate(() => { location.hash = 'knowledge'; });
  await page.waitForSelector('#view-knowledge h2');
  assert.doesNotMatch(await page.locator('#view-knowledge').innerText(), /obsidian|cofre|brain/i, 'o Knowledge Center não revela fontes privadas');
  // Foco mantido numa nova revisão dos dados (radar).
  await page.evaluate(() => { location.hash = 'radar'; });
  await page.waitForSelector('#radar-filters');
  await page.focus('#radar-filters input[name="q"]');
  srv.set('/report.json', {body: variant(5, r => { r.generated_at_utc = new Date(NOW - 30000).toISOString(); })});
  await page.evaluate(() => window.__V6.refresh());
  assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.name), 'q', 'foco perdido numa nova revisão');
  srv.clear('/report.json');
  await ctx.close();
  ({ctx, page, errors} = await open(browser, srv));
  // Falhas HTTP: no arranque mostra erro; depois de recuperar, uma nova falha mantém a última revisão válida.
  srv.set('/report.json', {status: 500});
  ({ctx, page, errors} = await open(browser, srv, {wait: false}));
  await page.waitForFunction(() => !document.getElementById('banner-error').hidden);
  assert.match(await page.locator('#banner-error').innerText(), /Não foi possível carregar/i);
  srv.clear('/report.json');
  await page.click('#refresh');
  await page.waitForFunction(() => window.__V6.state.mode === 'relatório' && !window.__V6.state.busy);
  assert.equal(await page.locator('#banner-error').isHidden(), true);
  srv.set('/report.json', {status: 503});
  await page.evaluate(() => window.__V6.refresh());
  assert.match(await page.locator('#banner-error').innerText(), /mantém-se a última revisão válida/i);
  assert.match(await page.locator('#view-command').innerText(), /Apostas acompanhadas/i);
  srv.set('/report.json', {body: '{"truncado":'});
  await page.evaluate(() => window.__V6.refresh());
  assert.match(await page.locator('#banner-error').innerText(), /ilegível/i);
  srv.clear('/report.json');
  assert.deepEqual(errors, []);
  await ctx.close();

  // Revisão mais antiga servida depois de uma mais recente: recusada.
  const newer = variant(5, r => { r.generated_at_utc = new Date(NOW - 60000).toISOString(); });
  const older = variant(5, r => { r.generated_at_utc = new Date(NOW - 3600000).toISOString(); r.unique_bets = r.unique_bets; });
  srv.set('/report.json', {body: newer});
  ({ctx, page, errors} = await open(browser, srv));
  const rev = await page.evaluate(() => window.__V6.state.files.summary.data.generated_at_utc);
  srv.set('/report.json', {body: older});
  await page.evaluate(() => window.__V6.refresh());
  assert.equal(await page.evaluate(() => window.__V6.state.files.summary.data.generated_at_utc), rev, 'revisão antiga aplicada');
  await ctx.close();

  // Dados desatualizados: recolha há 4 h.
  srv.set('/report.json', {body: variant(240)});
  ({ctx, page, errors} = await open(browser, srv));
  assert.match(await page.locator('#banner-fresh').innerText(), /Recolha parada/i);
  await ctx.close();

  // Relatório incoerente (soma dos grupos) é recusado e não é mostrado.
  srv.set('/report.json', {body: variant(5, r => { Object.values(r.groups)[0].bets += 7; })});
  ({ctx, page, errors} = await open(browser, srv, {wait: false}));
  await page.waitForFunction(() => !document.getElementById('banner-error').hidden);
  assert.match(await page.locator('#banner-error').innerText(), /recusado/i);
  await ctx.close();

  // Dados incompletos: secções opcionais em falta.
  srv.set('/report.json', {body: variant(5, r => { delete r.matches; delete r.simulation; delete r.brain.agenda; delete r.brain.llm; delete r.insights.luck; })});
  ({ctx, page, errors} = await open(browser, srv));
  for (const v of VIEWS) { await page.evaluate(h => { location.hash = h; }, v); await page.waitForSelector('#view-' + v + ' h2'); }
  assert.deepEqual(errors, []);
  await ctx.close();

  // Certificações inválidas e conflitos no relatório prospetivo; duplicados na agenda; dados privados.
  const badPros = structuredClone(prospective);
  badPros.H1.status = 'supported_predictive_only'; badPros.games_with_conflicting_results = 3;
  srv.set('/__raw/prospective_report.json', {body: JSON.stringify(badPros)});
  srv.set('/report.json', {body: variant(5, r => {
    r.brain.strategy_board.counts.validada = 2;
    const it = r.brain.agenda.items;
    if (it.length) it.push(structuredClone(it[0]), structuredClone(it[0]));
    r.insights.narrative = ['Contactar nuno@example.com ou @pessoa_privada', 'token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'];
    r.unparsed_samples = ['mensagem privada em bruto'];
  })});
  ({ctx, page, errors} = await open(browser, srv, {hash: 'lab'}));
  const lab = await page.locator('#view-lab').innerText();
  assert.match(lab, /certificação inválida/i);
  assert.doesNotMatch(lab, /suportada \(só preditiva\)/i);
  assert.match(lab, /Conflitos\s*\n?\s*3/i);
  assert.doesNotMatch(lab.replace(/Texto original do protocolo[\s\S]*$/i, ''), /predictive information|no claim before|earlier edge/i, 'texto do protocolo em inglês fora da secção original');
  await page.evaluate(() => { location.hash = 'command'; });
  await page.waitForSelector('#view-command h2');
  const cmd = await page.locator('#view-command').innerText();
  assert.match(cmd, /Estratégias validadas\s*\n?\s*0/i);
  assert.match(cmd, /H1 prospetiva[\s\S]{0,40}certificação inválida/i);
  assert.doesNotMatch(cmd, /suportada \(só preditiva\)/i);
  assert.match(cmd, /Certificações inválidas ignoradas/i);
  const html = await page.content();
  for (const secret of ['nuno@example.com', '@pessoa_privada', 'AAAAAAAAAAAAAAAA', 'mensagem privada em bruto']) assert.ok(!html.includes(secret), 'dado privado visível: ' + secret);
  if (base.brain.agenda.items.length) {
    await page.evaluate(() => { location.hash = 'radar'; });
    await page.waitForSelector('#radar-results');
    assert.match(await page.locator('#radar-results').innerText(), /2 duplicados/i);
  }
  assert.deepEqual(errors, []);
  await ctx.close();
  srv.clear();

  // Injeção de HTML pelo relatório prospetivo e pelo relatório principal: nada executa.
  const evil = structuredClone(prospective);
  evil.games_registered = '1" onmouseover="window.__pwned=1" x="';
  evil.H1.status = '<img src=x onerror="window.__pwned=1">';
  evil.H1.note = '<img src=x onerror="window.__pwned=1">';
  srv.set('/__raw/prospective_report.json', {body: JSON.stringify(evil)});
  srv.set('/report.json', {body: variant(5, r => {
    r.insights.narrative = ['<img src=x onerror="window.__pwned=1">'];
    if (r.brain.agenda.items.length) r.brain.agenda.items[0].match = '<script>window.__pwned=1</script>';
  })});
  for (const v of ['command', 'lab', 'radar']) {
    ({ctx, page, errors} = await open(browser, srv, {hash: v}));
    if (v === 'radar' && await page.locator('[data-game]').count()) await page.locator('[data-game]').first().click();
    await page.mouse.move(200, 300); await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => window.__pwned), undefined, 'HTML injetado executou em ' + v);
    assert.equal(await page.locator('#main img').count(), 0);
    await ctx.close();
  }
  srv.clear();

  // T8: certificação ativa e válida com estratégias validadas -> veredito publicado, nunca "nenhuma validada" ao lado.
  srv.set('/report.json', {body: variant(5, r => {
    Object.assign(r.brain.validation, {state: 'active', ledger_valid: true});
    Object.assign(r.brain.strategy_board, {certification_active: true, verdict: 'VEREDITO PUBLICADO DE TESTE', statistical_claim: 'none'});
    r.brain.strategy_board.counts.validada = 3;
  })});
  ({ctx, page, errors} = await open(browser, srv, {hash: 'command'}));
  const t8 = await page.locator('#view-command').innerText();
  assert.match(t8, /VEREDITO PUBLICADO DE TESTE/i);
  assert.doesNotMatch(t8, /NENHUMA ESTRATÉGIA VALIDADA/i);
  assert.match(t8, /Estratégias validadas\s*\n?\s*3/i);
  await ctx.close();
  srv.clear();

  // Relatório prospetivo indisponível: o Lab explica e o resto funciona.
  srv.set('/__raw/prospective_report.json', {status: 404});
  ({ctx, page, errors} = await open(browser, srv, {hash: 'lab'}));
  assert.match(await page.locator('#view-lab').innerText(), /indisponível/i);
  assert.deepEqual(errors, []);
  await ctx.close();
  srv.clear();
}

async function snapshots(browser, srvFactory) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v6snap-'));
  const opts = {report: REPORT_PATH, prospective: path.join(ROOT, 'data/prospective_report.json'), protocol: path.join(ROOT, 'scangoal/prospective_protocol.json'), out: dir};
  const manifest = build(opts);
  const files = {'/v6/snapshots/dashboard-manifest.json': path.join(dir, 'dashboard-manifest.json')};
  for (const k of core.SNAPSHOTS) files['/v6/snapshots/' + core.FILE(k)] = path.join(dir, core.FILE(k));
  const srv = await srvFactory({files});
  try {
    let {ctx, page, errors} = await open(browser, srv);
    assert.equal(await mode(page), 'snapshots');
    assert.deepEqual(srv.state.log.filter(l => l.path === '/report.json').map(l => l.status), [206], 'modo snapshots: só o pedido parcial da revisão');
    const first = srv.state.log.find(l => l.path.startsWith('/v6/snapshots/dashboard-') && !l.path.endsWith('manifest.json'));
    assert.equal(first.path, '/v6/snapshots/dashboard-summary.json', 'o resumo deve ser o primeiro snapshot');
    for (const v of VIEWS) { await page.evaluate(h => { location.hash = h; }, v); await page.waitForSelector('#view-' + v + ' h2'); }
    // Manifesto inalterado: nenhuma re-renderização (o foco e o texto escrito num filtro mantêm-se).
    await page.evaluate(() => { location.hash = 'radar'; });
    await page.waitForSelector('#radar-filters');
    await page.fill('#radar-filters input[name="q"]', 'a');
    const rev0 = await page.evaluate(() => window.__V6.state.revision);
    await page.evaluate(() => window.__V6.refresh());
    assert.equal(await page.evaluate(() => window.__V6.state.revision), rev0, 'revisão incrementada sem alterações');
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.name), 'q', 'foco perdido numa atualização sem alterações');
    await page.fill('#radar-filters input[name="q"]', '');
    // Revisão do relatório indisponível: os snapshots são usados, mas marcados como não verificados.
    srv.set('/report.json', {status: 500});
    await page.evaluate(() => window.__V6.refresh());
    assert.match(await page.evaluate(() => window.__V6.state.snapshotState), /não verificada/);
    srv.clear('/report.json');
    // Atualização seletiva: nova revisão em que só o resumo muda.
    const sumPath = path.join(dir, core.FILE('summary'));
    const sum = JSON.parse(fs.readFileSync(sumPath, 'utf8'));
    sum.data.generated_at_utc = new Date(NOW).toISOString();
    const body = JSON.stringify(sum) + '\n';
    const m2 = structuredClone(manifest);
    m2.report_generated_at_utc = new Date(NOW).toISOString();
    m2.files.summary = {path: core.FILE('summary'), sha256: require('node:crypto').createHash('sha256').update(body).digest('hex'), bytes: Buffer.byteLength(body)};
    srv.set('/v6/snapshots/dashboard-summary.json', {body});
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: JSON.stringify(m2)});
    srv.reset();
    await page.evaluate(() => window.__V6.refresh());
    const got = srv.state.log.filter(l => l.status === 200).map(l => l.path).sort();
    assert.deepEqual(got, ['/v6/snapshots/dashboard-manifest.json', '/v6/snapshots/dashboard-summary.json']);
    assert.equal(await page.evaluate(() => window.__V6.state.files.summary.data.generated_at_utc), new Date(NOW).toISOString());
    // Snapshot de outra revisão (hash diferente do manifesto): recusado, mantém a revisão anterior.
    const m3 = structuredClone(m2);
    m3.report_generated_at_utc = new Date(NOW + 60000).toISOString();
    m3.files.learning.sha256 = 'a'.repeat(64);
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: JSON.stringify(m3)});
    await page.evaluate(() => window.__V6.refresh());
    assert.match(await page.evaluate(() => window.__V6.state.snapshotState), /hash diferente/i);
    assert.equal(await page.evaluate(() => window.__V6.state.manifest.report_generated_at_utc), m2.report_generated_at_utc);
    assert.deepEqual(errors, []);
    await ctx.close();

    // Snapshot com esquema incompatível no arranque: recorre ao relatório completo.
    srv.clear(); srv.reset();
    const bad = JSON.parse(fs.readFileSync(path.join(dir, core.FILE('signals')), 'utf8'));
    bad.schema = 'goal_roi_dashboard_v5';
    srv.set('/v6/snapshots/dashboard-signals.json', {body: JSON.stringify(bad)});
    ({ctx, page, errors} = await open(browser, srv));
    assert.equal(await mode(page), 'relatório');
    assert.deepEqual(errors, []);
    await ctx.close();

    // Manifesto ilegível: recurso ao relatório completo.
    srv.clear();
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: '<html>'});
    ({ctx, page, errors} = await open(browser, srv));
    assert.equal(await mode(page), 'relatório');
    await ctx.close();

    // P1-3: snapshots obsoletos face ao relatório -> recurso automático; recuperação sem recarregar quando alcançam.
    srv.clear(); srv.reset();
    const newerRev = new Date(Date.parse(manifest.report_generated_at_utc) + 20 * 60000).toISOString();
    const newerReport = variant(5, r => { r.generated_at_utc = newerRev; });
    srv.set('/report.json', {body: newerReport});
    ({ctx, page, errors} = await open(browser, srv));
    assert.equal(await mode(page), 'relatório', 'snapshots 20 min atrás do relatório devem ser ignorados');
    assert.equal(await page.evaluate(() => window.__V6.state.appliedRevision), newerRev);
    assert.match(await page.evaluate(() => window.__V6.state.snapshotState), /desatualizados/);
    // os snapshots alcançam o relatório: volta aos snapshots no ciclo seguinte, sem recarregar
    const caughtOut = fs.mkdtempSync(path.join(os.tmpdir(), 'v6caught-'));
    const caught = build(Object.assign({}, opts, {report: writeTmp(newerReport), out: caughtOut}));
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: JSON.stringify(caught)});
    for (const k of core.SNAPSHOTS) srv.set('/v6/snapshots/' + core.FILE(k), {body: fs.readFileSync(path.join(caughtOut, core.FILE(k)))});
    const marker = await page.evaluate(() => { window.__marker = 1; return 1; });
    await page.evaluate(() => window.__V6.refresh());
    assert.equal(await mode(page), 'snapshots');
    assert.equal(await page.evaluate(() => window.__marker), marker, 'sem recarregar a página');
    assert.equal(await page.evaluate(() => window.__V6.state.appliedRevision), newerRev);
    // desaparecimento dos snapshots -> relatório; corrupção -> relatório; sem mistura de revisões
    srv.set('/v6/snapshots/dashboard-manifest.json', {status: 404});
    await page.evaluate(() => window.__V6.refresh());
    assert.equal(await mode(page), 'relatório');
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: JSON.stringify(caught)});
    await page.evaluate(() => window.__V6.refresh());
    assert.equal(await mode(page), 'snapshots');
    srv.set('/v6/snapshots/dashboard-learning.json', {body: '{"corrompido":'});
    const m4 = structuredClone(caught); m4.report_generated_at_utc = new Date(Date.parse(newerRev) + 60000).toISOString();
    m4.files.summary.sha256 = 'b'.repeat(64);
    srv.set('/v6/snapshots/dashboard-manifest.json', {body: JSON.stringify(m4)});
    srv.set('/report.json', {body: variant(5, r => { r.generated_at_utc = m4.report_generated_at_utc; })});
    await page.evaluate(() => window.__V6.refresh());
    assert.equal(await mode(page), 'relatório', 'snapshots corrompidos devem levar ao relatório');
    assert.equal(await page.evaluate(() => window.__V6.state.appliedRevision), m4.report_generated_at_utc);
    const revs = await page.evaluate(() => { const f = window.__V6.state.files; return [f.summary.data.generated_at_utc]; });
    assert.deepEqual(revs, [m4.report_generated_at_utc]);
    assert.deepEqual(errors, []);
    await ctx.close();
  } finally { await srv.close(); }
}

function writeTmp(text) { const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'v6rep-')), 'report.json'); fs.writeFileSync(f, text); return f; }


(async () => {
  const browser = await chromium.launch(process.env.V6_CHROMIUM ? {executablePath: process.env.V6_CHROMIUM} : {});
  const failures = [];
  const run = async (name, fn) => { try { await fn(); console.log('ok -', name); } catch (e) { failures.push(e); console.log('not ok -', name, '\n', e && e.stack); } };
  const srv = await start();
  try {
    await run('funcional e adversarial (modo relatório)', () => functional(browser, srv));
    srv.clear();
    await run('visual: telemóvel, tablet e desktop em claro e escuro', () => visual(browser, srv));
    await run('Android 320–430 px sem overflow (claro e escuro)', () => narrow(browser, srv));
    await run('snapshots: progressivo, seletivo, incompatível', () => snapshots(browser, start));
  } finally { await srv.close(); await browser.close(); }
  if (failures.length) { console.error(failures.length + ' falha(s)'); process.exit(1); }
})();

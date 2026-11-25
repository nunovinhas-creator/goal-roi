/* Goal-ROI Intelligence V6 — interface. Depende apenas de v6-core.js. */
(function () {
  'use strict';
  const C = window.V6Core;
  const CFG = Object.assign({
    manifest: 'snapshots/dashboard-manifest.json',
    report: '../report.json',
    prospective: 'https://raw.githubusercontent.com/nunovinhas-creator/goal-roi/main/data/prospective_report.json',
    protocol: 'https://raw.githubusercontent.com/nunovinhas-creator/goal-roi/main/scangoal/prospective_protocol.json',
    refreshMs: 3 * 60000, tickMs: 15000, timeoutMs: 45000,
    knowledge: false // Integração com o Goal-ROI Brain desativada: não existe canal público seguro (ver Knowledge Center).
  }, window.V6_CONFIG || {});

  const $ = id => document.getElementById(id);
  const VIEWS = ['command', 'market', 'radar', 'brain', 'lab', 'performance', 'operations', 'knowledge'];
  const NEEDS = {command: ['summary'], market: ['signals'], radar: ['signals'], brain: ['learning'], lab: ['science'],
    performance: ['performance'], operations: ['operations', 'summary'], knowledge: []};

  const S = {
    mode: null, files: {}, revision: 0, manifest: null, reportText: null, prospectiveText: null, prospective: null,
    prospectiveState: 'por carregar', protocol: null, lastOk: null, lastError: null, busy: false,
    seq: C.createSequencer(), stats: {requests: 0, notModified: 0, failures: 0, bytes: 0, derivations: 0, snapshotFetches: 0},
    view: 'command', rendered: {}, charts: {}, ui: {radar: {q: '', source: '', phase: '', verdict: '', conflict: false, min: '', max: ''}, market: null}
  };

  // ---------- formatação (pt-PT) ----------
  const NF = new Intl.NumberFormat('pt-PT');
  const esc = v => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const n = (v, d = 0) => isNum(v) ? v.toLocaleString('pt-PT', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
  const int = v => isNum(v) ? NF.format(v) : '—';
  const pct = (v, d = 1) => isNum(v) ? (v * 100).toLocaleString('pt-PT', {minimumFractionDigits: d, maximumFractionDigits: d}) + ' %' : '—';
  const spct = (v, d = 1) => isNum(v) ? (v > 0 ? '+' : '') + pct(v, d) : '—';
  const units = (v, d = 2) => isNum(v) ? (v > 0 ? '+' : '') + n(v, d) : '—';
  const sign = v => isNum(v) ? (v > 0 ? 'pos' : v < 0 ? 'neg' : '') : '';
  const ci = c => Array.isArray(c) && c.length === 2 ? '[' + spct(c[0]) + '; ' + spct(c[1]) + ']' : '—';
  const LIS = new Intl.DateTimeFormat('pt-PT', {timeZone: 'Europe/Lisbon', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
  const LISD = new Intl.DateTimeFormat('pt-PT', {timeZone: 'Europe/Lisbon', day: '2-digit', month: 'short'});
  const when = iso => { const t = Date.parse(iso); return Number.isFinite(t) ? LIS.format(new Date(t)) : '—'; };
  const day = iso => { const t = Date.parse(iso); return Number.isFinite(t) ? LISD.format(new Date(t)) : '—'; };
  const ago = m => !isNum(m) ? '—' : m < 60 ? m + ' min' : m < 1440 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : Math.floor(m / 1440) + ' d';
  const src = s => '<span class="src ' + esc(s) + '"><i aria-hidden="true"></i>' + esc(s === 'Outros' ? 'Outros sinais do bot' : s) + '</span>';
  // Notas do relatório publicadas em inglês, traduzidas sem alterar o sentido; texto desconhecido fica como está.
  const NOTES = {'predictive information relative to a generic rate; not profitability': 'Informação preditiva face a uma taxa genérica; não é rentabilidade.',
    'earlier edge did not replicate; no ROI validation without executable prices and known costs': 'A vantagem anterior não se replicou; sem validação de ROI sem preços executáveis e custos conhecidos.',
    'the prospective cohort has no independent evidence of temporal order': 'a coorte prospetiva não tem evidência independente da ordem temporal'};
  const note = t => esc(NOTES[t] || t || '');
  const tag = (text, kind) => '<span class="tag ' + (kind || '') + '">' + esc(text) + '</span>';
  const H1S = {insufficient_games: ['amostra insuficiente', 'warn'], supported_predictive_only: ['suportada (só preditiva)', 'good'], not_supported: ['não suportada', 'bad']};
  const H2S = {insufficient_games: ['amostra insuficiente', 'warn'], descriptive_only: ['apenas descritiva', 'info']};
  const VERD = {'favorável': 'good', 'misto': 'warn', 'contraditório': 'bad', 'evitar': 'bad', 'sem evidência': ''};
  const ECON = {positive_exploratory: ['positiva (exploratória)', 'info'], nonpositive_exploratory: ['não positiva (exploratória)', ''], insufficient: ['insuficiente', 'warn']};
  const econ = s => s ? tag((ECON[s] || [s])[0], (ECON[s] || [0, ''])[1]) : '—';
  const STATUS = {'promissora': 'info', 'em observação': '', 'abandonada': 'bad', 'forte': 'good', 'confirmada': 'good'};
  const panel = (title, body, opts = {}) => '<section class="panel ' + (opts.span || 'span-12') + '"' + (opts.id ? ' id="' + opts.id + '"' : '') + '><header><h3>' + esc(title) + '</h3>' +
    (opts.meta ? '<span class="meta">' + opts.meta + '</span>' : '') + '</header><div class="body' + (opts.flush ? ' flush' : '') + '">' + body + '</div></section>';
  const table = (head, rows, caption) => '<div class="table-wrap" role="region" tabindex="0" aria-label="' + esc(caption || 'Tabela') + '"><table>' +
    (caption ? '<caption class="sr">' + esc(caption) + '</caption>' : '') + '<thead><tr>' +
    head.map(h => '<th scope="col"' + (h.n ? ' class="n"' : '') + '>' + esc(h.t || h) + '</th>').join('') + '</tr></thead><tbody>' +
    (rows.length ? rows.join('') : '<tr><td colspan="' + head.length + '" class="empty">Sem dados publicados.</td></tr>') + '</tbody></table></div>';
  const td = (v, cls) => '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + v + '</td>';
  const progress = (value, target) => { const p = isNum(value) && target ? Math.min(1, value / target) : 0;
    return '<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="' + target + '" aria-valuenow="' + (value || 0) + '"><i style="width:' + (p * 100).toFixed(1) + '%"></i></div>'; };
  const head = (title, lede, extra) => '<div class="view-head"><div><h2 tabindex="-1">' + esc(title) + '</h2>' + (lede ? '<p>' + lede + '</p>' : '') + '</div>' + (extra || '') + '</div>';
  const kpis = items => '<dl class="kpis" style="--n:' + items.length + '">' + items.map(k => '<div class="kpi"><dt>' + esc(k[0]) + '</dt><dd><b class="' + (k[3] || '') + '">' + k[1] + '</b>' + (k[2] ? '<small>' + k[2] + '</small>' : '') + '</dd></div>').join('') + '</dl>';
  const verdictBox = () => '<span class="verdict">' + esc(C.VERDICT) + '</span>';

  // ---------- rede ----------
  async function get(url) {
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctl && setTimeout(() => ctl.abort(), CFG.timeoutMs);
    S.stats.requests++;
    try {
      // no-cache: revalida com ETag/Last-Modified. Um relatório inalterado custa uma resposta 304 em vez de 3 MB.
      const res = await fetch(url, {cache: 'no-cache', signal: ctl ? ctl.signal : undefined, credentials: 'omit', referrerPolicy: 'no-referrer'});
      if (!res.ok) { const e = new Error('HTTP ' + res.status + ' em ' + url.split('/').pop()); e.status = res.status; throw e; }
      const text = await res.text();
      S.stats.bytes += text.length;
      return text;
    } finally { if (timer) clearTimeout(timer); }
  }
  async function digest(text) {
    try {
      if (!window.crypto || !crypto.subtle || !window.isSecureContext) return null;
      const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
      return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
    } catch (e) { return null; }
  }
  const byteLength = text => new TextEncoder().encode(text).length;

  // Modo 1: snapshots derivados com manifesto (carregamento progressivo e atualização seletiva).
  async function refreshSnapshots(ticket) {
    let text;
    try { text = await get(CFG.manifest); }
    catch (e) { if (S.mode === 'snapshots') throw e; return false; }
    let manifest;
    try { manifest = JSON.parse(text); } catch (e) { if (S.mode === 'snapshots') throw new Error('manifesto ilegível'); return false; }
    const mv = C.validateManifest(manifest);
    if (!mv.ok) { if (S.mode === 'snapshots') throw new Error(mv.problems.join('; ')); return false; }
    if (S.mode === 'snapshots' && S.manifest && Date.parse(manifest.report_generated_at_utc) < Date.parse(S.manifest.report_generated_at_utc)) return true;
    const changed = C.SNAPSHOTS.filter(k => !S.manifest || S.mode !== 'snapshots' || S.manifest.files[k].sha256 !== manifest.files[k].sha256);
    if (!changed.length) { S.seq.accept(ticket, manifest.report_generated_at_utc); S.manifest = manifest; return true; }
    const load = async name => {
      const body = await get(CFG.manifest.replace(/[^/]*$/, '') + manifest.files[name].path);
      S.stats.snapshotFetches++;
      const sha = await digest(body);
      let snap;
      try { snap = JSON.parse(body); } catch (e) { throw new Error(name + ': JSON inválido'); }
      const chk = C.checkSnapshot(name, snap, manifest, sha ? {sha256: sha} : {bytes: byteLength(body)});
      if (!chk.ok) throw new Error(chk.problems.join('; '));
      return [name, snap];
    };
    const first = S.mode !== 'snapshots' && changed.includes('summary');
    const fresh = {};
    if (first) {
      // Primeira pintura com o resumo (alguns KB) antes dos restantes ficheiros.
      const [, summary] = await load('summary');
      if (!S.files.summary) { S.files = {summary}; S.mode = 'snapshots-parcial'; paintChrome(); renderView(true); }
      fresh.summary = summary;
    }
    const rest = await Promise.all(changed.filter(k => !(first && k === 'summary')).map(load));
    for (const [k, v] of rest) fresh[k] = v;
    if (!S.seq.accept(ticket, manifest.report_generated_at_utc)) return true; // resposta fora de ordem: ignorada
    // Atómico: só agora os ficheiros novos substituem os anteriores, todos da mesma revisão do manifesto.
    S.files = Object.assign({}, S.mode === 'snapshots' ? S.files : {}, fresh);
    S.manifest = manifest; S.mode = 'snapshots'; S.revision++;
    return true;
  }

  // Modo 2 (recurso): relatório completo, derivado no navegador com o mesmo núcleo.
  async function refreshReport(ticket) {
    const [text, pros] = await Promise.all([get(CFG.report), loadProspective()]);
    if (text === S.reportText && !pros.changed) { S.seq.accept(ticket, S.files.summary && S.files.summary.data.generated_at_utc); S.stats.notModified++; return; }
    let report;
    try { report = JSON.parse(text); } catch (e) { throw new Error('relatório ilegível'); }
    const v = C.validateReport(report);
    if (!v.ok) throw new Error('relatório recusado: ' + v.problems.slice(0, 3).join('; '));
    if (!S.seq.accept(ticket, report.generated_at_utc)) return; // mais antigo do que o já aplicado
    S.files = C.buildAll(report, S.prospective, S.protocol, Date.now());
    S.stats.derivations++;
    S.reportText = text; S.mode = 'relatório'; S.revision++;
  }
  async function loadProspective() {
    const out = {changed: false};
    try {
      const [text, proto] = await Promise.all([get(CFG.prospective), S.protocol ? Promise.resolve(null) : get(CFG.protocol).catch(() => null)]);
      if (proto) { try { const p = JSON.parse(proto); if (p && p.schema === 'goal_roi_prospective_protocol_v1') S.protocol = p; } catch (e) {} }
      if (text !== S.prospectiveText) {
        const p = JSON.parse(text);
        if (!p || p.schema !== C.PROSPECTIVE_SCHEMA) throw new Error('esquema prospetivo desconhecido');
        S.prospective = p; S.prospectiveText = text; out.changed = true;
      }
      S.prospectiveState = 'atual';
    } catch (e) {
      S.prospectiveState = S.prospective ? 'última versão válida (falha: ' + e.message + ')' : 'indisponível (' + e.message + ')';
    }
    return out;
  }

  async function refresh() {
    if (S.busy) return;
    S.busy = true;
    const ticket = S.seq.begin();
    try {
      let used = false;
      try { used = await refreshSnapshots(ticket); }
      catch (e) {
        // Snapshots incompatíveis ou incompletos: se ainda não havia uma revisão de snapshots válida, usa-se o relatório
        // completo; se havia, mantém-se essa revisão e mostra-se o erro.
        if (S.mode === 'snapshots') throw e;
        S.files = {}; S.mode = null; S.snapshotError = e.message;
      }
      if (!used) await refreshReport(ticket);
      S.lastOk = Date.now(); S.lastError = null;
    } catch (e) {
      S.stats.failures++;
      S.lastError = e.message || String(e);
    } finally {
      S.busy = false;
      $('main').setAttribute('aria-busy', 'false');
      paintChrome();
      renderView(false);
    }
  }

  // ---------- moldura ----------
  function paintChrome() {
    const sum = S.files.summary && S.files.summary.data;
    const err = $('banner-error');
    if (S.lastError) {
      err.hidden = false;
      err.textContent = (sum ? 'Falha na atualização; mantém-se a última revisão válida. ' : 'Não foi possível carregar os dados. ') + S.lastError;
    } else err.hidden = true;
    $('loader-mode').textContent = 'Fonte: ' + (S.mode === 'snapshots' ? 'snapshots derivados' : S.mode === 'snapshots-parcial' ? 'snapshots (a completar)' : S.mode === 'relatório' ? 'report.json completo' : '—');
    if (!sum) return;
    const fr = C.freshness(sum.collected_at_utc, Date.now());
    const fb = $('banner-fresh');
    fb.hidden = fr.state === 'normal';
    fb.className = 'banner ' + (fr.state === 'parada' ? 'bad' : 'warn');
    fb.textContent = fr.state === 'parada' ? 'Recolha parada há ' + ago(fr.minutes) + '. Os sinais e as odds podem já não existir.'
      : fr.state === 'atrasada' ? 'Recolha atrasada: última há ' + ago(fr.minutes) + '. Confirme as odds antes de qualquer leitura.' : '';
    const ref = sum.reference || {}, roi = ref.roi && ref.roi['odd-0%'];
    const p = sum.prospective;
    $('ticker').innerHTML = [
      ['Recolha', ago(fr.minutes) + ' · ' + fr.state],
      ['Revisão', when(sum.generated_at_utc)],
      ['Acompanhadas', int(sum.tracked)],
      ['Liquidadas', int(sum.settled)],
      ['ROI Q7', roi ? spct(roi.roi) : '—'],
      ['Saldo', units(ref.units) + ' u'],
      ['H1', p && p.h1 ? int(p.h1.games) + '/' + p.h1.target : '—'],
      ['H2', p && p.h2 ? int(p.h2.games) + '/' + p.h2.target : '—'],
      ['Validadas', int(sum.certification.validated)]
    ].map(([k, v]) => '<span class="tick">' + esc(k) + ' <b>' + esc(v) + '</b></span>').join('');
  }

  // ---------- gráficos ----------
  let chartSeq = 0;
  function lineChart(series, opts = {}) {
    // series: [{name, cls, points:[{x:Number, y:Number, label:String}]}]; um único eixo y.
    const W = opts.w || 720, Hh = opts.h || 220, L = 46, R = 10, T = 10, B = 24;
    const all = series.flatMap(s => s.points).filter(p => isNum(p.x) && isNum(p.y));
    if (all.length < 2) return '<div class="empty">Dados insuficientes para o gráfico.</div>';
    let x0 = Math.min(...all.map(p => p.x)), x1 = Math.max(...all.map(p => p.x));
    let y0 = Math.min(...all.map(p => p.y), opts.zero ? 0 : Infinity), y1 = Math.max(...all.map(p => p.y), opts.zero ? 0 : -Infinity);
    if (x1 === x0) x1 = x0 + 1;
    if (y1 === y0) { y1 += 1; y0 -= 1; }
    const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
    const X = x => L + (x - x0) / (x1 - x0) * (W - L - R), Y = y => T + (1 - (y - y0) / (y1 - y0)) * (Hh - T - B);
    const ticks = 4, grid = [];
    for (let i = 0; i <= ticks; i++) {
      const v = y0 + (y1 - y0) * i / ticks;
      grid.push('<line class="gridline" x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(v).toFixed(1) + '" y2="' + Y(v).toFixed(1) + '"/><text class="axis" x="' + (L - 6) + '" y="' + (Y(v) + 3).toFixed(1) + '" text-anchor="end">' + esc((opts.fy || (a => n(a, 1)))(v)) + '</text>');
    }
    const xt = [x0, (x0 + x1) / 2, x1].map((v, i) => '<text class="axis" x="' + X(v).toFixed(1) + '" y="' + (Hh - 6) + '" text-anchor="' + ['start', 'middle', 'end'][i] + '">' + esc((opts.fx || (a => n(a)))(v)) + '</text>');
    const paths = series.map(s => {
      const pts = s.points.filter(p => isNum(p.x) && isNum(p.y));
      const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p.x).toFixed(1) + ' ' + Y(p.y).toFixed(1)).join('');
      return '<path class="line ' + (s.cls || '') + '" d="' + d + '"><title>' + esc(s.name) + '</title></path>' +
        (pts.length < 40 ? pts.map(p => '<circle class="dot line ' + (s.cls || '') + '" r="3" cx="' + X(p.x).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '"/>').join('') : '');
    }).join('');
    const zero = opts.zero && y0 < 0 && y1 > 0 ? '<line class="zero" x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(0).toFixed(1) + '" y2="' + Y(0).toFixed(1) + '"/>' : '';
    const id = 'c' + (++chartSeq);
    S.charts[id] = {points: all.slice().sort((a, b) => a.x - b.x), X, Y, W, Hh, T, B};
    const legend = series.length > 1 ? '<div class="legend">' + series.map(s => src(s.name)).join('') + '</div>' : '';
    return legend + '<div class="chart" data-chart="' + id + '" tabindex="0" role="img" aria-label="' + esc(opts.label || 'Gráfico') + '. Use as setas para percorrer os pontos.">' +
      '<svg viewBox="0 0 ' + W + ' ' + Hh + '" aria-hidden="true">' + grid.join('') + xt.join('') + zero + paths +
      '<line class="cross" x1="0" x2="0" y1="' + T + '" y2="' + (Hh - B) + '" visibility="hidden"/><rect class="hit" x="' + L + '" y="0" width="' + (W - L - R) + '" height="' + Hh + '"/></svg>' +
      '<div class="tooltip" hidden></div></div>';
  }
  function bindCharts(root) {
    for (const el of root.querySelectorAll('.chart[data-chart]')) {
      const c = S.charts[el.dataset.chart];
      if (!c) continue;
      const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'), tip = el.querySelector('.tooltip');
      let idx = -1;
      const show = i => {
        idx = Math.max(0, Math.min(c.points.length - 1, i));
        const p = c.points[idx], r = svg.getBoundingClientRect(), sx = r.width / c.W, sy = r.height / c.Hh;
        cross.setAttribute('x1', c.X(p.x)); cross.setAttribute('x2', c.X(p.x)); cross.setAttribute('visibility', 'visible');
        tip.hidden = false; tip.textContent = p.label;
        tip.style.left = Math.max(60, Math.min(r.width - 60, c.X(p.x) * sx)) + 'px'; tip.style.top = (c.Y(p.y) * sy) + 'px';
      };
      const hide = () => { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; };
      const near = clientX => {
        const r = svg.getBoundingClientRect(), x = (clientX - r.left) / r.width * c.W;
        let best = 0, bd = Infinity;
        c.points.forEach((p, i) => { const d = Math.abs(c.X(p.x) - x); if (d < bd) { bd = d; best = i; } });
        return best;
      };
      el.addEventListener('pointermove', e => show(near(e.clientX)));
      el.addEventListener('pointerleave', hide);
      el.addEventListener('blur', hide);
      el.addEventListener('keydown', e => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); show(idx < 0 ? c.points.length - 1 : idx + (e.key === 'ArrowRight' ? 1 : -1)); }
        if (e.key === 'Escape') hide();
      });
    }
  }
  function barsChart(rows, opts = {}) {
    // Barras verticais com rótulo de valor; positivo/negativo indicado também pelo sinal no texto.
    if (!rows.length) return '<div class="empty">Sem dados publicados.</div>';
    const W = opts.w || 720, Hh = opts.h || 190, L = 8, R = 8, T = 16, B = 26;
    const max = Math.max(...rows.map(r => Math.abs(r.v)), 1e-9), hasNeg = rows.some(r => r.v < 0);
    const zeroY = hasNeg ? T + (Hh - T - B) / 2 : Hh - B, scale = (hasNeg ? (Hh - T - B) / 2 : Hh - T - B) / max;
    const bw = (W - L - R) / rows.length, gap = Math.min(6, bw * 0.25);
    const bars = rows.map((r, i) => {
      const h = Math.abs(r.v) * scale, x = L + i * bw + gap / 2, y = r.v >= 0 ? zeroY - h : zeroY;
      return '<g><title>' + esc(r.label + ': ' + r.text) + '</title><rect class="' + (r.v >= 0 ? 'bar-pos' : 'bar-neg') + '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + Math.max(1, bw - gap).toFixed(1) + '" height="' + Math.max(1, h).toFixed(1) + '" rx="2"/>' +
        (rows.length <= 16 ? '<text class="axis" x="' + (x + (bw - gap) / 2).toFixed(1) + '" y="' + (r.v >= 0 ? y - 4 : y + h + 11).toFixed(1) + '" text-anchor="middle">' + esc(r.text) + '</text>' : '') +
        (rows.length <= 16 ? '<text class="axis" x="' + (x + (bw - gap) / 2).toFixed(1) + '" y="' + (Hh - 6) + '" text-anchor="middle">' + esc(r.label) + '</text>' : '') + '</g>';
    }).join('');
    return '<div class="chart" role="img" aria-label="' + esc(opts.label || 'Gráfico de barras') + '"><svg viewBox="0 0 ' + W + ' ' + Hh + '" aria-hidden="true"><line class="zero" x1="' + L + '" x2="' + (W - R) + '" y1="' + zeroY + '" y2="' + zeroY + '"/>' + bars + '</svg></div>';
  }
  function heatmap(rows) {
    const ORDER = ['Betfair', 'Polymarket', 'Stake', 'Outros'];
    const names = Object.keys(rows).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
    if (!names.length) return '<div class="empty">Sem dados publicados.</div>';
    const max = Math.max(1, ...names.flatMap(k => rows[k]));
    const lvl = v => v === 0 ? 0 : Math.min(5, 1 + Math.floor(v / max * 4.999));
    let html = '<div class="heat" role="table" aria-label="Sinais por hora de Lisboa e fonte"><span></span>' +
      Array.from({length: 24}, (_, h) => '<span class="hh">' + (h % 3 === 0 ? h : '') + '</span>').join('');
    for (const k of names) {
      html += '<span>' + src(k) + '</span>' + rows[k].map((v, h) => '<span class="cell l' + lvl(v) + '" title="' + esc(k + ', ' + h + 'h: ' + v + ' apostas') + '" aria-label="' + esc(k + ' às ' + h + ' h: ' + v) + '"></span>').join('');
    }
    return html + '</div><div class="legend"><span class="note">Mais claro = menos apostas; mais escuro = mais (máximo ' + int(max) + ' por hora e fonte).</span></div>';
  }

  // ---------- vistas ----------
  const missing = what => '<div class="panel span-12"><div class="body"><div class="skeleton" style="width:60%"></div><p class="note">A carregar ' + esc(what) + '…</p></div></div>';

  function viewCommand() {
    const s = S.files.summary && S.files.summary.data;
    if (!s) return head('Command Center') + missing('o resumo');
    const fr = C.freshness(s.collected_at_utc, Date.now());
    const r0 = s.reference.roi && s.reference.roi['odd-0%'], r2 = s.reference.roi && s.reference.roi['odd-2%'], r5 = s.reference.roi && s.reference.roi['odd-5%'];
    const p = s.prospective, cert = s.certification;
    const frKind = fr.state === 'normal' ? 'good' : fr.state === 'atrasada' ? 'warn' : 'bad';
    const ops = S.files.operations && S.files.operations.data;
    const issues = ops ? [
      ['Divergências de resultado (HL/BSD vs bot)', (ops.counters.hl_vs_bot_disagreements || 0) + (ops.counters.bsd_vs_bot_disagreements || 0) + (ops.counters.bot_mark_disagreements || 0)],
      ['Conflitos prospetivos', ops.prospective ? ops.prospective.conflicts : null],
      ['Sem resultado após 72 h', ops.prospective ? ops.prospective.missing_72h : null],
      ['Minutos de golos inconsistentes', ops.counters.goal_minutes_inconsistent],
      ['Mensagens por ler', ops.exclusions.unparsed]] : [];
    let html = head('Command Center', 'Estado operacional, qualidade dos dados e veredito científico desta revisão do relatório.', verdictBox());
    html += kpis([
      ['Recolha', ago(fr.minutes), tag(fr.state, frKind)],
      ['Apostas acompanhadas', int(s.tracked), int(s.messages) + ' mensagens lidas'],
      ['Apostas liquidadas', int(s.settled), int(s.reference.matches) + ' jogos'],
      ['ROI histórico Q7', r0 ? spct(r0.roi) : '—', 'IC95 ' + (r0 ? ci(r0.ci95) : '—'), r0 ? sign(r0.roi) : ''],
      ['Saldo histórico', units(s.reference.units) + ' u', 'à odd anunciada', sign(s.reference.units)],
      ['H1 prospetiva', p && p.h1 ? int(p.h1.games) + ' / ' + p.h1.target : '—', p && p.h1 ? (H1S[p.h1.status] || [p.h1.status])[0] : 'relatório indisponível'],
      ['H2 prospetiva', p && p.h2 ? int(p.h2.games) + ' / ' + p.h2.target : '—', p && p.h2 ? (H2S[p.h2.status] || [p.h2.status])[0] : 'relatório indisponível'],
      ['Estratégias validadas', int(cert.validated), cert.active ? 'certificação ativa' : 'certificação não ativa']
    ]);
    html += '<div class="grid">';
    html += panel('Veredito científico', '<div class="stack"><p style="margin:0"><b>' + esc(C.VERDICT) + '</b></p>' +
      '<dl class="facts"><dt>Certificação</dt><dd>' + esc(cert.state) + (cert.active ? '' : ' (não ativa)') + '</dd>' +
      '<dt>Preços executáveis</dt><dd>' + (cert.executable_price_evidence ? 'com evidência' : 'sem evidência') + '</dd>' +
      '<dt>Custos</dt><dd>' + (cert.costs_known ? 'conhecidos' : 'desconhecidos') + '</dd>' +
      '<dt>Relatório prospetivo</dt><dd>' + (p ? (p.valid ? 'válido' : 'com problemas') : esc(S.prospectiveState)) + '</dd></dl>' +
      (cert.invalid.length ? '<p class="note neg">Certificações inválidas ignoradas: ' + esc(cert.invalid.join('; ')) + '</p>' : '') +
      (p && p.problems.length ? '<p class="note neg">Problemas no relatório prospetivo: ' + esc(p.problems.join('; ')) + '</p>' : '') +
      '<p class="note">O ROI histórico usa odds anunciadas e resultados retrospetivos: é descritivo. Só o registo prospetivo, com critérios congelados, pode suportar hipóteses — e mesmo assim não demonstra rentabilidade.</p></div>', {span: 'span-5'});
    html += panel('Resultados independentes (registo prospetivo)', p ? '<div class="stack">' +
      '<div class="row"><span>Jogos registados</span><b class="num">' + int(p.games_registered) + '</b></div>' +
      '<div class="row"><span>Com resultado independente</span><b class="num">' + int(p.games_with_independent_result) + '</b></div>' +
      progress(p.games_with_independent_result, p.games_registered) +
      '<div class="row"><span>H1 · jogos com resultado</span><b class="num">' + int(p.h1 && p.h1.games) + ' / 200</b></div>' + progress(p.h1 && p.h1.games, 200) +
      '<div class="row"><span>H2 · jogos liquidados</span><b class="num">' + int(p.h2 && p.h2.games) + ' / 200</b></div>' + progress(p.h2 && p.h2.games, 200) +
      '<p class="note">Fonte: data/prospective_report.json (' + esc(S.prospectiveState) + '). Detalhe no <a href="#lab">Scientific Lab</a>.</p></div>'
      : '<div class="empty">Relatório prospetivo ' + esc(S.prospectiveState) + '.</div>', {span: 'span-7'});
    html += panel('Qualidade dos dados', ops ? '<div class="stack">' + issues.map(([k, v]) => '<div class="row"><span>' + esc(k) + '</span>' +
      (isNum(v) ? tag(int(v), v > 0 ? 'warn' : 'good') : '—') + '</div>').join('') + '<p class="note"><a href="#operations">Monitorização completa</a></p></div>' : missing('a qualidade dos dados'), {span: 'span-4'});
    html += panel('Descobertas recentes (exploratórias)', s.recent_discoveries.length ? '<ul class="list">' + s.recent_discoveries.map(d =>
      '<li><div>' + esc(d.id) + '</div><small>' + when(d.at_utc) + ' · ' + int(d.n) + ' resultados · acerto ' + pct(d.hit_rate) + ' vs necessário ' + pct(d.break_even) + ' · ' + esc(d.status || '') + '</small></li>').join('') + '</ul>'
      : '<div class="empty">Sem descobertas registadas.</div>', {span: 'span-8', flush: true, meta: 'nunca constituem validação'});
    html += panel('Leitura automática do relatório', s.narrative.length ? '<ul class="list">' + s.narrative.map(t => '<li>' + esc(t) + '</li>').join('') + '</ul>' : '<div class="empty">Sem leitura publicada.</div>',
      {flush: true, meta: 'texto gerado por regras em scangoal/insights.py'});
    html += panel('Agenda de sinais', '<div class="stack">' + Object.entries(s.agenda_counts).map(([k, v]) => '<div class="row"><span>' + tag(k, VERD[k]) + '</span><b class="num">' + int(v) + '</b></div>').join('') +
      '<p class="note">' + int(s.agenda_items) + ' sinais na agenda. <a href="#radar">Abrir o radar</a>.</p></div>', {span: 'span-6'});
    html += panel('Hipóteses do Brain', '<div class="stack">' + Object.entries(s.hypotheses_counts).map(([k, v]) => '<div class="row"><span>' + tag(k, STATUS[k]) + '</span><b class="num">' + int(v) + '</b></div>').join('') +
      '<p class="note">Estados exploratórios, sem correção de multiplicidade. <a href="#brain">Brain Intelligence</a>.</p></div>', {span: 'span-6'});
    return html + '</div>';
  }

  function viewMarket() {
    const sg = S.files.signals && S.files.signals.data;
    if (!sg) return head('Market Intelligence') + missing('os mercados');
    const cov = sg.coverage;
    const sel = S.ui.market && sg.markets.find(m => m.match_id === S.ui.market) || sg.markets[0];
    let html = head('Market Intelligence', 'Betfair, Polymarket e Stake: cronologia, volumes, odds observadas, reforços, convergências e contradições. Não se inferem identidades de apostadores.');
    html += '<div class="banner info" style="margin:0">Cobertura efetiva: cronologia de mercado publicada para ' + int(cov.games_with_market_history) + ' jogos de ' + int(cov.games_in_history) + ' no histórico (' + pct(cov.share) + '). As restantes vistas desta área usam o histórico completo.</div>';
    html += '<div class="grid">';
    html += panel('Mapa de calor · apostas por hora de Lisboa e fonte', '<div class="heat-wrap">' + heatmap(sg.heatmap) + '</div>', {flush: true, meta: 'histórico completo (' + int(Object.values(sg.heatmap).flat().reduce((a, b) => a + b, 0)) + ' apostas)'});
    const rows = sg.markets.map(m => '<tr class="click" tabindex="0" data-market="' + esc(m.match_id) + '"' + (sel && m.match_id === sel.match_id ? ' aria-selected="true"' : '') + '>' +
      td(esc(m.teams ? m.teams.join(' — ') : m.match_id)) + td(Object.keys(m.sources).map(src).join(' ')) + td(int(m.events), 'n') +
      td(day(m.first_utc) + ' → ' + when(m.last_utc)) +
      td((m.convergences.length ? tag('convergência', 'info') + ' ' : '') + (m.contradictions.length ? tag('contradição', 'bad') : '') || '—') + '</tr>');
    html += panel('Jogos com cronologia de mercado', table(['Jogo', 'Fontes', {t: 'Obs.', n: 1}, 'Período (Lisboa)', 'Sinais cruzados'], rows, 'Jogos com cronologia de mercado'), {span: 'span-6', flush: true, meta: int(sg.markets.length) + ' jogos'});
    if (sel) {
      const series = {};
      for (const e of sel.series) if (isNum(e.odds)) {
        const key = e.source;
        (series[key] = series[key] || []).push({x: Date.parse(e.d), y: e.odds, label: when(e.d) + ' · ' + e.source + ' · ' + e.selection + ' @ ' + n(e.odds, 2) + (isNum(e.total) ? ' · vol. ' + int(e.total) : '')});
      }
      const chart = lineChart(Object.entries(series).map(([k, pts]) => ({name: k, cls: 's-' + k, points: pts})), {fy: v => n(v, 2), fx: v => day(new Date(v).toISOString()), label: 'Odds observadas por fonte'});
      const selRows = sel.selections.map(x => '<tr>' + td(src(x.source)) + td(esc(x.market)) + td(esc(x.selection)) + td(n(x.first_odds, 2), 'n') + td(n(x.last_odds, 2), 'n') +
        td('<span class="' + sign(-(x.move_pct || 0)) + '">' + (isNum(x.move_pct) ? (x.move_pct > 0 ? '+' : '') + n(x.move_pct, 1) + ' %' : '—') + '</span>', 'n') +
        td(int(x.observations), 'n') + td(int(x.volume), 'n') + td(x.max_mult > 1 ? '×' + x.max_mult : '—', 'n') + '</tr>');
      html += panel('Odds observadas · ' + (sel.teams ? sel.teams.join(' — ') : sel.match_id), chart +
        (sel.contradictions.length ? '<p class="note neg">Contradição: ' + sel.contradictions.map(c => c.a + ' (' + c.sources_a.join(', ') + ') vs ' + c.b + ' (' + c.sources_b.join(', ') + ')').join('; ') + '</p>' : '') +
        (sel.convergences.length ? '<p class="note">Convergência: ' + sel.convergences.map(c => c.side + ' em ' + c.sources.join(' + ')).join('; ') + '</p>' : '') +
        table(['Fonte', 'Mercado', 'Seleção', {t: 'Odd inicial', n: 1}, {t: 'Odd final', n: 1}, {t: 'Variação', n: 1}, {t: 'Obs.', n: 1}, {t: 'Volume', n: 1}, {t: 'Reforço', n: 1}], selRows, 'Seleções observadas'),
        {span: 'span-6', meta: 'odds anunciadas, não executáveis'});
    }
    const pm = sg.price_move;
    if (pm) {
      const pmRows = Object.entries(pm).filter(([, v]) => v && isNum(v.n)).map(([k, v]) => '<tr>' + td(k === 'pre' ? 'Antes do jogo' : k === 'live' ? 'Durante o jogo' : esc(k)) + td(int(v.n), 'n') +
        td(int(v.shortened), 'n') + td(int(v.drifted), 'n') + td(int(v.unchanged), 'n') + td(isNum(v.median_move_pct) ? n(v.median_move_pct, 1) + ' %' : '—', 'n') +
        td(v.when_shortened ? pct(v.when_shortened.hit_rate) + ' (n=' + int(v.when_shortened.n) + ')' : '—', 'n') + td(v.when_drifted ? pct(v.when_drifted.hit_rate) + ' (n=' + int(v.when_drifted.n) + ')' : '—', 'n') + '</tr>');
      html += panel('Movimento da odd após reforço Betfair', table(['Fase', {t: 'n', n: 1}, {t: 'Desceu', n: 1}, {t: 'Subiu', n: 1}, {t: 'Igual', n: 1}, {t: 'Mediana', n: 1}, {t: 'Acerto se desceu', n: 1}, {t: 'Acerto se subiu', n: 1}], pmRows, 'Movimento da odd'),
        {span: 'span-6', flush: true, meta: 'insights.price_move · exploratório'});
    }
    const over = sg.polymarket_over25.map(x => '<tr>' + td(when(x.date_utc)) + td(esc(x.match)) + td(esc(x.selection)) + td(n(x.odds, 2), 'n') + td(isNum(x.share) ? n(x.share) + ' %' : '—', 'n') + td(int(x.total), 'n') + '</tr>');
    html += panel('Polymarket · Over 2,5', table(['Visto', 'Jogo', 'Seleção', {t: 'Odd', n: 1}, {t: 'Quota', n: 1}, {t: 'Volume', n: 1}], over, 'Polymarket Over 2,5'), {span: 'span-6', flush: true});
    return html + '</div>';
  }

  function radarFilter(items) {
    const f = S.ui.radar, q = f.q.trim().toLowerCase();
    const min = parseFloat(String(f.min).replace(',', '.')), max = parseFloat(String(f.max).replace(',', '.'));
    return items.filter(it => (!q || (it.match + ' ' + it.league + ' ' + it.selection + ' ' + it.market).toLowerCase().includes(q)) &&
      (!f.source || it.source === f.source) && (!f.phase || it.phase === f.phase) && (!f.verdict || it.verdict === f.verdict) &&
      (!f.conflict || it.conflict) && (!Number.isFinite(min) || it.odds >= min) && (!Number.isFinite(max) || it.odds <= max));
  }
  function viewRadar() {
    const sg = S.files.signals && S.files.signals.data;
    if (!sg) return head('Radar de sinais') + missing('os sinais');
    const f = S.ui.radar;
    const opts = (vals, cur) => '<option value="">Todos</option>' + vals.map(v => '<option' + (v === cur ? ' selected' : '') + '>' + esc(v) + '</option>').join('');
    const sources = [...new Set(sg.items.map(i => i.source))].sort(), phases = [...new Set(sg.items.map(i => i.phase).filter(Boolean))].sort(),
      verdicts = [...new Set(sg.items.map(i => i.verdict).filter(Boolean))].sort();
    let html = head('Radar de sinais', 'Sinais da agenda agrupados por jogo. Duplicações visuais eliminadas; cada ficha mostra a cronologia e a evidência favorável e contrária.');
    html += '<form class="filters panel" style="padding:10px 12px" id="radar-filters" role="search" onsubmit="return false">' +
      '<label>Pesquisa<input type="search" name="q" value="' + esc(f.q) + '" placeholder="Equipa, liga, seleção"></label>' +
      '<label>Fonte<select name="source">' + opts(sources, f.source) + '</select></label>' +
      '<label>Fase<select name="phase">' + opts(phases, f.phase) + '</select></label>' +
      '<label>Leitura<select name="verdict">' + opts(verdicts, f.verdict) + '</select></label>' +
      '<label>Odd mín.<input type="number" step="0.01" min="1" name="min" value="' + esc(f.min) + '" inputmode="decimal"></label>' +
      '<label>Odd máx.<input type="number" step="0.01" min="1" name="max" value="' + esc(f.max) + '" inputmode="decimal"></label>' +
      '<label class="check"><input type="checkbox" name="conflict"' + (f.conflict ? ' checked' : '') + '> Só contradições</label></form>';
    html += '<div id="radar-results">' + radarResults(sg) + '</div>';
    return html;
  }
  function radarResults(sg) {
    const items = radarFilter(sg.items);
    const games = C.groupByGame(items);
    const dupTotal = games.reduce((s, g) => s + g.duplicates, 0);
    const body = games.length ? '<div class="games">' + games.map(g => '<div class="game"><button type="button" data-game="' + esc(g.match_id) + '">' +
      '<span><span class="title">' + esc(g.match) + '</span><span class="sub"><span>' + esc(g.league || '') + '</span><span>' + int(g.signals.length) + ' sinal(is)</span><span>último ' + when(g.last_utc) + '</span>' +
      (g.duplicates ? '<span>' + int(g.duplicates) + ' duplicado(s) agrupado(s)</span>' : '') + '</span></span>' +
      '<span class="right">' + g.sources.map(src).join(' ') + Object.entries(g.verdicts).map(([k, v]) => tag(k + (v > 1 ? ' ×' + v : ''), VERD[k])).join('') + (g.conflict ? tag('contradição', 'bad') : '') + '</span></button></div>').join('') + '</div>'
      : '<div class="empty">Nenhum sinal corresponde aos filtros.</div>';
    return panel('Jogos', body, {flush: true, meta: int(games.length) + ' jogos · ' + int(items.length) + ' sinais' + (dupTotal ? ' · ' + int(dupTotal) + ' duplicados' : '') + ' · agenda ' + when(sg.agenda_generated_utc)});
  }
  function openGame(id) {
    const sg = S.files.signals && S.files.signals.data;
    if (!sg) return;
    const g = C.groupByGame(sg.items.filter(i => i.match_id === id))[0];
    if (!g) return;
    const market = sg.markets.find(m => m.match_id === id);
    const chrono = (market ? market.series.map(e => ({d: e.d, source: e.source, text: e.market + ' · ' + e.selection + (isNum(e.odds) ? ' @ ' + n(e.odds, 2) : '') + (isNum(e.total) ? ' · vol. ' + int(e.total) : '') + (e.phase ? ' · ' + e.phase : '')}))
      : g.signals.map(s => ({d: s.date_utc, source: s.source, text: s.market + ' · ' + s.selection + ' @ ' + n(s.odds, 2) + (s.phase ? ' · ' + s.phase : '')})));
    const ev = list => list.length ? '<ul>' + list.slice(0, 8).map(h => '<li><div>' + esc(h.id) + '</div><small class="note">' + int(h.games) + ' jogos · ' + int(h.hits) + ' acertos em ' + int(h.n) +
      ' · necessário ' + pct(h.break_even) + ' · P(acima) ' + pct(h.prob_above, 0) + ' · ' + esc(h.status || '') + '<br>Prospetivo: ' + econ(h.prospective && h.prospective.status) + '</small></li>').join('') + '</ul>' : '<p class="note">Nenhuma.</p>';
    const content = g.signals.map(s => '<section class="panel"><header><h3>' + esc(s.market + ' · ' + s.selection) + '</h3><span class="meta">' + when(s.date_utc) + '</span></header><div class="body stack">' +
      '<dl class="facts"><dt>Fonte</dt><dd>' + src(s.source) + ' · ' + esc(s.family || s.type) + '</dd><dt>Fase</dt><dd>' + esc(s.when || s.phase || '—') + (isNum(s.minute) ? ' · ' + s.minute + '′' : '') + '</dd>' +
      '<dt>Odd</dt><dd>' + n(s.odds, 2) + (isNum(s.odds_last) && s.odds_last !== s.odds ? ' → ' + n(s.odds_last, 2) : '') + ' · ' + esc(s.odds_band || '') + '</dd>' +
      '<dt>Reforço</dt><dd>' + (s.max_mult > 1 ? '×' + s.max_mult : 'nenhum') + '</dd><dt>Quota</dt><dd>' + (isNum(s.share) ? n(s.share) + ' %' : '—') + '</dd>' +
      '<dt>Leitura</dt><dd>' + tag(s.verdict || '—', VERD[s.verdict]) + (s.conflict ? ' ' + tag('contradição', 'bad') : '') + ' · não validado</dd>' +
      (s.duplicates ? '<dt>Duplicados</dt><dd>' + int(s.duplicates) + ' agrupados</dd>' : '') + '</dl>' +
      '<div class="evidence"><div class="for"><h4>A favor (' + int(s.good_total) + ')</h4>' + ev(s.good) + '</div><div class="against"><h4>Contra (' + int(s.bad_total) + ')</h4>' + ev(s.bad) + '</div></div></div></section>').join('');
    openDrawer(g.match, (g.league || '') + ' · ' + g.sources.join(', '),
      '<section class="panel"><header><h3>Cronologia</h3><span class="meta">' + (market ? 'histórico de mercado publicado' : 'só sinais da agenda (jogo sem histórico de mercado publicado)') + '</span></header><div class="body"><ol class="chrono">' +
      chrono.map(c => '<li class="' + esc(c.source) + '"><time>' + when(c.d) + '</time> ' + esc(c.source) + ' · ' + esc(c.text) + '</li>').join('') + '</ol></div></section>' + content +
      '<p class="note">Evidência exploratória por hipótese do gerador. Hipóteses partilham jogos e não têm correção de multiplicidade.</p>');
  }

  function hypTable(list, caption, opts = {}) {
    return table(['Hipótese', {t: 'Jogos', n: 1}, {t: 'Acerto', n: 1}, {t: 'Necessário', n: 1}, {t: 'ROI anunc.', n: 1}, 'Estado', 'Prospetivo'].concat(opts.born ? ['Criada'] : []),
      list.map(h => '<tr>' + td(esc(h.id) + (h.reason ? '<br><small class="note">' + esc(h.reason) + '</small>' : '')) + td(int(h.games), 'n') + td(pct(h.hit_rate), 'n') + td(pct(h.break_even), 'n') +
        td('<span class="' + sign(h.roi) + '">' + spct(h.roi) + '</span>', 'n') + td(tag(h.status || '—', STATUS[h.status])) +
        td(h.prospective ? econ(h.prospective.status) + ' <small class="note">' + int(h.prospective.games) + ' jogos</small>' : '—') + (opts.born ? td(day(h.born_utc)) : '') + '</tr>'), caption);
  }
  function viewBrain() {
    const l = S.files.learning && S.files.learning.data;
    if (!l) return head('Brain Intelligence') + missing('a aprendizagem');
    let html = head('Brain Intelligence', 'Diário da aprendizagem autónoma: hipóteses geradas, propostas pelo modelo de linguagem, rejeitadas e com replicação prospetiva exploratória.');
    html += kpis(Object.entries(l.counts).map(([k, v]) => [k, int(v), 'hipóteses']).concat([
      ['Execuções LLM', int(l.llm.runs), 'última ' + when(l.llm.last_run_utc) + ' · ' + esc(l.llm.last_outcome || '—')],
      ['Avaliação LLM', esc(l.llm.evaluation ? l.llm.evaluation.verdict : '—'), l.llm.evaluation ? esc(l.llm.evaluation.protocol) : ''],
      ['Esperadas por acaso', isNum(l.expected_by_chance) ? n(l.expected_by_chance, 1) : '—', 'entre as promissoras']]));
    html += '<div class="grid">';
    html += panel('Diário de aprendizagem', l.journal.length ? '<ul class="list">' + l.journal.map(j => '<li><div><b>' + esc(j.id) + '</b></div><div>' + esc(j.note) + '</div><small>' + when(j.at_utc) + ' · ' + esc(j.model || '') +
      ' · ' + int(j.n) + ' resultados · acerto ' + pct(j.hit_rate) + ' vs ' + pct(j.break_even) + ' · ' + esc(j.status || '') + '</small></li>').join('') + '</ul>' : '<div class="empty">Sem entradas.</div>',
      {span: 'span-6', flush: true, meta: 'texto gerado; não é evidência'});
    const track = l.track && l.track.by_source ? table(['Origem', {t: 'Hipóteses', n: 1}, {t: 'Com evidência prospetiva', n: 1}, {t: 'Jogos prospetivos', n: 1}],
      Object.entries(l.track.by_source).map(([k, v]) => '<tr>' + td(esc(k)) + td(int(v.hypotheses), 'n') + td(int(v.with_prospective_evidence), 'n') + td(int(v.prospective_games), 'n') + '</tr>'), 'Seguimento por origem') +
      '<p class="note" style="padding:0 12px 12px">' + esc(l.track.meaning || '') + '</p>' : '<div class="empty">Sem seguimento.</div>';
    html += panel('Alterações de comportamento · seguimento por origem', track, {span: 'span-6', flush: true});
    html += panel('Hipóteses propostas pelo modelo de linguagem', hypTable(l.llm.hypotheses, 'Hipóteses LLM', {born: true}), {flush: true, meta: int(l.llm.hypotheses.length) + ' · ' + (l.llm.evaluation ? note(l.llm.evaluation.reason) : '')});
    html += panel('Promissoras (exploratórias)', hypTable(l.promising, 'Hipóteses promissoras'), {span: 'span-6', flush: true, meta: int(l.promising.length)});
    html += panel('Replicação prospetiva exploratória positiva', hypTable(l.replicated_exploratory, 'Replicação exploratória'), {span: 'span-6', flush: true, meta: int(l.replicated_exploratory_total) + ' no total · não é validação'});
    html += panel('Rejeitadas (abandonadas)', hypTable(l.rejected, 'Hipóteses abandonadas'), {flush: true, meta: 'primeiras ' + int(l.rejected.length) + ' de ' + int(l.rejected_total)});
    const m = l.multiplicity || {};
    html += panel('Multiplicidade', '<dl class="facts"><dt>Testadas agora</dt><dd>' + int(m.tested_now) + '</dd><dt>Conjuntos de jogos distintos</dt><dd>' + int(m.distinct_game_sets_tested) + '</dd>' +
      '<dt>Promissoras ou melhor</dt><dd>' + int(m.promising_or_better) + '</dd><dt>Jogos (união)</dt><dd>' + int(m.promising_union_games) + ' de ' + int(m.promising_sum_games) + ' somados</dd></dl><p class="note">' + esc(m.note || '') + '</p>', {span: 'span-6'});
    html += panel('Limitações científicas', '<ul class="list">' + l.limitations.map(t => '<li>' + esc(t) + '</li>').join('') + '</ul>', {span: 'span-6', flush: true});
    return html + '</div>';
  }

  function viewLab() {
    const sc = S.files.science && S.files.science.data;
    if (!sc) return head('Scientific Lab') + missing('a ciência');
    const p = sc.prospective, v = sc.validation, proto = sc.protocol, F = sc.frozen;
    let html = head('Scientific Lab', 'Registo prospetivo append-only com critérios congelados antes da ativação. Esta página só lê: não altera o protocolo nem o registo.', verdictBox());
    if (!p) return html + '<div class="banner warn" style="margin:0">Relatório prospetivo ' + esc(S.prospectiveState) + '. Sem ele não são apresentados H1, H2 nem D1.</div>';
    if (v && !v.ok) html += '<div class="banner bad" style="margin:0">Relatório prospetivo com problemas — ' + esc(v.problems.concat(v.certification).join('; ')) + '. Estados não conformes com o protocolo não são apresentados como decisão.</div>';
    const certBad = v && v.certification.length > 0;
    const h1 = p.H1 || {}, h2 = p.H2 || {};
    const h1s = certBad && h1.status !== 'insufficient_games' ? ['certificação inválida', 'bad'] : (H1S[h1.status] || [h1.status || '—', '']);
    const h2s = certBad && !H2S[h2.status] ? ['certificação inválida', 'bad'] : (H2S[h2.status] || [h2.status || '—', '']);
    html += kpis([
      ['Ativação', day(p.activated_utc), when(p.activated_utc)],
      ['Entradas no registo', int(p.ledger_entries), 'append-only, cadeia SHA-256'],
      ['Jogos registados', int(p.games_registered), int(p.signals_registered) + ' sinais'],
      ['Resultado independente', int(p.games_with_independent_result), pct(p.independent_result_share) + ' dos jogos'],
      ['Conflitos', int(p.games_with_conflicting_results), 'excluídos da análise primária', p.games_with_conflicting_results ? 'neg' : ''],
      ['Sem resultado 72 h', int(p.games_without_result_after_72h), 'excluídos e contados', p.games_without_result_after_72h ? 'neg' : '']
    ]);
    html += '<div class="grid">';
    const bands = h1.minute_bands ? Object.entries(h1.minute_bands).map(([k, b]) => '<tr>' + td(esc(k) + '′') + td(int(b.games), 'n') + td(int(b.observed), 'n') + td(n(b.expected, 2), 'n') +
      td(b.games >= F.min_band ? (b.observed > b.expected ? tag('O > E', 'info') : tag('O ≤ E', '')) : tag('< ' + F.min_band + ' jogos', 'warn')) + '</tr>') : [];
    const strata = h1.score_strata ? Object.entries(h1.score_strata).map(([k, b]) => '<tr>' + td(esc(k.replace('margin_', 'margem ').replace('|goals_', ' · golos ').replace('level', 'empate').replace('one', '1').replace('two_plus', '2+'))) +
      td(int(b.games), 'n') + td(int(b.observed), 'n') + td(n(b.expected, 2), 'n') + '</tr>') : [];
    html += panel('H1 · golo adicional após sinal ao vivo', '<div class="stack"><div class="row"><span>Estado</span>' + tag(h1s[0], h1s[1]) + '</div>' +
      '<div class="row"><span>Jogos com resultado independente</span><b class="num">' + int(h1.games_with_result) + ' / ' + F.min_games + '</b></div>' + progress(h1.games_with_result, F.min_games) +
      '<dl class="facts"><dt>População</dt><dd>' + int(h1.games_in_population) + ' jogos</dd><dt>Observado (O)</dt><dd>' + int(h1.observed) + '</dd><dt>Esperado (E)</dt><dd>' + n(h1.expected, 3) + '</dd>' +
      '<dt>z publicado</dt><dd>' + n(h1.z, 3) + ' (limiar ' + n(F.z_crit, 3) + ', só aplicável a partir de ' + F.min_games + ' jogos)</dd></dl>' +
      table(['Faixa de minutos', {t: 'Jogos', n: 1}, {t: 'O', n: 1}, {t: 'E', n: 1}, 'Critério'], bands, 'H1 por faixa de minutos') +
      table(['Estado do marcador', {t: 'Jogos', n: 1}, {t: 'O', n: 1}, {t: 'E', n: 1}], strata, 'H1 por estado do marcador') +
      '<p class="note">' + note(h1.note) + '</p></div>', {span: 'span-7'});
    html += panel('H2 · jogo aberto, odds [1,50; 2,50)', '<div class="stack"><div class="row"><span>Estado</span>' + tag(h2s[0], h2s[1]) + '</div>' +
      '<div class="row"><span>Jogos liquidados</span><b class="num">' + int(h2.games_settled) + ' / ' + F.min_games + '</b></div>' + progress(h2.games_settled, F.min_games) +
      '<dl class="facts"><dt>População</dt><dd>' + int(h2.games_in_population) + ' jogos</dd><dt>Ganhos / perdas</dt><dd>' + int(h2.wins) + ' / ' + int(h2.losses) + '</dd>' +
      '<dt>Probabilidade implícita média</dt><dd>' + pct(h2.mean_implied) + '</dd></dl><p class="note">' + note(h2.note) + '</p></div>', {span: 'span-5'});
    html += panel('D1 · totais em sentido oposto', '<div class="stack"><div class="row"><span>Jogos</span><b class="num">' + int(p.D1 && p.D1.opposite_totals_games) + '</b></div>' +
      '<div class="row"><span>Estado</span>' + tag('apenas diagnóstico', '') + '</div><p class="note">' + esc(proto && proto.D1 ? proto.D1.definition : 'Sem teste de hipótese.') + '</p></div>', {span: 'span-5'});
    html += panel('Exclusões e integridade da amostra', '<dl class="facts"><dt>Sinais registados</dt><dd>' + int(p.signals_registered) + '</dd><dt>Antes do resultado</dt><dd>' + int(p.signals_registered_before_result) + '</dd>' +
      '<dt>Resultado já conhecido (excluídos)</dt><dd>' + int(p.signals_result_known_at_registration) + '</dd><dt>Jogos em conflito (excluídos)</dt><dd>' + int(p.games_with_conflicting_results) + '</dd>' +
      '<dt>Sem resultado após 72 h</dt><dd>' + int(p.games_without_result_after_72h) + '</dd><dt>Esquema</dt><dd>' + esc(p.schema) + '</dd></dl>' +
      (proto ? '<p class="note">Conflitos: ' + esc(proto.conflict || '') + '</p><p class="note">Ausência: ' + esc(proto.missing || '') + '</p>' : ''), {span: 'span-7'});
    html += panel('Critérios congelados', proto ? '<div class="stack">' + ['H1', 'H2'].map(k => proto[k] ? '<div><b>' + k + '</b> — <span class="note">' + esc(proto[k].status) + '</span><p class="note">População: ' + esc(proto[k].population) + '</p><p class="note">Decisão: ' + esc(proto[k].decision) + '</p></div>' : '').join('') +
      '<p class="note">' + esc(proto.frozen) + '</p><p class="note">Âmbito: ' + esc(proto.scope) + '</p></div>'
      : '<dl class="facts"><dt>Jogos mínimos</dt><dd>' + F.min_games + '</dd><dt>z crítico</dt><dd>' + F.z_crit + '</dd><dt>Faixa mínima</dt><dd>' + F.min_band + ' jogos</dd></dl><p class="note">Texto do protocolo indisponível; valores de scangoal/prospective.py.</p>',
      {meta: 'scangoal/prospective_protocol.json'});
    const c = sc.certification, sb = sc.strategy_board || {};
    html += panel('Certificação das estratégias (relatório principal)', '<dl class="facts"><dt>Método</dt><dd>' + esc(sc.validation_method ? sc.validation_method.method : '—') + '</dd><dt>Estado</dt><dd>' + esc(c.state) + '</dd>' +
      '<dt>Registo válido</dt><dd>' + (sc.validation_method && sc.validation_method.ledger_valid ? 'sim' : 'não') + '</dd><dt>Validadas</dt><dd>' + int(c.validated) + '</dd>' +
      '<dt>Afirmação estatística</dt><dd>' + esc(sb.statistical_claim || '—') + '</dd><dt>Critérios (SHA-256)</dt><dd style="word-break:break-all">' + esc(sb.criteria_sha256 || '—') + '</dd></dl>' +
      (sb.counts ? '<div class="stack" style="margin-top:10px">' + Object.entries(sb.counts).map(([k, v]) => '<div class="row"><span>' + esc(k.replace(/_/g, ' ')) + '</span><b class="num">' + int(v) + '</b></div>').join('') + '</div>' : '') +
      (c.invalid.length ? '<p class="note neg">' + esc(c.invalid.join('; ')) + '</p>' : ''), {span: 'span-6'});
    const e = sc.eligibility || {};
    html += panel('Elegibilidade dos sinais', '<dl class="facts"><dt>Total</dt><dd>' + int(e.total) + '</dd><dt>Elegíveis</dt><dd>' + int(e.eligible) + '</dd><dt>Excluídos</dt><dd>' + int(e.excluded) + '</dd></dl>' +
      (e.reasons ? table(['Motivo', {t: 'Sinais', n: 1}], Object.entries(e.reasons).map(([k, v]) => '<tr>' + td(esc(k.replace(/_/g, ' '))) + td(int(v), 'n') + '</tr>'), 'Motivos de exclusão') : ''), {span: 'span-6'});
    return html + '</div>';
  }

  function viewPerformance() {
    const pf = S.files.performance && S.files.performance.data;
    if (!pf) return head('Performance Analytics') + missing('o desempenho');
    const ref = pf.reference || {}, roi = ref.roi || {};
    let html = head('Performance Analytics', 'ROI histórico à odd anunciada e com perdas de 2 % e 5 %, saldo, quedas, intervalos de confiança e comparações por fonte, grupo e período. Descritivo: não demonstra rentabilidade.');
    html += kpis([
      ['ROI Q7 · odd anunciada', spct(roi['odd-0%'] && roi['odd-0%'].roi), 'IC95 ' + ci(roi['odd-0%'] && roi['odd-0%'].ci95), sign(roi['odd-0%'] && roi['odd-0%'].roi)],
      ['ROI · odd −2 %', spct(roi['odd-2%'] && roi['odd-2%'].roi), 'IC95 ' + ci(roi['odd-2%'] && roi['odd-2%'].ci95), sign(roi['odd-2%'] && roi['odd-2%'].roi)],
      ['ROI · odd −5 %', spct(roi['odd-5%'] && roi['odd-5%'].roi), 'IC95 ' + ci(roi['odd-5%'] && roi['odd-5%'].ci95), sign(roi['odd-5%'] && roi['odd-5%'].roi)],
      ['Saldo', units(ref.units) + ' u', int(ref.settled) + ' liquidadas', sign(ref.units)],
      ['Maior queda (histórico)', pf.luck ? n(pf.luck.max_drawdown, 2) + ' u' : '—', 'pior sequência ' + (pf.luck ? int(pf.luck.longest_losing_run) : '—')],
      ['Oscilação esperada', pf.luck ? '±' + n(pf.luck.luck_sd * 2, 1) + ' u' : '—', 'z ' + (pf.luck ? n(pf.luck.z, 2) : '—') + ' (sorte)']
    ]);
    html += '<div class="grid">';
    const pts = pf.timeline.map((p, i) => ({x: i, y: p.u, label: when(p.d) + ' · ' + (p.m || p.t) + ' · ' + units(p.w) + ' → saldo ' + units(p.u)}));
    html += panel('Saldo acumulado', lineChart([{name: 'Saldo', points: pts}], {zero: true, fy: v => units(v, 0), fx: v => pf.timeline[Math.round(v)] ? day(pf.timeline[Math.round(v)].d) : '', label: 'Saldo acumulado nas últimas liquidações'}) +
      '<p class="note">Janela publicada: ' + esc(pf.timeline_window.note) + ' de ' + int(pf.timeline_window.settled) + '. Maior queda dentro da janela: ' + n(pf.drawdown_window.max, 2) + ' u (calculada no navegador sobre a série publicada).</p>', {span: 'span-8', meta: 'unidades à odd anunciada'});
    const sens = ['odd-0%', 'odd-2%', 'odd-5%'].map(k => '<tr>' + td(k === 'odd-0%' ? 'Odd anunciada' : 'Odd −' + k.slice(4)) + td('<span class="' + sign(roi[k] && roi[k].roi) + '">' + spct(roi[k] && roi[k].roi) + '</span>', 'n') + td(ci(roi[k] && roi[k].ci95), 'n') + '</tr>');
    html += panel('Sensibilidade às odds (Q7)', table(['Cenário', {t: 'ROI', n: 1}, {t: 'IC95 (por jogo)', n: 1}], sens, 'Sensibilidade às odds') + '<p class="note" style="padding:0 12px 12px">' + (ref.conclusive ? 'Amostra acima do mínimo de 300 liquidadas.' : 'Sem conclusão (< 300 liquidadas).') + ' Odds anunciadas não são preços executáveis.</p>', {span: 'span-4', flush: true});
    const srcRows = Object.entries(pf.periods.by_source).map(([k, r]) => '<tr>' + td(src(k)) + td(int(r.bets), 'n') + td(int(r.settled), 'n') + td(int(r.wins), 'n') + td('<span class="' + sign(r.units) + '">' + units(r.units) + '</span>', 'n') + td('<span class="' + sign(r.roi) + '">' + spct(r.roi) + '</span>', 'n') + '</tr>');
    html += panel('Por fonte', table(['Fonte', {t: 'Apostas', n: 1}, {t: 'Liquidadas', n: 1}, {t: 'Ganhas', n: 1}, {t: 'Unidades', n: 1}, {t: 'ROI', n: 1}], srcRows, 'Desempenho por fonte') +
      '<p class="note" style="padding:0 12px 12px">Somas simples das apostas do simulador (1 unidade por aposta à odd anunciada); sem intervalo de confiança.</p>', {span: 'span-6', flush: true});
    const weeks = Object.entries(pf.periods.by_week).sort();
    html += panel('Por semana (unidades)', barsChart(weeks.map(([k, r]) => ({label: day(k + 'T12:00:00Z'), v: r.units, text: units(r.units, 1)})), {label: 'Unidades por semana'}) +
      table(['Semana', {t: 'Liquidadas', n: 1}, {t: 'Unidades', n: 1}, {t: 'ROI', n: 1}], weeks.map(([k, r]) => '<tr>' + td(day(k + 'T12:00:00Z')) + td(int(r.settled), 'n') + td(units(r.units), 'n') + td(spct(r.roi), 'n') + '</tr>'), 'Desempenho por semana'), {span: 'span-6'});
    const groups = Object.entries(pf.groups).map(([k, g]) => '<tr>' + td(esc(k)) + td(int(g.bets), 'n') + td(int(g.settled), 'n') + td('<span class="' + sign(g.units) + '">' + units(g.units) + '</span>', 'n') +
      ['odd-0%', 'odd-2%', 'odd-5%'].map(o => td(spct(g.roi && g.roi[o] && g.roi[o].roi), 'n')).join('') + td(ci(g.roi && g.roi['odd-0%'] && g.roi['odd-0%'].ci95), 'n') + td(tag(g.conclusive ? 'ok' : 'sem conclusão', g.conclusive ? '' : 'warn')) + '</tr>');
    html += panel('Por grupo e mercado', table(['Grupo', {t: 'Apostas', n: 1}, {t: 'Liq.', n: 1}, {t: 'Unid.', n: 1}, {t: 'ROI', n: 1}, {t: '−2 %', n: 1}, {t: '−5 %', n: 1}, {t: 'IC95', n: 1}, 'Conclusão'], groups, 'Desempenho por grupo'), {flush: true});
    const acc = Object.entries(pf.accuracy).map(([k, a]) => '<tr>' + td(esc(k)) + td(int(a.with_result), 'n') + td(pct(a.hit_rate), 'n') + td(ci(a.ci95).replace(/\+/g, ''), 'n') + td(pct(a.break_even), 'n') + td(n(a.avg_odds, 2), 'n') + td(spct(a.roi_announced_odds), 'n') + '</tr>');
    html += panel('Taxa de acerto por tipo', table(['Tipo', {t: 'Com resultado', n: 1}, {t: 'Acerto', n: 1}, {t: 'IC95', n: 1}, {t: 'Necessário', n: 1}, {t: 'Odd média', n: 1}, {t: 'ROI', n: 1}], acc, 'Acerto por tipo'), {span: 'span-6', flush: true});
    const cal = pf.calibration.map(b => '<tr>' + td(esc(b.band)) + td(int(b.n), 'n') + td(pct(b.hit_rate), 'n') + td(pct(b.break_even), 'n') + td(spct(b.roi), 'n') + '</tr>');
    html += panel('Calibração por faixa de odd', table(['Faixa', {t: 'n', n: 1}, {t: 'Acerto', n: 1}, {t: 'Necessário', n: 1}, {t: 'ROI', n: 1}], cal, 'Calibração'), {span: 'span-6', flush: true});
    const wf = pf.walk_forward;
    if (wf) {
      const wfr = ['followed', 'skipped', 'not_reconstructible'].filter(k => wf[k]).map(k => '<tr>' + td({followed: 'Seguidas', skipped: 'Ignoradas', not_reconstructible: 'Não reconstituíveis'}[k]) + td(int(wf[k].settled), 'n') + td(units(wf[k].units), 'n') + td(spct(wf[k].roi), 'n') + td(spct(wf[k].roi_cut), 'n') + '</tr>');
      const curve = pf.walk_forward_curve.map((y, i) => ({x: i, y, label: 'Passo ' + (i + 1) + ' · ' + units(y)}));
      html += panel('ROI prospetivo descritivo · reconstituição cronológica', table(['Apostas', {t: 'Liquidadas', n: 1}, {t: 'Unidades', n: 1}, {t: 'ROI', n: 1}, {t: 'ROI −2 %', n: 1}], wfr, 'Reconstituição cronológica') +
        lineChart([{name: 'Seguidas', points: curve}], {zero: true, fy: v => units(v, 0), fx: v => '#' + n(v + 1), label: 'Curva das apostas seguidas'}) +
        '<p class="note">Reconstituição com regras conhecidas em cada momento (brain.walk_forward). Descritiva e exploratória; não é o registo prospetivo congelado.</p>', {span: 'span-6'});
    }
    const reg = pf.regime.map(r => '<tr>' + td(esc(r.type)) + td(int(r.n), 'n') + td(esc(r.state)) + td(r.before ? pct(r.before.hit_rate) : '—', 'n') + td(r.recent ? pct(r.recent.hit_rate) : '—', 'n') + td(isNum(r.z) ? n(r.z, 2) : '—', 'n') + '</tr>');
    html += panel('Mudança de regime (recente vs anterior)', table(['Tipo', {t: 'n', n: 1}, 'Estado', {t: 'Antes', n: 1}, {t: 'Recente', n: 1}, {t: 'z', n: 1}], reg, 'Regime'), {span: 'span-6', flush: true});
    return html + '</div>';
  }

  function viewOperations() {
    const o = S.files.operations && S.files.operations.data;
    if (!o) return head('Data Quality & Operations') + missing('as operações');
    const live = iso => C.freshness(iso, Date.now());
    const lagRow = (name, x, limit) => { const f = live(x.at_utc), late = isNum(f.minutes) && f.minutes > limit;
      return '<tr>' + td(esc(name)) + td(when(x.at_utc)) + td(ago(f.minutes), 'n') + td(tag(late ? 'atrasado' : 'normal', late ? 'warn' : 'good')) + td(int(limit) + ' min', 'n') + '</tr>'; };
    let html = head('Data Quality & Operations', 'Recolha, continuidade, atrasos, falhas, duplicados, resultados contraditórios, integridade e cobertura efetiva de cada vista.');
    html += '<div class="grid">';
    html += panel('Recolha e fornecedores', table(['Processo', 'Última execução', {t: 'Há', n: 1}, 'Estado', {t: 'Esperado até', n: 1}],
      [lagRow('Recolha Telegram', o.collector, 45), lagRow('Highlightly (resultados)', o.highlightly, 6 * 60), lagRow('BSD (resultados)', o.bsd, 3 * 60)], 'Estado da recolha'), {span: 'span-7', flush: true});
    const cnt = o.counters, warnIf = v => isNum(v) && v > 0 ? 'warn' : 'good';
    const cRows = [['Versões de mensagens lidas', cnt.versions, null], ['Edições que mudaram o sinal', cnt.edits_changing_signal, 1], ['Marca ✅/❌ do bot vs liquidação', cnt.bot_mark_disagreements, 1],
      ['Highlightly vs bot', cnt.hl_vs_bot_disagreements, 1], ['BSD vs bot', cnt.bsd_vs_bot_disagreements, 1], ['Manual vs outras fontes', cnt.manual_vs_other_disagreements, 1],
      ['Resultados impossíveis observados', cnt.impossible_ft_observations, 1], ['Minutos de golos inconsistentes', cnt.goal_minutes_inconsistent, 1],
      ['Nomes por rever (Highlightly)', cnt.hl_near_misses, null], ['Nomes por rever (BSD)', cnt.bsd_near_misses, null], ['Revisões de nomes pendentes', o.name_review, null],
      ['Duplicados na agenda (agrupados)', o.agenda_duplicates, 1]]
      .map(([k, v, w]) => '<tr>' + td(esc(k)) + td(int(v), 'n') + td(w ? tag(v > 0 ? 'rever' : 'ok', warnIf(v)) : tag('informativo', '')) + '</tr>');
    html += panel('Integridade e conflitos', table(['Indicador', {t: 'Valor', n: 1}, 'Estado'], cRows, 'Integridade'), {span: 'span-5', flush: true});
    const ex = o.exclusions;
    html += panel('Exclusões', table(['Motivo', {t: 'Apostas', n: 1}], [['Vistas já com resultado', ex.first_seen_with_result], ['Editadas antes de vistas', ex.edited_before_seen], ['Lote (≥ 5 no mesmo segundo)', ex.burst],
      ['Odd < 1,10', ex.low_odds], ['Mensagens por ler', ex.unparsed], ['Mercados sem liquidação', ex.unsettled_market]].map(([k, v]) => '<tr>' + td(esc(k)) + td(int(v), 'n') + '</tr>'), 'Exclusões'), {span: 'span-4', flush: true});
    const ct = o.continuity;
    html += panel('Continuidade', '<dl class="facts"><dt>Dias com apostas</dt><dd>' + int(ct.days) + '</dd><dt>Primeiro dia</dt><dd>' + esc(ct.first_day || '—') + '</dd><dt>Último dia</dt><dd>' + esc(ct.last_day || '—') + '</dd>' +
      '<dt>Interrupções</dt><dd>' + (ct.gaps.length ? ct.gaps.map(g => esc(g.after + ' → ' + g.before + ' (' + g.missing_days + ' d)')).join('<br>') : 'nenhuma') + '</dd></dl>' +
      (o.prospective ? '<dl class="facts" style="margin-top:10px"><dt>Conflitos prospetivos</dt><dd>' + int(o.prospective.conflicts) + '</dd><dt>Sem resultado 72 h</dt><dd>' + int(o.prospective.missing_72h) + '</dd>' +
        '<dt>Resultado conhecido no registo</dt><dd>' + int(o.prospective.result_known_at_registration) + '</dd></dl>' : '<p class="note">Relatório prospetivo ' + esc(S.prospectiveState) + '.</p>'), {span: 'span-4'});
    const cv = o.coverage;
    html += panel('Cobertura efetiva por vista', table(['Vista', 'Cobertura'], [
      ['Command Center / Performance (ROI)', int(cv.settled) + ' liquidadas de ' + int(cv.tracked) + ' acompanhadas'],
      ['Saldo acumulado', int(cv.timeline_points) + ' de ' + int(cv.settled) + ' liquidações (janela publicada)'],
      ['Market Intelligence (cronologia)', int(cv.match_histories) + ' jogos de ' + int(cv.committed_games) + ' (' + pct(cv.committed_games ? cv.match_histories / cv.committed_games : null) + ')'],
      ['Mapa de calor e períodos', int(cv.simulation_entries) + ' apostas (histórico completo)'],
      ['Apostas recentes', int(cv.recent_bets) + ' de ' + int(cv.tracked)]].map(([k, v]) => '<tr>' + td(esc(k)) + td(v) + '</tr>'), 'Cobertura'), {span: 'span-4', flush: true});
    const types = Object.entries(o.types).sort((a, b) => b[1] - a[1]).map(([k, v]) => '<tr>' + td(esc(k === 'None' ? 'sem tipo reconhecido' : k)) + td(int(v), 'n') + '</tr>');
    html += panel('Mensagens por tipo', table(['Tipo', {t: 'Mensagens', n: 1}], types, 'Mensagens por tipo'), {span: 'span-5', flush: true});
    html += panel('Jogos por liquidar', table(['Jogo', 'Liga', 'Sinal', 'Motivo'], o.unsettled.slice(0, 40).map(u => '<tr>' + td(esc(u.match)) + td(esc(u.league || '')) + td(when(u.date_utc)) + td(esc(u.why)) + '</tr>'), 'Jogos por liquidar'),
      {span: 'span-7', flush: true, meta: int(o.unsettled.length) + ' jogos'});
    const st = S.stats;
    html += panel('Carregamento desta página', '<dl class="facts"><dt>Fonte de dados</dt><dd>' + esc(S.mode || '—') + '</dd><dt>Pedidos</dt><dd>' + int(st.requests) + '</dd><dt>Sem alterações</dt><dd>' + int(st.notModified) + '</dd>' +
      '<dt>Falhas</dt><dd>' + int(st.failures) + '</dd><dt>Snapshots descarregados</dt><dd>' + int(st.snapshotFetches) + '</dd><dt>Derivações no navegador</dt><dd>' + int(st.derivations) + '</dd>' +
      '<dt>Última atualização válida</dt><dd>' + (S.lastOk ? when(new Date(S.lastOk).toISOString()) : '—') + '</dd><dt>Relatório prospetivo</dt><dd>' + esc(S.prospectiveState) + '</dd></dl>', {span: 'span-5'});
    return html + '</div>';
  }

  function viewKnowledge() {
    return head('Knowledge Center', 'Integração com o Goal-ROI Brain e o cofre Obsidian.') +
      '<div class="grid">' + panel('Integração desativada', '<div class="knowledge-off"><p style="margin:0">' + tag('desativada', 'warn') + '</p>' +
      '<p>O Goal-ROI Brain é um cofre privado. Esta página é pública e não existe ainda um canal seguro que publique apenas indicadores agregados, por isso nenhum conteúdo do Brain é carregado ou mostrado aqui.</p>' +
      '<p>Condições para ativar (decisão humana):</p><ul><li>um ficheiro agregado gerado pelo próprio Brain, com uma lista fechada de campos numéricos (por exemplo, número de PRs indexadas, relações curadas, temas);</li>' +
      '<li>sem texto livre, títulos de notas, caminhos, autores ou ligações;</li><li>verificação automática de privacidade antes da publicação, igual à aplicada aos snapshots desta página;</li>' +
      '<li>publicação sem credenciais novas e sem custo.</li></ul><p class="note">Os indicadores científicos públicos (H1, H2, D1, certificação) já estão no <a href="#lab">Scientific Lab</a>, lidos diretamente da produção.</p></div>') + '</div>';
  }

  const RENDER = {command: viewCommand, market: viewMarket, radar: viewRadar, brain: viewBrain, lab: viewLab, performance: viewPerformance, operations: viewOperations, knowledge: viewKnowledge};

  // ---------- navegação ----------
  function renderView(focus) {
    const el = $('view-' + S.view);
    const key = S.revision + ':' + NEEDS[S.view].map(k => S.files[k] ? 1 : 0).join('');
    if (S.rendered[S.view] !== key) {
      S.charts = {};
      el.innerHTML = RENDER[S.view]();
      bindCharts(el);
      S.rendered[S.view] = key;
    }
    if (focus) { const h = el.querySelector('h2'); if (h) h.focus({preventScroll: true}); }
  }
  function selectView(focus) {
    const id = (location.hash || '#command').slice(1);
    S.view = VIEWS.includes(id) ? id : 'command';
    for (const v of VIEWS) $('view-' + v).hidden = v !== S.view;
    for (const a of document.querySelectorAll('.nav a')) { if (a.dataset.view === S.view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); }
    const title = $('view-' + S.view).dataset.title;
    $('view-title').textContent = title;
    document.title = title + ' · Goal-ROI Intelligence';
    renderView(focus);
    if (focus) window.scrollTo(0, 0);
  }

  // ---------- gaveta ----------
  let lastFocus = null;
  function openDrawer(title, sub, html) {
    lastFocus = document.activeElement;
    $('drawer-title').textContent = title; $('drawer-sub').textContent = sub;
    $('drawer-content').innerHTML = html;
    $('drawer').hidden = false; $('scrim').hidden = false;
    $('drawer-close').focus();
  }
  function closeDrawer() {
    $('drawer').hidden = true; $('scrim').hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // ---------- tema ----------
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  function theme() { const t = document.documentElement.dataset.theme; return t === 'dark' || t === 'light' ? t : media.matches ? 'dark' : 'light'; }
  function showTheme() { $('theme').setAttribute('aria-pressed', String(theme() === 'dark')); }

  // ---------- eventos ----------
  document.addEventListener('click', e => {
    const g = e.target.closest('[data-game]');
    if (g) { openGame(g.dataset.game); return; }
    const m = e.target.closest('tr[data-market]');
    if (m) { S.ui.market = m.dataset.market; S.rendered.market = null; renderView(false); const row = document.querySelector('tr[data-market="' + CSS.escape(S.ui.market) + '"]'); if (row) row.focus(); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !$('drawer').hidden) closeDrawer();
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('tr[data-market]')) { e.preventDefault(); e.target.click(); }
  });
  document.addEventListener('input', e => {
    const form = e.target.closest('#radar-filters');
    if (!form) return;
    const f = S.ui.radar, el = e.target;
    f[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    const sg = S.files.signals && S.files.signals.data;
    if (sg) $('radar-results').innerHTML = radarResults(sg); // só a lista; os filtros mantêm foco e valores
  });
  $('drawer-close').onclick = closeDrawer;
  $('scrim').onclick = closeDrawer;
  $('refresh').onclick = () => refresh();
  $('theme').onclick = () => {
    const next = theme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('goal-roi-v6-theme', next); } catch (e) {}
    showTheme();
  };
  media.addEventListener && media.addEventListener('change', showTheme);
  window.addEventListener('hashchange', () => selectView(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });

  showTheme();
  selectView(false);
  refresh();
  setInterval(refresh, CFG.refreshMs);
  setInterval(() => { if (!document.hidden && S.files.summary) paintChrome(); }, CFG.tickMs);
  window.__V6 = {state: S, refresh}; // diagnóstico e testes
})();

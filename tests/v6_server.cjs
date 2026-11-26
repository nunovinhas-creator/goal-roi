// Servidor HTTP local para os testes V6: serve docs/ com ETag real (para medir respostas 304) e permite
// substituir, atrasar ou falhar qualquer caminho. Sem serviços externos.
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');

const ROOT = path.join(__dirname, '..');
// Os mesmos cabeçalhos de cache do GitHub Pages em produção (Fastly): max-age=600, ETag partilhado pelo 206 e pelo 200.
// Com max-age=0 os testes não reproduziam a reutilização de um 206 em cache como corpo de um pedido completo.
const CACHE = process.env.V6_TEST_CACHE || 'max-age=600';
const TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.csv': 'text/csv'};

function start(options = {}) {
  const state = {overrides: new Map(), log: [], bytes: 0};
  const files = Object.assign({
    '/__raw/prospective_report.json': path.join(ROOT, 'data/prospective_report.json'),
    '/__raw/prospective_protocol.json': path.join(ROOT, 'scangoal/prospective_protocol.json')
  }, options.files || {});
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const p = decodeURIComponent(url.pathname);
    const o = state.overrides.get(p);
    // Simula o defeito observado em produção: depois de um pedido parcial, o primeiro pedido completo recebe só o fragmento.
    if (options.truncateAfterRange || state.truncateAfterRange) {
      if (req.headers.range) state.pendingTruncate = true;
      else if (state.pendingTruncate && p === '/report.json') {
        state.pendingTruncate = false;
        const frag = fs.readFileSync(path.join(ROOT, 'docs/report.json')).subarray(0, 512);
        state.log.push({path: p, status: 200, bytes: frag.length, truncated: true});
        res.writeHead(200, {'content-type': 'application/json', 'cache-control': CACHE});
        return res.end(frag);
      }
    }
    let status = 200, body, served = p;
    try {
      if (o && o.delay) await new Promise(r => setTimeout(r, typeof o.delay === 'function' ? o.delay() : o.delay));
      if (o && o.status) status = o.status;
      if (o && o.body !== undefined) body = Buffer.from(typeof o.body === 'function' ? o.body() : o.body);
      else {
        let file = files[p] || path.join(ROOT, 'docs', p.endsWith('/') ? p + 'index.html' : p);
        if ((!files[p] && !file.startsWith(path.join(ROOT, 'docs'))) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) status = o && o.status ? o.status : 404;
        else { body = fs.readFileSync(file); served = file; }
      }
    } catch (e) { status = 500; }
    const type = TYPES[path.extname(served)] || TYPES[path.extname(p)] || 'application/octet-stream';
    if (status !== 200 || !body) {
      state.log.push({path: p, status, bytes: 0});
      res.writeHead(status, {'content-type': 'text/plain'});
      return res.end(status === 404 ? 'not found' : 'error');
    }
    const etag = '"' + crypto.createHash('sha1').update(body).digest('hex') + '"';
    const range = /^bytes=(\d+)-(\d+)$/.exec(req.headers.range || '');
    if (range && !(o && o.noRange) && !options.noRange) {
      const part = body.subarray(Number(range[1]), Number(range[2]) + 1);
      state.bytes += part.length;
      state.log.push({path: p, status: 206, bytes: part.length});
      res.writeHead(206, {'content-type': type, etag, 'cache-control': CACHE, 'accept-ranges': 'bytes', 'content-range': 'bytes ' + range[1] + '-' + (Number(range[1]) + part.length - 1) + '/' + body.length, 'content-length': part.length});
      return res.end(part);
    }
    if (req.headers['if-none-match'] === etag) {
      state.log.push({path: p, status: 304, bytes: 0});
      res.writeHead(304, {etag, 'cache-control': CACHE});
      return res.end();
    }
    // Como o GitHub Pages: o 200 vai comprimido com gzip quando o cliente o aceita; o 206 vai sem compressão.
    if (/\bgzip\b/.test(req.headers['accept-encoding'] || '') && !options.noGzip) {
      const gz = zlib.gzipSync(body);
      state.bytes += gz.length;
      state.log.push({path: p, status: 200, bytes: gz.length});
      res.writeHead(200, {'content-type': type, etag, 'cache-control': CACHE, 'accept-ranges': 'bytes', 'content-encoding': 'gzip', 'vary': 'Accept-Encoding', 'content-length': gz.length});
      return res.end(gz);
    }
    state.bytes += body.length;
    state.log.push({path: p, status: 200, bytes: body.length});
    res.writeHead(200, {'content-type': type, etag, 'cache-control': CACHE, 'accept-ranges': 'bytes', 'content-length': body.length});
    res.end(body);
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => {
    const base = 'http://127.0.0.1:' + server.address().port;
    resolve({server, state, base, close: () => new Promise(r => server.close(r)),
      set: (p, o) => state.overrides.set(p, o), clear: p => (p ? state.overrides.delete(p) : state.overrides.clear()),
      reset: () => { state.log.length = 0; state.bytes = 0; }});
  }));
}

const V6_CONFIG = {prospective: '/__raw/prospective_report.json', protocol: '/__raw/prospective_protocol.json', refreshMs: 3600000};
module.exports = {start, ROOT, V6_CONFIG};

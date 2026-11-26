// Staging HTTPS local e isolado para os snapshots V7 (custo 0, sem rede externa, nada publicado).
//
// - Gera uma CA e um certificado de servidor efémeros (openssl) numa pasta temporária, válidos para 127.0.0.1/localhost.
// - Serve uma pasta de origem por HTTPS com os cabeçalhos do GitHub Pages (Cache-Control: max-age=600, ETag).
// - À frente da origem, uma cache partilhada que imita uma CDN: chave com ou sem query string (keyMode 'full'|'path'),
//   frescura por max-age, relógio controlado (advance) para ensaiar horas em segundos.
// - Regista acertos, falhas e "chaves envenenadas": pedidos ?v=<sha16> cujo corpo guardado não tem esse SHA.
'use strict';
const https = require('node:https');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');

function makeTls(dir) {
  const run = args => execFileSync('openssl', args, {cwd: dir, stdio: 'pipe'});
  run(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=goal-roi staging CA', '-keyout', 'ca.key', '-out', 'ca.pem',
       '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign']);
  run(['req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost', '-keyout', 'srv.key', '-out', 'srv.csr']);
  fs.writeFileSync(path.join(dir, 'ext.cnf'), 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n');
  run(['x509', '-req', '-in', 'srv.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial', '-days', '1', '-extfile', 'ext.cnf', '-out', 'srv.pem']);
  return {ca: fs.readFileSync(path.join(dir, 'ca.pem')), key: fs.readFileSync(path.join(dir, 'srv.key')), cert: fs.readFileSync(path.join(dir, 'srv.pem'))};
}

const TYPES = {'.json': 'application/json', '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'};

async function start({root, ttl = 600, keyMode = 'full'} = {}) {
  if (!root) throw new Error('root obrigatório');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'goal-roi-staging-'));
  const tls = makeTls(tmp);
  let clock = 0; // segundos simulados
  const cache = new Map();
  const stats = {hits: 0, misses: 0, origin: 0, poisoned_keys: 0, requests: []};
  const origin = rel => {
    const file = path.resolve(root, '.' + rel);
    if (!file.startsWith(path.resolve(root) + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return null;
    stats.origin++;
    return fs.readFileSync(file);
  };
  const server = https.createServer({key: tls.key, cert: tls.cert}, (req, res) => {
    const url = new URL(req.url, 'https://localhost');
    const key = keyMode === 'full' ? url.pathname + url.search : url.pathname;
    let entry = cache.get(key), hit = !!entry && clock - entry.at < ttl;
    if (!hit) {
      const body = origin(decodeURIComponent(url.pathname));
      if (!body) { stats.requests.push({key, status: 404}); res.writeHead(404); return res.end('not found'); }
      entry = {body, at: clock, etag: '"' + crypto.createHash('sha1').update(body).digest('hex') + '"'};
      cache.set(key, entry);
      stats.misses++;
      // Chave "envenenada": o corpo guardado não tem o SHA-256 que a chave promete (?v=<sha> ou objects/<sha>/…).
      const v = url.searchParams.get('v') || (/^\/objects\/([0-9a-f]{64})\//.exec(url.pathname) || [])[1];
      if (v && crypto.createHash('sha256').update(body).digest('hex').slice(0, v.length) !== v) stats.poisoned_keys++;
    } else stats.hits++;
    const age = clock - entry.at;
    stats.requests.push({key, status: 200, hit, age});
    if (req.headers['if-none-match'] === entry.etag) { res.writeHead(304, {etag: entry.etag, age: String(age)}); return res.end(); }
    res.writeHead(200, {'content-type': TYPES[path.extname(url.pathname)] || 'application/octet-stream', 'cache-control': 'max-age=' + ttl,
      etag: entry.etag, age: String(age), 'x-cache': hit ? 'HIT' : 'MISS', 'strict-transport-security': 'max-age=0'});
    res.end(entry.body);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'https://localhost:' + server.address().port;
  const agent = new https.Agent({ca: tls.ca, keepAlive: true});
  const get = rel => new Promise((resolve, reject) => {
    https.get(base + '/' + rel.replace(/^\//, ''), {agent, servername: 'localhost', lookup: (h, o, cb) => cb(null, o && o.all ? [{address: '127.0.0.1', family: 4}] : '127.0.0.1', 4)}, res => {
      const chunks = []; res.on('data', c => chunks.push(c));
      res.on('end', () => res.statusCode === 200 ? resolve(Buffer.concat(chunks).toString('utf8')) : reject(new Error('HTTP ' + res.statusCode)));
    }).on('error', reject);
  });
  return {base, ca: tls.ca, get, stats, advance: s => { clock += s; }, now: () => clock, purge: (keep = () => false) => { for (const k of [...cache.keys()]) if (!keep(k)) cache.delete(k); },
    close: async () => { agent.destroy(); await new Promise(r => server.close(r)); fs.rmSync(tmp, {recursive: true, force: true}); }};
}

module.exports = {start};

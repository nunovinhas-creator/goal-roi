#!/usr/bin/env node
// Gera os snapshots derivados do Goal-ROI Intelligence V6 a partir do relatório original.
//
//   node scripts/build_dashboard_snapshots.cjs [--report data/report.json] [--prospective data/prospective_report.json]
//        [--protocol scangoal/prospective_protocol.json] [--out build/dashboard-snapshots]
//
// Só lê. Nunca altera report.json, o registo prospetivo nem o protocolo. A derivação é a mesma do navegador
// (docs/v6/v6-core.js). A saída é determinística: a frescura é medida à hora de geração do relatório.
// Não está ligado a nenhum workflow: a ativação em produção exige um passo novo em collect.yml (fora do âmbito).
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const core = require('../docs/v6/v6-core.js');

function args(argv) {
  const out = {report: 'data/report.json', prospective: 'data/prospective_report.json',
    protocol: 'scangoal/prospective_protocol.json', out: 'build/dashboard-snapshots'};
  for (let i = 0; i < argv.length; i += 2) {
    const k = argv[i].replace(/^--/, '');
    if (!(k in out) || argv[i + 1] === undefined) throw new Error('argumento desconhecido: ' + argv[i]);
    out[k] = argv[i + 1];
  }
  return out;
}
const readJson = (file, optional) => {
  if (optional && !fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8'));
};
const sha = buf => crypto.createHash('sha256').update(buf).digest('hex');

function build(opts) {
  const reportBytes = fs.readFileSync(opts.report);
  const report = JSON.parse(reportBytes.toString('utf8'));
  const check = core.validateReport(report);
  if (!check.ok) throw new Error('relatório inválido: ' + check.problems.join('; '));
  const prospective = readJson(opts.prospective, true);
  const protocol = readJson(opts.protocol, true);
  const at = Date.parse(report.generated_at_utc);
  // Conta o que o filtro de privacidade retira (para auditoria) e confirma que nada passa depois dele.
  const redacted = core.privacyFindings(core.buildAll(report, prospective, protocol, at, {raw: true})).length;
  const files = core.buildAll(report, prospective, protocol, at);
  const leaks = core.privacyFindings(files);
  if (leaks.length) throw new Error('dados privados detetados: ' + leaks.slice(0, 5).join(', '));
  fs.mkdirSync(opts.out, {recursive: true});
  const manifest = {schema: core.MANIFEST_SCHEMA, report_generated_at_utc: report.generated_at_utc,
    report_sha256: sha(reportBytes), report_bytes: reportBytes.length,
    prospective_sha256: prospective ? sha(fs.readFileSync(opts.prospective)) : null, redacted_fields: redacted, files: {}};
  for (const name of core.SNAPSHOTS) {
    const body = Buffer.from(JSON.stringify(files[name]) + '\n', 'utf8');
    fs.writeFileSync(path.join(opts.out, core.FILE(name)), body);
    manifest.files[name] = {path: core.FILE(name), sha256: sha(body), bytes: body.length};
  }
  fs.writeFileSync(path.join(opts.out, 'dashboard-manifest.json'), JSON.stringify(manifest, null, 1) + '\n');
  return manifest;
}

if (require.main === module) {
  try {
    const m = build(args(process.argv.slice(2)));
    const total = Object.values(m.files).reduce((s, f) => s + f.bytes, 0);
    console.log(JSON.stringify({report_bytes: m.report_bytes, snapshots_bytes: total,
      files: Object.fromEntries(Object.entries(m.files).map(([k, f]) => [k, f.bytes]))}));
  } catch (e) {
    console.error(e.message);
    process.exit(2);
  }
}
module.exports = {build, args};

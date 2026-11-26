'use strict';
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const path = require('node:path');
const core = require(path.resolve(process.argv[2], 'docs/v6/v6-core.js'));
const hash = s => createHash('sha256').update(s).digest('hex');
const a = JSON.stringify({schema:core.SCHEMA,kind:'summary',data:{value:'old'}});
const b = a.replace('"old"', '"new"');
const m = {files:{summary:{sha256:hash(a),bytes:Buffer.byteLength(a)}}};
assert.equal(Buffer.byteLength(a),Buffer.byteLength(b));
assert.equal(core.checkSnapshot('summary',JSON.parse(b),m,{sha256:hash(b)}).ok,false);
assert.equal(core.checkSnapshot('summary',JSON.parse(b),m,{bytes:Buffer.byteLength(b)}).ok,true);
const seq = core.createSequencer();
const old = seq.begin(), next = seq.begin();
assert(seq.accept(next,'2026-11-26T10:55:00Z'));
assert(!seq.accept(old,'2026-11-26T10:54:00Z'));
assert(!seq.accept(seq.begin(),'2026-11-26T10:54:59Z'));
console.log(JSON.stringify({same_length_mutation_sha256_rejected:true,same_length_mutation_bytes_only_accepted:true,
  stale_responses_rejected:true},null,2));

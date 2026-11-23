// Replays the PR #75 simulator as of 7bdaa4f (verbatim run loop) on one FT correction. Run from the repository root.
const fs=require('fs');const src=require('node:child_process').execFileSync('git',['show','7bdaa4f:docs/dashboard.js'],{encoding:'utf8'});
const cls=src.slice(src.indexOf('class SimulationDecimal'),src.indexOf('function simulationEuros'));
const body=src.slice(src.indexOf('    let cash=start, reserved=zero'),src.indexOf("    const bank=cash.plus(reserved), profit"));
const fn=new Function('entries','on','start','mode','val','oddsMultiplier','fraction','SimulationDecimal',
 `const zero=SimulationDecimal.from(0), one=SimulationDecimal.from(1);${body} return {bank:cash.plus(reserved).toNumber(),n,skipped,curve:curve.map(c=>c.toNumber())};`);
const SD=new Function(cls+';return SimulationDecimal')();
const t=m=>Date.parse('2026-11-23T12:00:00Z')+m*60000;
const run=(rows)=>{const e=rows.map(p=>({...p,placed:t(p.d),settled:p.z==null?Infinity:t(p.z)})).sort((a,b)=>a.placed-b.placed);
 return fn(e,new Set(['A','B']),SD.from(100),'pct',SD.from(100),SD.from(1),SD.from(100).percent(),SD)};
const B={d:-40,z:-30,w:1,o:2,t:'B'};
console.log('report published at -25 (A won at -60):', JSON.stringify(run([{d:-90,z:-60,w:1,o:2,t:'A'},B])));
console.log('report after correction at -20 (A lost):', JSON.stringify(run([{d:-90,z:-20,w:-1,o:2,t:'A'},B])));

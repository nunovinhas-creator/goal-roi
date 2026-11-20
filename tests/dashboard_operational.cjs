// Execute the actual dashboard functions without a browser, fetch, or services.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('docs/index.html','utf8');
const js=fs.readFileSync('docs/dashboard.js','utf8');
const extract=(start,end)=>js.slice(js.indexOf(start),js.indexOf(end,js.indexOf(start)));
const source=extract('function verdict(', '\nconst ICON=')+'\n'+extract('function answer(', '\nfunction fresh(');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
function render(d){
  const nodes={};
  const ctx={NAMES:{OPEN_GAME:['Jogo aberto','']},Date,
    $:id=>nodes[id]??=( {textContent:'',appendChild(){}} ),
    el:()=>({append(){}}),sign:String,document:{createTextNode:s=>s}};
  vm.createContext(ctx);vm.runInContext(source,ctx);ctx.answer(d);
  return [nodes.answer.textContent,nodes.answer2.textContent];
}
const baseline=render(payload);
const wins={signals:1000,hits:1000,misses:0,hit_rate:1,ci95:[.99,1],break_even:.5};
const contaminated={...payload,accuracy:{OPEN_GAME:wins},insights:{narrative:['melhor evidência'],regime:[{state:'melhorou'}]}};
assert.deepEqual(render(contaminated),baseline);
assert.match(baseline[1],/Apenas evidência elegível/);
// Sensitivity: the same statistics may affect the verdict when genuinely eligible.
assert.notDeepEqual(render({...payload,operational:{scope:'eligible_bets_only',accuracy:{OPEN_GAME:wins}}}),baseline);
for(const operational of [undefined,{}, {scope:'all_signals',accuracy:{OPEN_GAME:wins}}]){
  assert.match(render({...contaminated,operational})[0],/Evidência elegível ainda indisponível/);
}
assert.match(html, /Leitura exploratória dos dados/);
assert.match(html, /Inclui sinais inelegíveis/);
assert.match(js, /Exploratório: /);
console.log('Dashboard operational regressions passed');

const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const el=(tag,attrs={},text)=>{const e=document.createElement(tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e};
const sv=(tag,attrs={})=>{const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);return e};
const pts=v=>(v>0?'+':v<0?'−':'')+Math.abs(v*100).toFixed(0), edgeText=h=>h.edge_ci?`${pts(h.edge)} pontos (entre ${pts(h.edge_ci[0])} e ${pts(h.edge_ci[1])})`:'—';
const pct=(v,d=0)=>(v*100).toFixed(d).replace('.',',')+' %', num=v=>v==null?'—':String(v).replace('.',','), cls=v=>v>0?'pos':v<0?'neg':'';
const sign=v=>(v>0?'+':'')+num(v), hitsOf=(h,n)=>`${h} ${h===1?'acerto':'acertos'} em ${n}`;
const NAMES={'BETFAIR/pre':['Dinheiro na Betfair, antes do jogo','Aposta no resultado (1X2) que está a receber volume fora do normal'],
 'BETFAIR/live':['Dinheiro na Betfair, durante o jogo','Igual, mas com o jogo a decorrer'],
 'OPEN_GAME':['Jogo aberto, com odd','Jogo com ritmo alto: aposta em mais golos'],
 'OPEN_GAME_NO_ODDS':['Jogo aberto, sem odd','Acerta se houver pelo menos mais um golo'],
 'POLYMARKET':['Dinheiro na Polymarket','Segue transações de valor elevado num mercado de previsões'],
 'STAKE_LOAD':['Aposta grande na Stake','Segue uma aposta de valor elevado. A odd indicada pode já não existir quando o aviso chega'],
 'TOTALS_FORM':['Forma de golos a 100 %','As duas equipas costumam passar esta linha de golos'],
 'TOTALS_FORM_NO_ODDS':['Forma de golos, sem odd','Acerta se houver pelo menos mais um golo'],
 'GOAL_BREWING':['Golo a caminho','O bot diz que a probabilidade de golo nos próximos minutos está acima do habitual. Acerta se o golo cair nessa janela'],
 'WEAKENED_LINEUP':['Alinhamento enfraquecido','Aposta contra a equipa que entra sem os titulares habituais, ao handicap indicado']};
const GROUPS={'BETFAIR/pre/mkt>=10K':'Betfair antes do jogo, mercado grande','BETFAIR/pre/mkt<10K':'Betfair antes do jogo, mercado pequeno',
 'BETFAIR/live/mkt>=10K':'Betfair durante o jogo, mercado grande','BETFAIR/live/mkt<10K':'Betfair durante o jogo, mercado pequeno',
 'POLYMARKET/pre':'Polymarket antes do jogo','POLYMARKET/live':'Polymarket durante o jogo','STAKE_LOAD/pre':'Stake antes do jogo','STAKE_LOAD/live':'Stake durante o jogo','OPEN_GAME':'Jogo aberto','TOTALS_FORM':'Forma de golos','WEAKENED_LINEUP':'Alinhamento enfraquecido'};
const gname=g=>GROUPS[g]||g;

function verdict(g){
  const n=g.hits+g.misses;
  if(!n)return['none','Sem resultados'];
  if(g.break_even==null)return n<30?['none','Poucos dados']:['none','Sem odd para comparar'];
  if(n<30)return['none','Poucos dados'];
  if (!Array.isArray(g.ci95)) return ['none','Incerteza indisponível'];
  const [lo,hi]=g.ci95;
  if(lo>g.break_even)return['good','Acerto acima da referência'];
  if(hi<g.break_even)return['bad','Acerto abaixo da referência'];
  return['wait','Acerto estatisticamente incerto'];
}
const ICON={good:'▲',bad:'▼',wait:'◆',none:'○',ok:'✓'};
function chip(kind,text){const c=el('span',{class:'chip '+kind});c.append(el('span',{'aria-hidden':'true'},ICON[kind]),document.createTextNode(text));return c}

function plot(g){
  const W=1000,H=44,y=20,x=v=>v*W, s=sv('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none',role:'img'});
  const t=sv('title');t.textContent=g.hit_rate==null?'Sem resultados':`Taxa de acerto ${pct(g.hit_rate)}`+(g.ci95?` · intervalo de ${pct(g.ci95[0])} a ${pct(g.ci95[1])}`:'')+(g.break_even!=null?`; mínimo necessário ${pct(g.break_even)}`:'');s.appendChild(t);
  s.appendChild(sv('rect',{x:0,y:y-3,width:W,height:6,rx:3,fill:'var(--track)'}));
  s.appendChild(sv('rect',{x:x(.5)-.5,y:y-8,width:1,height:16,fill:'var(--line)'}));
  if(g.hit_rate!=null && g.ci95){
    const [lo,hi]=g.ci95;
    s.appendChild(sv('rect',{x:x(lo),y:y-7,width:Math.max(6,x(hi)-x(lo)),height:14,rx:7,fill:'var(--band)'}));
  }
  const wrap=el('div',{class:'plot'});wrap.appendChild(s);
  const pin=(v,css)=>{const d=el('span');d.style.cssText=`position:absolute;top:${y}px;left:${v*100}%;transform:translate(-50%,-50%);${css}`;wrap.appendChild(d)};
  if(g.break_even!=null)pin(g.break_even,'width:3px;height:26px;background:var(--ink);border-radius:2px');
  if(g.hit_rate!=null)pin(g.hit_rate,'width:14px;height:14px;border-radius:50%;background:var(--mark);box-shadow:0 0 0 2px var(--card)');
  const cap=el('div',{class:'cap'});cap.append(el('span',{},'0 %'),el('span',{},'50 %'),el('span',{},'100 %'));wrap.appendChild(cap);
  return wrap;
}

function pending(g){
  const gone=g.no_source||0, odd=g.unsettleable||0, wait=g.signals-g.with_result-gone-odd, out=[];
  if(wait>0)out.push(`${wait} à espera de resultado`);
  if(gone)out.push(`${gone} sem resultado disponível nas fontes`);
  if(odd)out.push(`${odd} em mercados que o resultado final não decide`);
  return out.length?'. '+out.join('; '):'';
}

function signals(d){
  const box=$('signals');
  const order=Object.entries(d.accuracy||{}).sort((a,b)=>(b[1].hits+b[1].misses)-(a[1].hits+a[1].misses));
  for(const [key,g] of order){
    const [title,desc]=NAMES[key]||[key,''], n=g.hits+g.misses, row=el('div',{class:'row'});
    const a=el('div');a.append(el('h3',{},title),el('div',{class:'cnt'},desc));
    const b=el('div',{class:'plotcell'});b.appendChild(plot(g));
    const r=el('div',{class:'read'});
    if(n){r.append(el('b',{},hitsOf(g.hits,n)),document.createTextNode(` (${pct(g.hit_rate)})`+(g.break_even!=null?(g.baseline?`, o habitual seria ${pct(g.break_even)}`:`, precisa de ${pct(g.break_even)}`):'')+(g.prob_above!=null?`. Probabilidade de estar acima ${g.baseline?'do habitual':'do mínimo'}: ${pct(g.prob_above)}`:'')+pending(g)))}
    else r.textContent=`${g.signals} ${g.signals===1?'sinal':'sinais'}, ainda sem resultado`+pending(g);
    if(n&&n<d.min_settled&&g.per_day){const e=el('div',{class:'eta'});e.textContent=`Ritmo: ${num(g.per_day)} resultados por dia. `+(n<30&&g.days_to_hint!=null?`Primeiro indício em cerca de ${g.days_to_hint} ${g.days_to_hint===1?'dia':'dias'}; `:'')+`conclusão em cerca de ${g.days_to_conclusion} dias.`;r.appendChild(e)}
    b.appendChild(r);
    const [kind,text]=verdict(g);
    row.append(a,b,chip(kind,text));box.appendChild(row);
  }
  if(!order.length)box.appendChild(el('div',{class:'row'},'Ainda sem sinais.'));
}

function answer(d){
  const operational=d.operational?.scope==='eligible_bets_only'?d.operational:null;
  const acc=Object.entries(operational?.accuracy||{}).map(([k,g])=>({k,g,n:g.hits+g.misses,v:verdict(g)}));
  const firm=acc.filter(x=>x.n>=d.min_settled&&x.v[0]!=='none'), hint=acc.filter(x=>x.n>=30&&x.n<d.min_settled&&(x.v[0]==='good'||x.v[0]==='bad'));
  const lead=acc.slice().sort((a,b)=>b.n-a.n)[0];
  let main,sub;
  if(firm.length){const good=firm.filter(x=>x.v[0]==='good').map(x=>(NAMES[x.k] || [x.k])[0]);
    main=good.length?`Acerto histórico acima da referência de preço em ${good.join(', ')}.`:'O acerto histórico dos grupos observados não superou a referência de preço.';
    sub='A referência é a média do inverso das odds anunciadas.'}
  else if(hint.length){main='Ainda não há conclusões, mas já há indícios: '+hint.map(x=>`${(NAMES[x.k] || [x.k])[0].toLowerCase()} (${x.v[0]==='good'?'positivo':'negativo'})`).join('; ')+'.';
    sub=`A comparação histórica usa um limiar descritivo de ${d.min_settled} apostas liquidadas por tipo.`}
  else{main='Ainda é cedo para dizer se os sinais dão lucro.';
    sub=lead&&lead.n?`O tipo com mais resultados é “${NAMES[lead.k]?NAMES[lead.k][0]:lead.k}”, com ${lead.n}. São precisos pelo menos 30 para um primeiro indício e ${d.min_settled} para uma conclusão.`:'Os primeiros resultados aparecem assim que os jogos terminarem.'}
  if(!operational){main='Evidência elegível ainda indisponível.';sub='Aguarda um relatório com evidência elegível; as estatísticas exploratórias não sustentam este veredito.'}
  else{sub='Apenas evidência elegível. '+sub+' Resultados históricos não demonstram capacidade preditiva.'}
  $('answer').textContent=main;$('answer2').textContent=sub;
  const days=Number.isFinite(Date.parse(d.first_signal_utc)) ? Math.max(1,Math.ceil((Date.now()-Date.parse(d.first_signal_utc))/864e5)) : null;
  const tl=d.timeline||[], bal=tl.length?tl[tl.length-1].u:null;
  [[d.messages,'mensagens lidas'],[d.unique_bets,'apostas seguidas'],[d.settled_bets,'com resultado'],[bal == null ? '—' : sign(bal)+' un.','saldo'],[days,days===1?'dia de dados':'dias de dados']].forEach(([v,l])=>{
    const s=el('span');s.append(el('b',{},String(v ?? '—')),document.createTextNode(' '+l));$('facts').appendChild(s)});
}

function fresh(d, failed) {
  const last = d?.audit?.collector_last_run_utc || d?.audit?.last_collected_at_utc;
  const timestamp = Date.parse(last), generated = Date.parse(d?.generated_at_utc), now = Date.now();
  const unavailable = !Number.isFinite(timestamp);
  const invalid = timestamp > now || generated > now + 5000;
  const oldReport = Number.isFinite(generated) && now - generated > 45 * 60000;
  const stopped = !unavailable && now - timestamp > 45 * 60000;
  const when = unavailable ? '' : lisbonDate.format(timestamp);
  const warning = $('collection-warning');
  warning.hidden = !stopped || (currentView !== 'overview' && currentView !== 'sinais');
  $('sinais').toggleAttribute('data-stale', stopped || oldReport || invalid);
  if (stopped) {
    const time = $('collection-time');
    time.dateTime = last;
    if (time.textContent !== when) time.textContent = when;
  }
  const label = failed ? (unavailable ? 'Falha ao atualizar' : `Falha ao atualizar · dados de ${when}`) :
    unavailable ? 'Recolha sem data' : invalid ? 'Data de recolha ou de relatório no futuro' :
    stopped ? `Recolha parada desde ${when}` : oldReport ? 'Relatório desatualizado' : `Atualizado em ${when}`;
  const box = $('fresh');
  if (box.lastElementChild.textContent !== label) box.lastElementChild.textContent = label;
  box.classList.toggle('stale', !!failed || unavailable || stopped || oldReport || invalid);
}

function chart(d){
  const box=$('chart'), tl=d.timeline||[], slider=$('chart-position'), tip=$('tip');
  const wasAtEnd = slider.value === slider.max || !slider.max;
  box.replaceChildren(tip);
  tip.style.display='none';
  $('chart-controls').hidden=tl.length<2;
  if(tl.length<2){box.appendChild(el('p',{class:'note'},'O gráfico aparece quando houver pelo menos duas apostas com resultado.'));return}
  const offset = Math.max(0, (d.settled_bets || 0) - tl.length);
  const previous = tl[0].u - tl[0].g;
  const start = Number.isFinite(previous) ? Number(previous.toFixed(2)) : tl[0].u;
  const W=720,H=255,L=51,R=17,T=14,B=32, vals=[start,...tl.map(p=>p.u)], mn=Math.min(0,...vals), mx=Math.max(0,...vals), pad=(mx-mn||1)*.12;
  const y=v=>T+(mx+pad-v)/((mx+pad)-(mn-pad))*(H-T-B), x=i=>L+i/(vals.length-1)*(W-L-R);
  const s=sv('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':`Saldo acumulado em unidades ao longo de ${tl.length} apostas liquidadas; valor final ${sign(tl[tl.length-1].u)}`});
  const step=[1,2,5,10,20,50,100].find(k=>(mx-mn+2*pad)/k<=5)||200;
  for(let g=Math.ceil((mn-pad)/step)*step;g<=mx+pad;g+=step){
    s.appendChild(sv('line',{x1:L,x2:W-R,y1:y(g),y2:y(g),stroke:g===0?'var(--mut)':'var(--line)','stroke-width':1}));
    const t=sv('text',{x:L-8,y:y(g)+4,'text-anchor':'end','font-size':18,fill:'var(--mut)'});t.textContent=sign(g);s.appendChild(t)}
  const tx=sv('text',{x:W-R,y:H-8,'text-anchor':'end','font-size':18,fill:'var(--mut)'});tx.textContent=offset ? `Últimas ${tl.length} apostas liquidadas` : `${tl.length} apostas liquidadas`;s.appendChild(tx);
  s.appendChild(sv('polyline',{points:vals.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' '),fill:'none',stroke:'var(--mark)','stroke-width':3,'stroke-linejoin':'round','stroke-linecap':'round'}));
  const last=vals.length-1;s.appendChild(sv('circle',{cx:x(last),cy:y(vals[last]),r:5,fill:'var(--mark)',stroke:'var(--card)','stroke-width':2}));
  const cross=sv('line',{y1:T,y2:H-B,stroke:'var(--mut)','stroke-width':1,visibility:'hidden'}), dot=sv('circle',{r:5,fill:'var(--mark)',stroke:'var(--card)','stroke-width':2,visibility:'hidden'});
  s.append(cross,dot);box.appendChild(s);
  const display = i => {
    const p=tl[i-1];
    $('chart-point').textContent=`Aposta ${offset+i} · ${p.m || 'Jogo'} · ${p.s} @ ${num(p.o)} · saldo ${sign(p.u)} un.`;
    slider.setAttribute('aria-valuetext', `Aposta ${offset+i} de ${offset+tl.length}, saldo ${sign(p.u)} unidades`);
  };
  const highlight = i => {
    const p=tl[i-1], r=s.getBoundingClientRect();
    cross.setAttribute('x1',x(i));cross.setAttribute('x2',x(i));cross.setAttribute('visibility','visible');
    dot.setAttribute('cx',x(i));dot.setAttribute('cy',y(p.u));dot.setAttribute('visibility','visible');
    tip.style.display='block';tip.style.left=Math.max(110,Math.min(r.width-110,x(i)/W*r.width))+'px';const ty=y(p.u)/H*r.height;tip.style.top=ty+'px';tip.style.transform=ty<70?'translate(-50%,18px)':'translate(-50%,-115%)';
    tip.textContent='';tip.append(el('b',{},`${p.m||'Jogo'}`),el('br'),document.createTextNode(`${p.s} @ ${num(p.o)}: ${sign(p.g)} un. Saldo ${sign(p.u)}`))};
  const move=ev=>{
    const r=s.getBoundingClientRect(), px=(ev.touches?ev.touches[0].clientX:ev.clientX)-r.left;
    const i=Math.round((px/r.width*W-L)/(W-L-R)*tl.length);
    highlight(Math.max(1,Math.min(tl.length,i)));
  };
  slider.max=tl.length;
  if (wasAtEnd) slider.value=tl.length;
  display(Number(slider.value));
  slider.oninput=()=>{display(Number(slider.value));highlight(Number(slider.value))};
  const out=()=>{tip.style.display='none';cross.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden')};
  s.addEventListener('mousemove',move);s.addEventListener('touchstart',move,{passive:true});s.addEventListener('touchmove',move,{passive:true});s.addEventListener('mouseleave',out);s.addEventListener('touchend',out);slider.onblur=out;
}

function insights(d, panel){
  const I=d.insights || {};
  if (panel === 'exploracao') {
  for(const t of I.narrative || [])$('says').appendChild(el('li',{},'Exploratório: '+t));
    if (!I.narrative?.length) $('says').append(el('li', {}, 'Sem observações exploratórias.'));
    return;
  }
  if (panel === 'faixas-odd') {
  const mv=(title,m,note)=>{const p=el('div',{class:'panel'});p.appendChild(el('h3',{},title));
    if(!m?.n){p.appendChild(el('p',{},'Ainda sem apostas com reforço.'))}
    else{p.appendChild(el('div',{class:'big'},`${m.shortened} de ${m.n}`));p.appendChild(el('p',{},`apostas em que a odd desceu depois do sinal. Subiu em ${m.drifted}. Variação típica: ${m.median_move_pct>0?'+':''}${num(m.median_move_pct)} %.`));
      const a=m.when_shortened,b=m.when_drifted;p.appendChild(el('p',{},`Quando desceu: ${hitsOf(a.hits,a.n)}. Quando subiu: ${hitsOf(b.hits,b.n)}.`))}
    p.appendChild(el('p',{},note));$('move').appendChild(p)};
  mv('Antes do jogo',I.price_move?.pre,'Aqui a odd só mexe por dinheiro e notícias, por isso é o indicador mais limpo.');
  mv('Durante o jogo',I.price_move?.live,'Aqui a odd também mexe com o relógio e os golos; serve só de contexto.');
  for(const c of I.calibration || []){const row=el('div',{class:'row'}), a=el('div');a.append(el('h3',{},c.band.replace('odd','Odd')),el('div',{class:'cnt'},`${c.signals} sinais`));
    const b=el('div',{class:'plotcell'});b.appendChild(plot(c));const r=el('div',{class:'read'});
    if(c.n)r.append(el('b',{},hitsOf(c.hits,c.n)),document.createTextNode(` (${pct(c.hit_rate)}), a odd exige ${pct(c.break_even)}`));else r.textContent='Ainda sem resultados';
    b.appendChild(r);const v=verdict({hits:c.hits,misses:c.misses,break_even:c.break_even,ci95:c.ci95},d.min_settled);row.append(a,b,chip(v[0],v[1]));$('calib').appendChild(row)}
  if (!I.calibration?.length) $('calib').append(el('p', {class:'empty'}, 'Sem faixas de odd disponíveis.'));
  return;
  }
  if (panel === 'comportamento') {
  for(const c of I.bot_confidence||[]){const tr=el('tr');td(tr,c.band,'name');td(tr,c.n);td(tr,c.stated!=null?pct(c.stated):'—','mut');td(tr,c.hit_rate!=null?pct(c.hit_rate):'—');td(tr,c.break_even!=null?pct(c.break_even):'—','mut');$('conf').appendChild(tr)}
  const ng=I.next_goal;if(ng&&ng.n)$('nextgoal').textContent=`Depois de um sinal de jogo aberto, o golo seguinte demorou tipicamente ${ng.median_min} minutos (${ng.n} ${ng.n===1?'caso':'casos'} em que a lista de golos do bot bate certo com o resultado).`;
  for(const r of I.regime||[]){const tr=el('tr');td(tr,(NAMES[r.type]||[r.type])[0],'name');
    if(r.before){td(tr,r.n);td(tr,pct(r.before.hit_rate));td(tr,pct(r.recent.hit_rate));td(tr,pct(r.before.roi,1),cls(r.before.roi));td(tr,pct(r.recent.roi,1),cls(r.recent.roi));td(tr,chip(r.state==='piorou'?'bad':r.state==='melhorou'?'good':'wait',r.state[0].toUpperCase()+r.state.slice(1)))}
    else{td(tr,`${r.n} de ${r.needed}`);for(let i=0;i<4;i++)td(tr,'—','mut');td(tr,chip('none','Poucos dados'))}
    $('regime').appendChild(tr)}
  emptyTable('conf', 5, 'Sem resultados por faixa de confiança.');
  emptyTable('regime', 7, 'Sem comparações disponíveis.');
  return;
  }
  if (panel === 'segmentos') {
  $('segnote').textContent=I.method ?`Cada linha é um corte dos sinais com pelo menos ${I.method.min_segment} resultados, ordenado da melhor para a pior evidência. Foram comparados ${I.segments_tested} cortes: quantos mais se comparam, mais provável é algum parecer bom por acaso. Servem para escolher o que confirmar em dados novos.` : 'Critérios de segmentação indisponíveis.';
  for(const g of I.segments || []){const tr=el('tr');td(tr,g.family,'l');td(tr,`${g.dimension}: ${g.value}`,'name');td(tr,g.n);td(tr,pct(g.hit_rate));td(tr,g.break_even!=null?pct(g.break_even):'—','mut');
    td(tr,g.prob_above!=null?pct(g.prob_above):'—',g.prob_above==null?'mut':g.prob_above>=.9?'pos':g.prob_above<=.1?'neg':'');td(tr,g.roi!=null?pct(g.roi,1):'—',g.roi==null?'mut':cls(g.roi));$('segs').appendChild(tr)}
  emptyTable('segs', 7, I.method ? `Nenhum corte tem ${I.method.min_segment} resultados.` : 'Sem segmentos disponíveis.');
  return;
  }
  const L=I.luck, card=(title,big,text)=>{const p=el('div',{class:'panel'});p.append(el('h3',{},title),el('div',{class:'big'},big),el('p',{},text));$('risk').appendChild(p)};
  if(L?.n){
    card('Saldo histórico',`${sign(L.units)} un.`,`Referência exploratória para ${L.n} apostas independentes a odds justas: oscilação de cerca de ±${num(Math.round(2*L.luck_sd*10)/10)} unidades. Apostas no mesmo jogo podem estar correlacionadas.`);
    card('Maior queda',`−${num(L.max_drawdown)} un.`,'A maior descida do saldo desde um máximo. Mostra a dimensão da maior queda histórica.');
    card('Pior sequência',`${L.longest_losing_run} seguidas`,'O maior número de apostas perdidas de seguida até agora.')}
  else card('Sorte e risco','—','Aparece quando houver apostas com resultado.');
}

const LISBON = {timeZone:'Europe/Lisbon'};
const lisbonTime = new Intl.DateTimeFormat('pt-PT', {...LISBON, hour:'2-digit', minute:'2-digit'});
const lisbonDate = new Intl.DateTimeFormat('pt-PT', {...LISBON, day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit'});
const formatCount = new Intl.NumberFormat('pt-PT');
const count = value => Number.isFinite(value) ? formatCount.format(value) : '—';
const AGV = {
  'favorável':['wait','Indício favorável'],
  'misto':['wait','Indícios mistos'],
  'sem evidência':['none','Sem evidência'],
  'contraditório':['bad','Sinais contraditórios'],
  'evitar':['bad','Indício desfavorável']
};
const validStates = new Set(['forte', 'confirmada']);
const validationActive = v => v?.state === 'active' && v.method === 'eprocess_online_alpha_v1' &&
  v.ledger_valid === true && Array.isArray(v.certified);

// The report generator verifies the ledger history. For display, require the same
// per-hypothesis proof fields as the existing Telegram presentation.
function certificates(brain) {
  const v = brain?.validation;
  if (!validationActive(v)) return new Set();
  const top = new Map((brain.top || []).map(h => [h.id, h]));
  return new Set(v.certified.filter(id => {
    if (typeof id !== 'string') return false;
    const row = Object.hasOwn(brain.presentation?.hypotheses || {}, id) ?
      brain.presentation.hypotheses[id] : top.get(id);
    const proof = row?.confirmation;
    return proof?.certifiable === true && Number.isInteger(proof.rank) && proof.rank > 0 &&
      typeof proof.certified_utc === 'string' && Number.isFinite(Date.parse(proof.certified_utc));
  }));
}

function scienceState(brain, certified) {
  const v = brain?.validation;
  if (validationActive(v)) return ['Ativa', certified.size ?
    `${count(certified.size)} ${certified.size === 1 ? 'tática certificada' : 'táticas certificadas'}` : 'Sem táticas certificadas'];
  if (v?.state === 'not_activated') return ['Inativa', 'Sem táticas certificadas'];
  if (v) return ['Não verificada', 'Sem certificação verificável'];
  return ['Indisponível', 'Estado ausente no relatório'];
}

function scientificStatus(status, id, certified) {
  return validStates.has(status) && !certified.has(id) ? 'promissora' : status;
}

function historicSummary(d) {
  // Q7 has the same population as the two historical counts. The graph can be a smaller window.
  const g = d.questions?.[REFERENCE];
  return Number.isFinite(d.unique_bets) && Number.isFinite(d.settled_bets) &&
    g?.bets === d.unique_bets && g.settled === d.settled_bets ? g : null;
}

function overview(d, certified) {
  const g = historicSummary(d), normal = g?.roi?.['odd-0%'];
  const hasRoi = Number.isFinite(normal?.roi) && g.settled > 0;
  $('metric-tracked').textContent = count(d.unique_bets);
  $('metric-settled').textContent = count(d.settled_bets);
  $('metric-roi').textContent = hasRoi ? pct(normal.roi, 1) : '—';
  $('metric-roi-note').textContent = hasRoi ? `Odd anunciada · N = ${count(g.settled)}` : 'ROI indisponível';
  const [state, note] = scienceState(d.brain, certified);
  $('metric-science').textContent = state;
  $('metric-science-note').textContent = note;
  $('overview-conclusion').textContent = certified.size ?
    'Há certificação prospetiva para as táticas identificadas no relatório.' :
    'Rentabilidade prospetiva por demonstrar.';
  $('method-cert').textContent = `Certificação científica · ${state.toLowerCase()}. ${note}.`;
}

function agendaStill(a, now, liveMinutes) {
  if (a.when === 'por começar') return Date.parse(a.kickoff_utc) > now;
  const age = now - Date.parse(a.date_utc);
  return a.when === 'a decorrer' && age >= 0 && age <= liveMinutes * 60000;
}

function agendaStatus(a, certified) {
  return a.verdict === 'favorável' && !a.conflict && a.validated === true &&
    (a.good || []).some(h => validStates.has(h.status) && certified.has(h.id)) ? 'certificado' : (a.verdict || 'sem evidência');
}

const tname = h => (h.conditions || []).map(([key,value]) => `${key.toLowerCase()} = ${value}`).join(' e ');
const odd = v => v == null ? '—' : v.toFixed(2).replace('.',',');
const tline = h => `${hitsOf(h.hits,h.n)}` +
  (Number.isFinite(h.games) ? ` · ${count(h.games)} jogos independentes` : '') +
  ` · ROI histórico ${h.roi == null ? '—' : pct(h.roi)}` +
  (h.prob_above == null ? '' : ` · ${pct(h.prob_above)} de probabilidade de superar o mínimo`);
const openSignals = new Set();
let agendaSnapshot = null, agendaItems = [], agendaSignature = '';

function signalDetails(a, byMatch, rules, key) {
  const details = el('details'), body = el('div');
  details.open = openSignals.has(key);
  details.append(el('summary', {}, 'Ver análise'), body);
  details.firstElementChild.setAttribute('aria-label', `Ver análise · ${a.match} · ${a.selection}`);
  details.addEventListener('toggle', () => {
    if (!details.isConnected) return;
    if (details.open) openSignals.add(key);
    else openSignals.delete(key);
  });
  // Hypothesis text is constructed once the reader opens the card.
  const draw = () => {
    if (body.children.length) return;
    if (a.league) body.append(el('p', {class:'why'}, a.league));
    if (a.verdict === 'contraditório') {
      const selections = byMatch.filter(x => x.market === a.market).map(x => `${x.selection} @ ${odd(x.odds)}`);
      body.append(el('p', {class:'warn'}, selections.length > 1 ?
        `Seleções diferentes no mesmo mercado: ${selections.join(' e ')}.` : 'O relatório registou seleções diferentes no mesmo mercado.'));
    }
    for (const [label, hypotheses, total] of [['A favor', a.good, a.good_total], ['Contra', a.bad, a.bad_total]]) {
      const first = hypotheses?.[0];
      if (!first) continue;
      const p = el('p', {class:'why'});
      p.append(el('b', {}, label + ' · '), document.createTextNode(`${tname(first)} (${tline(first)}).`));
      if (first.source === 'llm') p.append(document.createTextNode(' Proposta do modelo de linguagem.'));
      if (total > 1) p.append(document.createTextNode(` Existem mais ${total - 1} correspondências, que podem partilhar jogos.`));
      body.append(p);
    }
    if (!a.good?.length && !a.bad?.length) body.append(el('p', {class:'why'}, 'Nenhuma tática correspondente com evidência suficiente.'));
    if (Number.isFinite(a.odds_band_n) && a.odds_band_n < rules.min_results)
      body.append(el('p', {class:'warn'}, `Amostra nesta faixa (${a.odds_band}): ${a.odds_band_n}. As táticas foram medidas sobretudo noutras odds.`));
    if (a.odds_last && a.odds_last !== a.odds)
      body.append(el('p', {class:'warn'}, `Odd de ${odd(a.odds)} para ${odd(a.odds_last)}${a.max_mult > 1 ? ' no reforço ×' + a.max_mult : ''}.`));
    else if (a.max_mult > 1) body.append(el('p', {class:'warn'}, `Reforço até ×${a.max_mult}.`));
    if (a.share != null && a.share_last != null && a.share_last !== a.share)
      body.append(el('p', {class:'warn'}, `Quota de mercado de ${a.share} % para ${a.share_last} %.`));
  };
  if (details.open) draw();
  details.addEventListener('toggle', () => { if (details.open) draw(); });
  return details;
}

function signalOptions(id, keys, labels) {
  const select = $(id), previous = select.value;
  const options = [...new Set(keys.filter(Boolean))].sort((a,b) => a.localeCompare(b, 'pt-PT'));
  if (previous && !options.includes(previous)) options.push(previous);
  select.replaceChildren(select.options[0], ...options.map(value =>
    el('option', {value}, (labels[value] || value) + (!keys.includes(value) ? ' · indisponível' : ''))));
  select.value = previous;
}

function agendaUI(d, certified, showList) {
  const agenda = d.brain?.agenda;
  const rules = agenda?.rules || {};
  const now = Date.now();
  const source = agenda?.items || [];
  const liveMinutes = rules.live_max_min || 30;
  const items = source.filter(a => agendaStill(a, now, a.live_max_min || liveMinutes));
  const hasAgenda = Array.isArray(agenda?.items);
  const summary = hasAgenda ? (items.length ? `${count(items.length)} ${items.length === 1 ? 'sinal na janela de observação' : 'sinais na janela de observação'}` :
    'Sem sinais na janela de observação') : 'Agenda indisponível';
  $('overview-signals').textContent = summary;
  if (!showList) return;

  if (agendaSnapshot !== d) {
    signalOptions('signal-source', source.map(a => a.family), {});
    signalOptions('signal-status', source.map(a => agendaStatus(a, certified)),
      {...Object.fromEntries(Object.entries(AGV).map(([key, value]) => [key, value[1]])), certificado:'Tática certificada'});
    const validKeys = new Set(source.map(signalKey));
    for (const key of openSignals) if (!validKeys.has(key)) openSignals.delete(key);
    $('agendafresh').textContent = Number.isFinite(Date.parse(agenda?.generated_utc)) ?
      `Agenda de ${lisbonDate.format(new Date(agenda.generated_utc))}` : '';
    $('agendanote').textContent = rules.min_results != null ?
      `O relatório cruza sinais com odd com táticas com pelo menos ${rules.min_results} jogos independentes. Validade até ao início do jogo ou até ${liveMinutes} minutos após o aviso ao vivo. A odd pode mudar.` :
      'A agenda mostra sinais disponíveis no relatório.';
  }
  const phase = document.querySelector('[name="signal-phase"]:checked').value;
  const origin = $('signal-source').value, state = $('signal-status').value;
  const signature = JSON.stringify([phase, origin, state]);
  if (agendaSnapshot === d && signature === agendaSignature && items.length === agendaItems.length &&
      items.every((item, i) => item === agendaItems[i])) return;
  agendaSnapshot = d;
  agendaItems = items;
  agendaSignature = signature;
  const visible = items.filter(a => (!phase || a.when === phase) &&
    (!origin || a.family === origin) && (!state || agendaStatus(a, certified) === state));
  $('signal-count').textContent = visible.length === items.length ? summary :
    `${visible.length} de ${items.length} sinais na janela de observação`;
  const list = $('agendalist');
  const focused = document.activeElement?.closest('[data-signal-key]')?.dataset.signalKey;
  const byMatch = new Map();
  for (const a of items) {
    if (!byMatch.has(a.match_id)) byMatch.set(a.match_id, []);
    byMatch.get(a.match_id).push(a);
  }
  const cards = visible.map(a => {
    const key = signalKey(a), status = agendaStatus(a, certified);
    const [kind, label] = status === 'certificado' ? ['ok','Tática certificada'] : (AGV[status] || ['none',status]);
    const pre = a.when === 'por começar', kickoff = Date.parse(a.kickoff_utc);
    const expiry = pre ? kickoff : Date.parse(a.date_utc) + (a.live_max_min || liveMinutes) * 60000;
    const meta = el('div', {class:'signal-meta'});
    const clock = Number.isFinite(kickoff) ? el('time', {datetime:a.kickoff_utc},
      `Jogo ${pre ? lisbonDate.format(kickoff) : lisbonTime.format(kickoff)}`) :
      el('time', {datetime:a.date_utc}, `Sinal ${lisbonTime.format(new Date(a.date_utc))}`);
    const marker = pre ? 'Pré-jogo' : `Ao vivo${a.minute != null ? ' · min. ' + a.minute : ''}`;
    meta.append(el('strong', {}, marker), clock);
    const price = el('p', {class:'signal-price'}, `${a.selection} @ ${odd(a.odds)}`);
    price.append(el('span', {}, ` · ${a.market} · ${a.family}`));
    const foot = el('div', {class:'signal-foot'});
    const staleLabel = chip('wait', 'Dados desatualizados');
    staleLabel.classList.add('signal-stale');
    foot.append(chip(kind, label), el('span', {class:'signal-validity'}, `Até ${lisbonTime.format(expiry)}${pre ? ' · início' : ''}`), staleLabel);
    const card = el('article', {class:'signal-card', 'data-signal-key':key});
    card.append(meta, el('h2', {}, a.match), price, foot, signalDetails(a, byMatch.get(a.match_id), rules, key));
    return card;
  });
  if (!cards.length) cards.push(el('p', {class:'empty'}, !hasAgenda ? 'Este relatório não disponibiliza a agenda.' :
    items.length ? 'Nenhum sinal corresponde aos filtros escolhidos.' : 'Sem sinais na janela de observação. A agenda atualiza com a recolha.'));
  list.replaceChildren(...cards);
  if (focused) {
    const match = cards.find(card => card.dataset.signalKey === focused);
    (match?.querySelector('summary') || $('signal-status')).focus({preventScroll:true});
  }
  const counts = new Map();
  for (const a of items) {
    const status = agendaStatus(a, certified);
    counts.set(status, (counts.get(status) || 0) + 1);
  }
  $('agendacounts').replaceChildren(...[...counts].map(([status,n]) => {
    const [kind, label] = status === 'certificado' ? ['ok','Tática certificada'] : (AGV[status] || ['none',status]);
    return chip(kind,`${label} · ${n}`);
  }));
}

function signalKey(a) {
  return JSON.stringify([a.match_id, a.market, a.selection, a.type, a.date_utc]);
}

function bars(target, rows, diverging = false) {
  const box = $(target);
  if (!rows.length) {
    box.replaceChildren(el('p', {class:'note'}, 'Dados indisponíveis neste relatório.'));
    return;
  }
  const max = Math.max(0, ...rows.map(row => Math.abs(row.value ?? 0)));
  const list = el('ul', {class:'chart-list', role:'list'});
  for (const {label,value,text,style} of rows) {
    const row = el('li'), title = el('span', {class:'bar-label'});
    title.append(el('span', {}, label), el('b', {}, text));
    row.append(title);
    if (Number.isFinite(value)) {
      const track = el('span', {class:'bar-track' + (diverging ? ' diverging' : ''), 'aria-hidden':'true'});
      const fill = el('span', {class:'bar-fill' + (style ? ' ' + style : diverging && value < 0 ? ' loss' : '')});
      const width = max ? Math.abs(value) / max * (diverging ? 50 : 100) : 0;
      fill.style.width = width + '%';
      fill.style.left = (diverging ? (value < 0 ? 50 - width : 50) : 0) + '%';
      track.append(fill);
      row.append(track);
    }
    list.append(row);
  }
  box.replaceChildren(list);
}

function resultsSummary(d) {
  const g = historicSummary(d), tl = d.timeline || [], normal = g?.roi?.['odd-0%'];
  $('balance-total').textContent = Number.isFinite(g?.units) && g.settled > 0 ? `${sign(g.units)} un.` :
    tl.length ? `${sign(tl.at(-1).u)} un.` : '—';
  $('balance-note').textContent = normal?.roi != null ?
    `ROI histórico ${pct(normal.roi,1)} · ${count(g.settled)} liquidadas${normal.ci95 ? ` · IC 95% de ${pct(normal.ci95[0],1)} a ${pct(normal.ci95[1],1)}` : ''}.` :
    'ROI histórico indisponível.';
  const pending = g && g.bets - g.settled;
  const outcomes = g && Number.isFinite(g.wins) && Number.isFinite(g.pushes) ?
    [['Ganhas',g.wins], ['Perdidas',g.settled-g.wins-g.pushes,'loss'], ['Devolvidas',g.pushes,'secondary'],
      ['Por liquidar',pending,'secondary']] : [];
  bars('results-outcomes', outcomes.map(([label,value,style]) => ({label,value,text:count(value),style})));
  const groups = Object.entries(d.groups || {}).map(([key,item]) => {
    const value = item.roi?.['odd-0%']?.roi;
    return {label:`${gname(key)} · N=${count(item.settled)}`, value, text:value == null ? '—' : pct(value,1)};
  });
  bars('results-groups',groups,true);
  bars('results-odds',g?.roi ? [['odd-0%','Odd anunciada'],['odd-2%','Odd −2%'],['odd-5%','Odd −5%']].map(([key,label]) => {
    const value = g.roi[key]?.roi;
    return {label,value,text:value == null ? '—' : pct(value,1)};
  }) : [],true);
}

function laboratorySummary(d, certified) {
  const B = d.brain, counts = B?.counts;
  const entries = [];
  if (counts) {
    const verified = {forte:0, confirmada:0};
    for (const id of certified) {
      const status = B.status_list?.[id] || B.top?.find(h => h.id === id)?.status;
      if (validStates.has(status)) verified[status]++;
    }
    const unverified = Math.max(0, (counts.forte || 0) + (counts.confirmada || 0) - verified.forte - verified.confirmada);
    for (const [label,value] of [['Em observação',counts['em observação'] || 0],['Promissoras',(counts.promissora || 0) + unverified],
      ['Fortes',verified.forte],['Confirmadas',verified.confirmada],['Abandonadas',counts.abandonada || 0]]) {
      const item = el('span',{},label);
      item.prepend(el('b',{},count(value)));
      // Read the count and its label with an explicit separator.
      item.insertBefore(document.createTextNode(' '), item.lastChild);
      entries.push(item);
    }
  }
  $('braincounts').replaceChildren(...entries);
  const multi = B?.multiplicity, eligibility = B?.eligibility;
  const context = [];
  if (eligibility?.eligible != null) context.push(`${count(eligibility.eligible)} sinais elegíveis de ${count(eligibility.total)}.`);
  if (multi?.promising_union_games != null) context.push(`Na amostra das hipóteses promissoras ou acima há ${count(multi.promising_union_games)} jogos distintos.`);
  if (!counts) context.push('Estados das hipóteses indisponíveis.');
  $('lab-overlap').textContent = context.join(' ');
  const study = B?.walk_forward?.followed;
  $('lab-study').textContent = study ? `${count(study.settled)} apostas seguidas com resultado · ${study.roi == null ? 'ROI indisponível' : 'ROI ' + pct(study.roi,1)} na reconstituição cronológica.` : 'Estudo indisponível.';
  $('lab-llm').textContent = Number.isFinite(B?.llm_total) ?
    `${count(B.llm_total)} propostas registadas.` + (Number.isFinite(B.track?.llm_with_prospective_evidence) ? ` ${count(B.track.llm_with_prospective_evidence)} com amostra prospetiva mínima.` : '') : 'Propostas indisponíveis.';
}

function prospectiveUI(d) {
  const track = d.brain?.track, evaluation = d.brain?.llm_evaluation;
  const bits = ['Observações depois do registo. As propostas podem partilhar jogos.'];
  if (evaluation?.verdict) bits.push(`Avaliação formal da LLM · ${evaluation.verdict}.`);
  if (track?.min_results != null) bits.push(`Amostra mínima de ${track.min_results} jogos independentes para figurar no acompanhamento.`);
  $('prospective-note').textContent = bits.join(' ');
  for (const hypothesis of track?.llm || []) {
    const row = el('tr'), e = hypothesis.economics;
    td(row,hypothesis.id,'name');
    for (const v of [hypothesis.games,e?.results_confirmed,e?.imputed_losses,e?.pending]) td(row,v ?? '—');
    for (const v of [e?.roi_advertised,e?.roi_odds_minus_2pct]) td(row,v == null ? '—' : pct(v,1),v == null ? 'mut' : cls(v));
    $('prospective').append(row);
  }
  emptyTable('prospective', 7, 'Sem propostas com dados prospetivos disponíveis.');
}

function boardUI(d, certified){
  const B=d.brain?.board;
  if (!B) { $('boardnote').textContent='Ranking indisponível neste relatório.'; return; }
  const LV={'indício inicial':'none','promissora':'wait','forte':'good','confirmada':'good','abandonada':'bad'};
  const name=h=>h.conditions.length?`${h.family} · ${h.conditions.map(x=>`${x[0]}: ${x[1]}`).join(' e ')}`:`${h.family} · todos os sinais deste tipo`;
  const item=(h,bad)=>{const li=el('li');const level=scientificStatus(h.level,h.id,certified)||'indício inicial';
    li.append(el('b',{},name(h)),chip(bad?'bad':LV[level]||'none',bad?'A evitar':level[0].toUpperCase()+level.slice(1)));
    li.appendChild(el('span',{class:'why'},`${hitsOf(h.hits,h.n)}; a odd exige ${pct(h.break_even)}. ROI ${pct(h.roi,1)}.`+(h.edge_ci?` Vantagem estimada: ${edgeText(h)}; com 2 % de perda de odd, ${pct(h.prob_above_cut)} de probabilidade de continuar acima do necessário.`:'')+(h.prob_adds!=null?` Probabilidade de render mais do que os restantes sinais do mesmo tipo: ${pct(h.prob_adds)}.`:'')+(h.missing?` Faltam ${h.missing} jogos independentes para deixar de ser só um indício.`:'')+(h.source==='llm'?' Proposta pelo modelo de linguagem.':'')));return li};
  $('boardnote').textContent=`As táticas com melhor evidência entre as ${d.brain.hypotheses} que o cérebro e o modelo de linguagem estão a testar, com pelo menos ${B.min_results} jogos independentes. Com tantas em teste, algumas parecem boas só por acaso: abaixo de ${B.hint_at} jogos são indícios, não conclusões. A evidência e os limiares contam um voto por jogo: vários sinais ou mercados no mesmo jogo não a fazem crescer.`;
  for(const h of B.best || [])$('best').appendChild(item(h,false));
  if(!B.best?.length)$('best').append(el('li',{},'Sem táticas com evidência suficiente para o ranking.'));
  for(const h of B.avoid || [])$('avoid').appendChild(item(h,true));
  if(!B.avoid?.length){const li=el('li');li.textContent='Nada a assinalar por agora.';$('avoid').appendChild(li)}
  const o=$('boardopen');
  for(const x of B.open || []){const p=el('p');p.append(el('b',{},x.match||'Jogo'),document.createTextNode(` · ${x.selection||'mais um golo'}${x.odds?' @ '+num(x.odds):''}`),el('span',{class:'why'},`Encaixa em: ${(x.tactic?.split(' | ')[1] || x.tactic || '—').replaceAll(' = ',': ').replaceAll(' & ',' e ')}. ${x.kickoff_utc&&new Date(x.kickoff_utc)>new Date()?'Jogo a '+new Date(x.kickoff_utc).toLocaleString('pt-PT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})+'. ':''}Sinal das ${new Date(x.date_utc).toLocaleTimeString('pt-PT',{hour:'2-digit',minute:'2-digit'})}.`));o.appendChild(p)}
  if(!B.open?.length)o.appendChild(el('p',{class:'note'},'Nenhum sinal em aberto encaixa nas táticas acima neste momento.'));
  const n=(d.brain.notes||[])[0];
  $('boardllm').textContent=n?`Última nota do modelo de linguagem, sobre "${n.id.replace(' | ',': ')}": ${n.note}`:'';
}

function strategyUI(d){
  const S=d.brain?.strategy_board;
  if (!S || S.schema !== 'goal_roi_strategy_board_v1') { $('strategy-verdict').textContent='Quadro de estratégias indisponível neste relatório.'; return; }
  $('strategy-verdict').append(el('b',{},S.verdict || '—'));
  const m=S.multiplicity||{};
  $('strategy-note').textContent=`Critérios ${S.criteria?.version||'—'} fixados antes dos resultados; só contam jogos posteriores ao registo, uma unidade por jogo, odd ×0,98. Correção de Bonferroni sobre ${count(m.candidates_tested||0)} candidatas (rejeitadas incluídas). Sem prova de odds executáveis nem de custos: nenhuma estratégia pode ser validada.`;
  for (const [key,label] of [['em_observacao','Em observação'],['evidencia_insuficiente','Evidência insuficiente'],['rejeitada','Rejeitadas'],['validada','Validadas']]) {
    const li=el('li'); li.append(el('b',{},count(S.counts?.[key]||0)),document.createTextNode(' '+label)); $('strategy-counts').append(li);
  }
  const labels=S.reason_labels||{};
  const line=c=>{const li=el('li');li.append(el('b',{},c.id||'—'),chip(c.state==='validada'?'good':c.state==='rejeitada'?'bad':'wait',c.label||c.state||'—'));
    li.append(el('span',{class:'why'},`${count(c.confirmed||0)} resultados prospetivos comprovados`+(c.roi_odds_minus_2pct==null?'':`, ROI com odd ×0,98 ${pct(c.roi_odds_minus_2pct,1)}`)+(c.lower_corrected==null?'':`, limite inferior corrigido ${pct(c.lower_corrected,1)}`)+`. Falta: ${(c.reasons||[]).slice(0,3).map(r=>labels[r]||r).join('; ')||'—'}.`));return li};
  for (const c of S.shortlist?.entries||[]) $('strategy-shortlist').append(line(c));
  emptyList('strategy-shortlist','Lista curta indisponível.');
  for (const c of (S.insufficient||[]).slice(0,5)) $('strategy-insufficient').append(line(c));
  emptyList('strategy-insufficient','Nenhuma candidata com evidência suficiente para leitura.');
  for (const [code,n] of Object.entries(S.reasons||{}).filter(([c])=>c!=='avoid_hypothesis').sort((a,b)=>b[1]-a[1]).slice(0,6)) $('strategy-reasons').append(el('li',{},`${labels[code]||code}: ${count(n)}`));
}
function emptyList(id,text){if(!$(id).children.length)$(id).append(el('li',{},text))}

let sortCol=5, sortDir=-1, showAll=false;
function brainUI(d, certified, panel){
  const B=d.brain || {}, rules=B.rules || {};
  const ST={'em observação':'none','promissora':'wait','forte':'ok','confirmada':'ok','abandonada':'bad'};
  const statusChip = h => {
    const status=scientificStatus(h.status,h.id,certified);
    return chip(ST[status] || 'none', status ? status[0].toUpperCase()+status.slice(1) : 'Indisponível');
  };
  if (panel === 'hipoteses') {
  const V=B.validation;

  const notes=[];
  if (rules.min_support != null) notes.push(`Uma combinação torna-se hipótese com pelo menos ${rules.min_support} sinais. Das ${count(B.hypotheses)} hipóteses registadas, ${count(B.tested)} já têm pelo menos ${rules.hint_at} jogos independentes. Cerca de ${num(B.expected_by_chance)} pareceriam promissoras apenas por acaso.`);
  if (validationActive(V)) {
    notes.push('Certificação prospetiva com teste sequencial e odd 2% inferior, com uma aposta por jogo e jogos admitidos sem sobreposição.');
    if (V.min_games != null) notes.push(`São necessários pelo menos ${V.min_games} jogos após o registo.`);
    if (V.alpha != null) notes.push(`O nível de erro de ${pct(V.alpha)} é repartido pelas táticas registadas.`);
    notes.push(`Registadas ${count(V.selected)} · certificadas ${certified.size} · anteriores à ativação ${count(V.legacy)}.`);
  }
  else notes.push('Os estados forte e confirmada exigem um certificado verificável. Promissora é um indício exploratório.');
  if (B.multiplicity) notes.push(`As ${B.multiplicity.promising_or_better} hipóteses promissoras ou acima somam ${B.multiplicity.promising_sum_games} jogos, dos quais ${B.multiplicity.promising_union_games} são distintos.`);
  if (rules.confirm_steps) notes.push(`Uma mudança de estado requer ${rules.confirm_steps} jogos seguidos. Uma hipótese promissora volta a observação abaixo de ${pct(rules.keep_promising)}.`);
  $('brainnote').textContent=notes.join(' ');
  const RANK={'confirmada':5,'forte':4,'promissora':3,'em observação':2,'abandonada':1};
  const COLS=[null,null,h=>h.games,h=>h.hit_rate,h=>h.break_even,h=>h.prob_above,h=>h.edge,h=>h.roi,h=>RANK[scientificStatus(h.status,h.id,certified)]];
  const ths=[...$('hyps').parentElement.querySelectorAll('thead th')];
  const drawH=()=>{const body=$('hyps');body.textContent='';
    const rows=(B.top || []).slice().sort((x,y)=>{const a=COLS[sortCol](x),b=COLS[sortCol](y);return ((a==null)-(b==null))||sortDir*((a>b)-(a<b))||(y.n-x.n)});
    for(const h of rows.slice(0,showAll?1e9:25)){const tr=el('tr');td(tr,h.family,'l');
      td(tr,h.conditions.map(x=>`${x[0]}: ${x[1]}`).join(' e ')+(h.expect==='abaixo'?' (a evitar)':'')+(h.tradable?'':' (informativa: só se sabe depois do sinal)'),'l');
      td(tr,h.games!=null&&h.games!==h.n?`${h.games} (${h.n} sinais)`:(h.games??h.n));td(tr,pct(h.hit_rate));td(tr,h.break_even!=null?pct(h.break_even):'—','mut');td(tr,h.prob_above!=null?pct(h.prob_above):'—',h.prob_above==null?'mut':h.prob_above>=.9?'pos':h.prob_above<=.1?'neg':'');td(tr,edgeText(h),h.edge_ci?(h.edge_ci[0]>0?'pos':h.edge_ci[1]<0?'neg':''):'mut');
      td(tr,h.roi!=null?pct(h.roi,1):'—',h.roi==null?'mut':cls(h.roi));const cell=td(tr,statusChip(h));
      const next=scientificStatus(h.pending_status,h.id,certified);
      if(next && next!==scientificStatus(h.status,h.id,certified)) cell.append(document.createTextNode(` · a confirmar: ${next}`));
      else if(h.entry_status && h.entry_status!==h.status) cell.append(document.createTextNode(' · estado mantido pela regra de estabilidade'));body.appendChild(tr)}
    ths.forEach((th,i)=>{if(COLS[i])th.setAttribute('aria-sort',i===sortCol?(sortDir<0?'descending':'ascending'):'none')});
    $('hypsmore').hidden=showAll||rows.length<=25;$('hypsmore').textContent=`Mostrar as ${rows.length} hipóteses`};
  ths.forEach((th,i)=>{if(!COLS[i])return;let btn=th.querySelector('button');
    if(!btn){btn=el('button',{type:'button',class:'sort'},th.textContent);th.replaceChildren(btn)}
    btn.onclick=()=>{if(sortCol===i)sortDir=-sortDir;else{sortCol=i;sortDir=-1}drawH()}});
  $('hypsmore').onclick=()=>{showAll=true;drawH()};
  drawH();
  emptyTable('hyps', 9, 'Nenhuma hipótese com resultados suficientes no relatório.');
  return;
  }
  if (panel === 'estudo-chronologico') {
  const WF=B.walk_forward;
  if(WF){$('wfnote').textContent=`A reconstituição decide cada sinal só com os resultados conhecidos naquele momento${WF.rule.known_after_min!=null?` (um resultado conta ${WF.rule.known_after_min} minutos depois do início do jogo)`:`, pela hora a que o sistema efetivamente leu o sinal e viu cada resultado, e só com as táticas já registadas nessa altura. Sinais lidos tarde demais para apostar (${(WF.not_reconstructible||{}).bets||0}) não contam como decisões`}. Segue-se o sinal quando encaixa numa tática apostável que, nessa altura, já tinha ${WF.rule.min_results} jogos independentes e ${pct(WF.rule.min_prob)} de probabilidade de estar acima do necessário. Se o cérebro acrescenta alguma coisa, os sinais seguidos têm de render mais do que os outros.`+(WF.prob_followed_better!=null?` Probabilidade de os seguidos terem mais vantagem do que os não seguidos: ${pct(WF.prob_followed_better)}.`:'')+(WF.followed.settled<30?` Com ${WF.followed.settled} resultados seguidos, ainda é só um indício.`:'');
    for(const [name,g] of [['Seguidos pelo cérebro',WF.followed],['Não seguidos',WF.skipped]].concat(WF.not_reconstructible?[['Não reconstituíveis (lidos tarde)',WF.not_reconstructible]]:[])){const tr=el('tr');td(tr,name,'l');td(tr,g.bets);td(tr,g.settled);td(tr,g.wins);td(tr,g.settled?num(g.units)+' un.':'—',g.settled?cls(g.units):'mut');td(tr,g.roi!=null?pct(g.roi,1):'—',g.roi==null?'mut':cls(g.roi));td(tr,g.roi_cut!=null?pct(g.roi_cut,1):'—',g.roi_cut==null?'mut':cls(g.roi_cut));$('wf').appendChild(tr)}}
  emptyTable('wf', 7, 'Estudo cronológico indisponível.');
  return;
  }
  if (panel === 'propostas') {
  const T=B.llm_today||{}, TR=B.track||{};
  $('llmnote').textContent=B.llm_runs?`De hora a hora, um modelo de linguagem lê os números e alterna entre propor hipóteses novas, refinar as melhores, apontar situações a evitar e escrever notas. Como já viu resultados, cada proposta só conta os sinais que chegarem depois dela. Propostas aceites até agora: ${B.llm_total||0}. Hoje: ${T.calls_today||0} ${T.calls_today===1?'chamada':'chamadas'}, cerca de ${num(Math.round(T.neurons_today||0))} de 10 000 neurónios gratuitos.`+(TR.llm_with_prospective_evidence?` ${TR.llm_with_prospective_evidence} ${TR.llm_with_prospective_evidence===1?'proposta tem':'propostas têm'} ${TR.min_results} ou mais jogos independentes depois do registo; o modelo recebe esses números só como descrição.`:` Ainda nenhuma proposta tem ${TR.min_results||10} jogos independentes depois do registo.`)+` Nenhuma proposta é dada como confirmada por acertar mais de metade: a avaliação científica segue o protocolo pré-registado (audits/pr4-llm-protocol-v5.md) e ${(B.llm_evaluation||{}).verdict==='não verificada'?'não é feita nesta página (precisa do histórico git completo)':'está no relatório da auditoria'}.`:'Este relatório não regista execuções do modelo de linguagem.';
  for(const h of B.llm||[]){const tr=el('tr');td(tr,h.family,'l');const c=td(tr,'','l');c.append(el('b',{},h.conditions.map(x=>`${x[0]}: ${x[1]}`).join(' e ')+(h.expect==='abaixo'?' (a evitar)':'')));if(h.reason)c.append(el('br'),document.createTextNode(h.reason));
    td(tr,new Date(h.born_utc).toLocaleDateString('pt-PT',{day:'2-digit',month:'2-digit'}));td(tr,h.n);td(tr,h.hit_rate!=null?pct(h.hit_rate):'—');td(tr,statusChip(h));$('llm').appendChild(tr)}
  if(!(B.llm||[]).length){const tr=el('tr');td(tr,'Ainda sem propostas.','mut').colSpan=6;$('llm').appendChild(tr)}
  return;
  }
  if (panel === 'notas') {
  for(const n of B.notes||[]){const p=el('p');p.append(el('b',{},n.id.replace(' | ',': ')),document.createTextNode(` (${hitsOf(n.hits,n.n)}, precisa de ${n.break_even!=null?pct(n.break_even):'—'}). ${n.note}`));$('notes').appendChild(p)}
  if(!(B.notes||[]).length)$('notes').appendChild(el('p',{class:'note'},'Sem notas do investigador neste relatório.'));
  return;
  }
  if (panel === 'entradas') {
  for(const e of B.entry_points||[]){if(!e.signals)continue;const tr=el('tr');td(tr,e.phase==='pre'?'Antes do jogo':'Durante o jogo','l');td(tr,e.level===1?'Ao primeiro sinal':`Ao reforço ×${e.level}`,'l');td(tr,e.n);
    td(tr,e.hit_rate!=null?pct(e.hit_rate):'—');td(tr,e.break_even!=null?pct(e.break_even):'—','mut');td(tr,e.prob_above!=null?pct(e.prob_above):'—',e.prob_above==null?'mut':e.prob_above>=.9?'pos':e.prob_above<=.1?'neg':'');td(tr,e.roi!=null?pct(e.roi,1):'—',e.roi==null?'mut':cls(e.roi));$('entry').appendChild(tr)}
  emptyTable('entry', 7, 'Sem dados de entradas e reforços.');
  return;
  }
  const w=$('watch');
  const goodN=['promissora','forte','confirmada'].reduce((n,k)=>n+((B.counts||{})[k]||0),0);
  if(!B.watch?.length)w.appendChild(el('p',{class:'note'},!Array.isArray(B.watch)?'Lista indisponível no relatório.':goodN?'Neste momento nenhum sinal ainda apostável encaixa numa hipótese promissora, forte ou confirmada (ao vivo: até 30 minutos depois do sinal; antes do jogo: até ao início).':`Ainda nenhuma hipótese chegou a promissora (são precisos ${(rules.hint_at ?? 'mais')} jogos independentes). A lista aparece aqui quando isso acontecer.`));
  for(const x of B.watch || []){const r=x.reasons?.[0];if(!r)continue;const p=el('p');p.append(el('b',{},x.match||'Jogo'),document.createTextNode(` · ${x.selection||'mais um golo'}${x.odds?' @ '+num(x.odds):''}. Encaixa em ${x.reasons.length} ${x.reasons.length===1?'hipótese':'hipóteses'}; a melhor: ${r.id.split(' | ')[1]} (${pct(r.hit_rate)} em ${r.n}, ${scientificStatus(r.status,r.id,certified)}).`));w.appendChild(p)}
}

function unsettledUI(d){
  const U=d.unsettled||[], a=d.audit||{};
  $('unsnote').textContent=U.length?`${U.length} ${U.length===1?'jogo terminou':'jogos terminaram'} há mais de 2 horas e ${U.length===1?'continua':'continuam'} sem resultado. As fontes são consultadas de 90 em 90 minutos. Quando um jogo não existe em nenhuma fonte, podes procurar o resultado e dizê-lo: entra como resultado manual${a.manual_results?` (já há ${a.manual_results})`:''}.`:Array.isArray(d.unsettled) ? 'Sem jogos listados por liquidar há mais de 2 horas.' : 'Dados por liquidar indisponíveis.';
  for(const u of U){const tr=el('tr');td(tr,u.match||u.match_id,'l');td(tr,u.league||'—','l');td(tr,new Date(u.date_utc).toLocaleString('pt-PT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}),'mut');td(tr,u.why[0].toUpperCase()+u.why.slice(1),'l');
    const c=td(tr,''),day=new Date(u.kickoff_utc||u.date_utc).toLocaleDateString('pt-PT');
    c.appendChild(el('a',{href:'https://www.google.com/search?q='+encodeURIComponent(`${u.match.replace(' — ',' vs ')} ${day} resultado`),target:'_blank',rel:'noopener'},'Procurar'));$('uns').appendChild(tr)}
  if(!U.length){const tr=el('tr');td(tr,'Nada por liquidar.','mut').colSpan=5;$('uns').appendChild(tr)}
}

// Retain exact decimal values through reservations and payouts. Round only for
// display, with exact halfway amounts rounded away from zero (decimal half-up).
class SimulationDecimal {
  constructor(coefficient, scale=0){
    this.coefficient=coefficient;
    this.scale=coefficient===0n?0:scale;
  }
  static from(value){
    if(!Number.isFinite(Number(value)) || Number(value)===0)return new SimulationDecimal(0n);
    const [, integer, fraction='', exponent='0']=String(value).trim().match(/^([+-]?\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    const scale=fraction.length-Number(exponent);
    const coefficient=BigInt(integer+fraction);
    return scale<0?new SimulationDecimal(coefficient*10n**BigInt(-scale)):new SimulationDecimal(coefficient,scale);
  }
  atScale(scale){return this.coefficient*10n**BigInt(scale-this.scale)}
  plus(other){
    const scale=Math.max(this.scale,other.scale);
    return new SimulationDecimal(this.atScale(scale)+other.atScale(scale),scale);
  }
  minus(other){return this.plus(new SimulationDecimal(-other.coefficient,other.scale))}
  times(other){return new SimulationDecimal(this.coefficient*other.coefficient,this.scale+other.scale)}
  percent(){return new SimulationDecimal(this.coefficient,this.scale+2)}
  min(other){return this.minus(other).coefficient<0n?this:other}
  max(other){return this.minus(other).coefficient>0n?this:other}
  toNumber(){return Number(`${this.coefficient}e-${this.scale}`)}
  rounded(places){
    if(this.scale<=places)return this.atScale(places);
    const divisor=10n**BigInt(this.scale-places);
    const negative=this.coefficient<0n;
    const magnitude=negative?-this.coefficient:this.coefficient;
    const rounded=magnitude/divisor+(magnitude%divisor*2n>=divisor?1n:0n);
    return negative?-rounded:rounded;
  }
}
function simulationEuros(value){
  const cents=value.rounded(2), magnitude=cents<0n?-cents:cents;
  const euros=(magnitude/100n).toLocaleString('pt-PT');
  return `${cents<0n?'-':''}${euros},${String(magnitude%100n).padStart(2,'0')} €`;
}

const simOff=new Set();
// Payoff versions of one position: [[instant, payoff|null], ...]. Without `s`, the single proven settlement (z, w).
function settlementEvents(p){
  const rows=Array.isArray(p.s)?p.s:(p.z&&p.w!==null&&p.w!==undefined?[[p.z,p.w]]:[]);
  return rows.map(([at,w])=>({at:Date.parse(at),w:w===null||w===undefined?null:w}));
}
// Chronological replay of the historical positions. Uses only what each instant knew:
// a stake is reserved from placement until a payoff is proven; a later correction moves the
// available balance by the payoff difference; a result back in quarantine reserves the stake again.
// `until` cuts the replay at an instant (normally the report cut): nothing after it is used.
function simulatePortfolio(rows, {start, mode, val, cut, on}, until=Infinity){
  const zero=SimulationDecimal.from(0), one=SimulationDecimal.from(1);
  start=SimulationDecimal.from(start).max(one);
  const value=SimulationDecimal.from(val).max(zero), fraction=value.percent();
  const oddsMultiplier=one.minus(SimulationDecimal.from(cut));
  const positions=rows.map((p,i)=>({p,i,placed:Date.parse(p.d),events:settlementEvents(p)}))
    .filter(x=>(!on||on.has(x.p.t))&&x.placed<=until);
  const queue=[];
  for(const x of positions){
    queue.push({at:x.placed,kind:1,i:x.i,k:0,x});
    x.events.forEach((e,k)=>{if(e.at>x.placed&&e.at<=until)queue.push({at:e.at,kind:0,i:x.i,k,x,w:e.w})});
  }
  // Settlements before placements at the same instant would use a payoff not yet visible to the decision: placements first.
  queue.sort((a,b)=>a.at-b.at||b.kind-a.kind||a.i-b.i||a.k-b.k);
  let cash=start, reserved=zero, peak=start, dd=zero, low=start, staked=zero, n=0, maxReserved=zero, skipped=0, revisions=0;
  const curve=[{at:null,balance:start}], stakes=new Map();
  const pay=(stake,w,o)=>{const weight=SimulationDecimal.from(w);
    return stake.times(one.plus(w>0?weight.times(SimulationDecimal.from(o).times(oddsMultiplier).minus(one)):weight))};
  const mark=at=>{const balance=cash.plus(reserved);peak=peak.max(balance);dd=dd.max(peak.minus(balance));low=low.min(balance);curve.push({at,balance})};
  for(const item of queue){
    const {x}=item;
    if(item.kind===1){
      const stake=cash.min(mode==='flat'?value:cash.times(fraction));
      if(stake.coefficient<=0n){skipped++;continue}
      cash=cash.minus(stake);reserved=reserved.plus(stake);staked=staked.plus(stake);n++;
      maxReserved=maxReserved.max(reserved);
      stakes.set(x.i,{stake,w:null});
      continue;
    }
    const held=stakes.get(x.i);
    if(!held||held.w===item.w)continue;
    if(held.w===null){reserved=reserved.minus(held.stake);cash=cash.plus(pay(held.stake,item.w,x.p.o))}
    else if(item.w===null){cash=cash.minus(pay(held.stake,held.w,x.p.o));reserved=reserved.plus(held.stake);maxReserved=maxReserved.max(reserved);revisions++}
    else{cash=cash.plus(pay(held.stake,item.w,x.p.o)).minus(pay(held.stake,held.w,x.p.o));revisions++}
    held.w=item.w;
    mark(item.at);
  }
  const realized=cash.plus(reserved);
  return {start,cash,reserved,realized,profit:realized.minus(start),peak,dd,low,staked,n,maxReserved,skipped,revisions,curve};
}
function simulator(d){
  const evidence=d.simulation, rows=evidence?.entries || d.timeline || [];
  const until=Date.parse(d.generated_at_utc);
  const types=[...new Set(rows.map(p=>p.t))], on=new Set(types.filter(t=>!simOff.has(t)));
  $('sim-evidence').textContent=evidence ?
    `${count(evidence.timed_settlements)} liquidações com hora comprovada em ${count(evidence.total_bets)} apostas. ${count(evidence.committed_games)} jogos identificados, com até ${count(evidence.max_units_per_game)} unidades por jogo. Preços executáveis e custos sem observação.` :
    'Série histórica sem horários de liquidação. O saldo dos resultados só fica disponível no fim da simulação.';
  const typeBox=$('simtypes'), focusedType=typeBox.contains(document.activeElement) ? document.activeElement.value : null;
  typeBox.replaceChildren();
  for(const t of types){const l=el('label'),c=el('input',{type:'checkbox',value:t});c.checked=on.has(t);c.addEventListener('change',()=>{c.checked?(on.add(t),simOff.delete(t)):(on.delete(t),simOff.add(t));run()});l.append(c,document.createTextNode((NAMES[t]||[t])[0]));typeBox.appendChild(l);if(t===focusedType)c.focus({preventScroll:true})}
  const run=()=>{
    const eur=simulationEuros, mode=$('simmode').value;
    $('simvallabel').textContent=mode==='flat'?'Valor por aposta (€)':'Percentagem da banca (%)';
    // The timeline fallback has no settlement instants: its results only count at the end (after every placement).
    const history=evidence?rows:rows.map(p=>({...p,z:p.w!==null&&p.w!==undefined?new Date(until+1).toISOString():null}));
    const r=simulatePortfolio(history,{start:$('simbank').value,mode,val:$('simval').value,cut:$('simcut').value,on},evidence?until:Infinity);
    const {start,peak,dd,low,staked,n,maxReserved,reserved,skipped}=r, val=SimulationDecimal.from($('simval').value).max(SimulationDecimal.from(0));
    const bank=r.realized, profit=r.profit, curve=r.curve.map(point=>point.balance);
    $('sim-exposure').textContent=`Saldo máximo reservado ${eur(maxReserved)} · em aberto no fim ${eur(reserved)} · ${count(skipped)} oportunidades sem saldo disponível.`+
      ` Saldo realizado ${eur(r.realized)} · capital reservado ${eur(reserved)} · saldo disponível ${eur(r.cash)}`+
      (r.revisions?` · ${count(r.revisions)} correções de resultado aplicadas na data em que foram conhecidas.`:'.');
    const out=$('simout');out.textContent='';
    [[n,'apostas simuladas'],[eur(bank),'banca final'],[(profit.coefficient>=0n?'+':'')+eur(profit),'lucro ou perda simulados'],[staked.coefficient?pct(profit.toNumber()/staked.toNumber(),1):'—','retorno sobre o apostado'],[eur(dd),'maior queda'],[eur(low),'banca mais baixa']]
      .forEach(([v,l])=>{const w=el('div');w.append(el('dt',{},l),el('dd',{},String(v)));out.appendChild(w)});
    const box=$('simchart');box.textContent='';
    if(curve.length<3){box.appendChild(el('p',{class:'note'},n?'São precisas pelo menos duas apostas para desenhar a curva.':val.coefficient===0n?'Indique um valor por aposta acima de zero.':'Nenhuma aposta liquidada nos tipos escolhidos.'));return}
    const W=720,H=200,L=64,R=14,T=12,B=22, range=peak.minus(low).toNumber(), pad=(range||1)*.1;
    const y=value=>T+(peak.minus(value).toNumber()+pad)/(range+2*pad)*(H-T-B), x=i=>L+i/(curve.length-1)*(W-L-R);
    const s=sv('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':`Banca simulada ao longo de ${n} apostas: de ${eur(start)} para ${eur(bank)}`});
    for(const g of [low,start,peak].filter((v,i,a)=>a.findIndex(other=>v.minus(other).coefficient===0n)===i)){s.appendChild(sv('line',{x1:L,x2:W-R,y1:y(g),y2:y(g),stroke:g.minus(start).coefficient===0n?'var(--mut)':'var(--line)','stroke-width':1}));
      const t=sv('text',{x:L-8,y:y(g)+4,'text-anchor':'end','font-size':17,fill:'var(--mut)'});t.textContent=g.rounded(0).toLocaleString('pt-PT');s.appendChild(t)}
    s.appendChild(sv('polyline',{points:curve.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' '),fill:'none',stroke:'var(--mark)','stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'}));
    box.appendChild(s)};
  for (const id of ['simbank','simval','simcut']) $(id).oninput=run;
  $('simmode').onchange=()=>{$('simval').value=$('simmode').value==='flat'?10:1;run()};
  run();
}

function td(tr,t,c){const e=el('td',c?{class:c}:{});if(t instanceof Node)e.appendChild(t);else e.textContent=t;tr.appendChild(e);return e}
function emptyTable(id, columns, text) {
  const body=$(id);
  if (body.children.length) return;
  const row=el('tr');
  td(row, text, 'mut').colSpan=columns;
  body.append(row);
}
function roi(tr,g,full){const r=g.roi;
  if(!r?.['odd-0%']){for(let i=0;i<(full?4:2);i++)td(tr,'—','mut');return}
  const a=r['odd-0%'];td(tr,pct(a.roi,1),cls(a.roi));td(tr,a.ci95?pct(a.ci95[0])+' e '+pct(a.ci95[1]):'—','mut');
  if(full)for(const key of ['odd-2%','odd-5%']){const v=r[key]?.roi;td(tr,v==null?'—':pct(v,1),v==null?'mut':cls(v))}}
function tables(d, panel){
  if (panel === 'dados-resultados') {
  $('minset').textContent=d.min_settled ?? '—';
  for(const [name,g] of Object.entries(d.groups || {})){const tr=el('tr');td(tr,gname(name),'name');
    const f=document.createDocumentFragment(), b=el('span',{class:'prog',title:`${g.settled} de ${d.min_settled}`}), i=el('i');i.style.width=Math.min(100,g.settled/d.min_settled*100)+'%';b.appendChild(i);
    f.append(document.createTextNode(`${g.settled} de ${g.bets}`));if(d.min_settled>0)f.append(b);td(tr,f);td(tr,g.wins);td(tr,num(g.avg_odds));td(tr,g.settled?sign(g.units):'—',g.settled?cls(g.units):'mut');roi(tr,g,true);$('groups').appendChild(tr)}
  for(const x of (d.daily||[]).slice().reverse()){const tr=el('tr');td(tr,new Date(x.day+'T12:00:00Z').toLocaleDateString('pt-PT',{weekday:'short',day:'2-digit',month:'2-digit'}));td(tr,x.bets);td(tr,x.settled);td(tr,x.wins);td(tr,x.settled?sign(x.units):'—',x.settled?cls(x.units):'mut');$('daily').appendChild(tr)}
  emptyTable('groups', 9, 'Sem resultados por grupo.');
  emptyTable('daily', 5, 'Sem resultados por dia.');
  return;
  }
  if (panel === 'segmentos') {
  for(const [name,g] of Object.entries(d.questions||{})){const tr=el('tr');td(tr,name.replace(/^Q\d+ /,''),'name');td(tr,`${g.settled} de ${g.bets}`);td(tr,g.wins);td(tr,num(g.avg_odds));roi(tr,g,false);$('questions').appendChild(tr)}
  emptyTable('questions', 6, 'Sem perguntas em teste.');
  return;
  }
  const a=d.audit||{};
  if(!d.audit || !Object.keys(a).length) {
    $('names').append(el('p', {class:'note'},'Controlo de qualidade indisponível neste relatório.'));
    return;
  }
  if((a.name_review||[]).length){const box=$('names');box.appendChild(el('p',{class:'note'},'Jogos em que o nome das equipas difere entre o bot e a fonte de resultados. Ficam em aberto até o nome ser confirmado à mão:'));
    const ul=el('ul',{class:'note'});for(const n of a.name_review)ul.appendChild(el('li',{},`${n.bot.join(' — ')} ↔ ${n.bsd_candidates.map(c=>c.join(' — ')).join(' ou ')} (${n.provider})`));box.appendChild(ul)}
  const gaps=a.ingestion_gaps?.count ? [[a.ingestion_gaps.count,'lacunas de ingestão com histórico Telegram por confirmar']] : [];
  [...gaps,[a.versions,'versões de mensagens guardadas'],[d.unparsed,'mensagens em formato desconhecido'],[a.edits_changing_signal,'sinais que o bot alterou depois de publicar'],
   [a.bot_mark_disagreements,'resultados em que a marca do bot difere das nossas contas'],[d.excluded_first_seen_with_result,'sinais lidos já com resultado (fora do saldo)'],
   [d.excluded_edited_before_seen,'sinais já editados quando foram lidos (fora do saldo)'],[d.excluded_burst,'sinais chegados em lote no mesmo segundo (fora do saldo)'],
   [a.deleted_by_bot,'mensagens que o bot apagou depois de publicar'],[a.ended_after_90,'jogos decididos depois dos 90 minutos (em aberto)'],
   [`${a.stake_sent_45_after_kickoff??0}`,'sinais Stake chegados mais de 45 min depois do início'],[`${a.stake_no_score_after_kickoff??0} de ${a.stake_original_seen??0}`,'sinais Stake originais sem marcador, já com o jogo a decorrer'],
   [`${a.goal_lists_consistent??0} de ${a.goal_lists??0}`,'listas de golos do bot que batem certo'],
   [`${(a.goal_minutes_matches??0)-(a.goal_minutes_inconsistent??0)} de ${a.goal_minutes_matches??0}`,'jogos com minutos dos golos confirmados pela Highlightly'],
   [a.manual_results??0,'resultados introduzidos à mão'],[a.manual_vs_other_disagreements??0,'resultados manuais diferentes dos de outra fonte'],
   [d.excluded_low_odds,'sinais com odd abaixo de 1,10 (ignorados)'],[a.hl_results,'resultados obtidos na Highlightly'],[a.hl_vs_bot_disagreements,'divergências entre a Highlightly e o bot'],
   [a.hl_near_misses,'jogos com nome de equipa por rever'],[a.bsd_results,'resultados obtidos na BSD']].forEach(([v,l])=>{const w=el('div');w.append(el('dt',{},l),el('dd',{},String(v??'—')));$('qc').appendChild(w)});
}

function outcome(b){const w=b.w;
  if(w==null)return b.ft?['none','Não liquidável']:['none','Em aberto'];
  if(w>0)return['good',w<1?'Meia ganha':'Ganha'];if(w<0)return['bad',w>-1?'Meia perdida':'Perdida'];return['wait','Devolvida']}
const KIND={BETFAIR:'Betfair',STAKE_LOAD:'Stake',POLYMARKET:'Polymarket',OPEN_GAME:'Jogo aberto',OPEN_GAME_NO_ODDS:'Jogo aberto, sem odd',TOTALS_FORM:'Forma de golos',TOTALS_FORM_NO_ODDS:'Forma de golos, sem odd',GOAL_BREWING:'Golo a caminho',WEAKENED_LINEUP:'Alinhamento enfraquecido',FULL_TIME_NOTE:'Fim do jogo'};
function story(m){
  const box=el('div',{class:'story'});
  if(!m){box.appendChild(el('p',{class:'note'},'Sem detalhe para este jogo.'));return box}
  const t=m.teams||['equipa da casa','equipa de fora'], money=v=>v>=1000?num(Math.round(v/100)/10)+' mil $':num(v)+' $';
  for(const e of m.events){const bits=[KIND[e.k]||e.k];
    if(e.k==='BETFAIR'){bits[0]+=e.phase==='pre'?', antes do jogo':', durante o jogo';bits.push(`${({1:t[0],2:t[1],X:'Empate'})[e.selection]||e.selection} @ ${num(e.odds)}`);if(e.mult>1)bits.push(`reforço ×${e.mult}`);if(e.share!=null)bits.push(`${e.share} % de ${money(e.mkt_volume)}`)}
    else if(e.k==='GOAL_BREWING'){bits.push(`golo nos próximos ${e.window} min: ${e.bot_prob} % (habitual ${e.usual_prob} %)`)}
    else if(e.selection){bits.push(`${e.market&&e.market!=='Asian Total'?e.market+': ':''}${e.selection}${e.odds?' @ '+num(e.odds):''}`);if(e.total)bits.push(money(e.total));if(e.rhythm!=null)bits.push(`ritmo ${e.rhythm}, confiança ${e.bot_over_pct} %`)}
    if(e.minute!=null)bits.push(`minuto ${e.minute}${e.score_at_signal?`, ${e.score_at_signal[0]}-${e.score_at_signal[1]}`:''}`);
    const p=el('p');p.append(el('span',{class:'when'},new Date(e.d).toLocaleTimeString('pt-PT',{hour:'2-digit',minute:'2-digit'})+' '),document.createTextNode(bits.join(' · ')));box.appendChild(p)}
  if(m.goals&&m.goals.length)box.appendChild(el('p',{},`Golos${m.goals_source==='bot'?' (lista do bot, pode estar incompleta)':''}: `+m.goals.map(g=>`${g[0]}' ${g[1]}`).join(', ')+'.'));
  if(m.ft)box.appendChild(el('p',{},`Resultado final: ${m.ft[0]}-${m.ft[1]}.`));
  return box;
}
let limit=12, recentF=0;
const openBets = new Set();
function recent(d){
  const all=d.recent_bets||[], box=$('recent'), F=[['todas','Todas',()=>true],['ganhas','Ganhas',b=>b.w>0],['perdidas','Perdidas',b=>b.w<0],['abertas','Em aberto',b=>b.w==null]];
  const keyOf=b=>JSON.stringify([b.match_id,b.date_utc,b.group,b.selection]);
  const currentKeys=new Set(all.map(keyOf));
  for(const key of openBets)if(!currentKeys.has(key))openBets.delete(key);
  const draw=fn=>{
    const focused=document.activeElement?.dataset.betKey, focusMore=box.contains(document.activeElement)&&document.activeElement.classList.contains('more');
    box.replaceChildren();
    const every=all.filter(fn), rows=every.slice(0,limit);
    if(!rows.length){box.appendChild(el('p',{class:'note'},'Nenhuma aposta neste filtro.'));return}
    for(const b of rows){
      const key=keyOf(b), open=openBets.has(key), r=el('button',{type:'button',class:'bet','aria-expanded':String(open),'data-bet-key':key});
      const t=new Date(b.date_utc);
      r.append(el('span',{class:'when'},lisbonDate.format(t)));
      const w=el('span',{class:'what'});w.append(el('b',{},`${b.selection}`),document.createTextNode(b.odds?` @ ${num(b.odds)}`:''));
      w.append(el('span',{class:'bet-context'},`${b.match}${b.ft?`, final ${b.ft[0]}-${b.ft[1]}`:''}. ${gname(b.group)}${b.max_mult>1?`, reforçado ×${b.max_mult}`:''}`));
      const [k,txt]=outcome(b);
      r.append(w,chip(k,txt));
      box.append(r);
      let detail=open?story((d.matches||{})[b.match_id]):null;
      if(detail)r.after(detail);
      r.onclick=()=>{
        if(detail){detail.remove();detail=null;openBets.delete(key)}
        else{detail=story((d.matches||{})[b.match_id]);r.after(detail);openBets.add(key)}
        r.setAttribute('aria-expanded',String(!!detail));
      };
      if(key===focused)r.focus({preventScroll:true});
    }
    if(every.length>rows.length){
      const more=el('button',{type:'button',class:'more'},`Mostrar as ${every.length} apostas`);
      more.onclick=()=>{const firstNew=rows.length;limit=Infinity;draw(fn);box.querySelectorAll('.bet')[firstNew]?.focus()};
      box.append(more);
      if(focusMore)more.focus({preventScroll:true});
    }
  };
  const filterBox=$('filters');
  F.forEach(([id,label,fn],i)=>{
    let btn=filterBox.children[i];
    if(!btn){btn=el('button',{type:'button'});filterBox.append(btn)}
    btn.textContent=`${label} (${all.filter(fn).length})`;
    btn.setAttribute('aria-pressed',String(i===recentF));
    btn.onclick=()=>{for(const item of filterBox.children)item.setAttribute('aria-pressed',String(item===btn));limit=12;recentF=i;draw(fn)};
  });
  draw(F[recentF][2]);
}


const panels = {
  simulador: {draw:simulator},
  'dados-resultados': {clear:['groups','daily'], draw:d => { tables(d,'dados-resultados'); recent(d); }},
  estrategias: {clear:['strategy-verdict','strategy-note','strategy-counts','strategy-shortlist','strategy-insufficient','strategy-reasons'], draw:strategyUI},
  taticas: {clear:['best','avoid','boardopen','boardllm'], draw:boardUI},
  hipoteses: {draw:(d, cert) => brainUI(d,cert,'hipoteses')},
  'estudo-chronologico': {clear:['wfnote','wf'], draw:(d, cert) => brainUI(d,cert,'estudo-chronologico')},
  prospetivo: {clear:['prospective'], draw:prospectiveUI},
  propostas: {clear:['llm'], draw:(d, cert) => brainUI(d,cert,'propostas')},
  notas: {clear:['notes'], draw:(d, cert) => brainUI(d,cert,'notas')},
  entradas: {clear:['entry'], draw:(d, cert) => brainUI(d,cert,'entradas')},
  'sinais-hipoteses': {clear:['watch'], draw:(d, cert) => brainUI(d,cert,'sinais-hipoteses')},
  exploracao: {clear:['says','signals'], draw:d => { insights(d,'exploracao'); signals(d); }},
  'faixas-odd': {clear:['move','calib'], draw:d => insights(d,'faixas-odd')},
  comportamento: {clear:['conf','regime','nextgoal'], draw:d => insights(d,'comportamento')},
  segmentos: {clear:['segs','questions'], draw:d => { insights(d,'segmentos'); tables(d,'segmentos'); }},
  qualidade: {clear:['qc','names'], draw:d => tables(d,'qualidade')},
  'por-liquidar': {clear:['uns'], draw:unsettledUI}
};

const views = [...document.querySelectorAll('.view')];
const nav = [...document.querySelectorAll('nav a')];
const rendered = new Map();
let currentView = 'overview', snapshot = null, report = null, certified = new Set();
let requested = 0, applied = 0, revision = 0, failed = false;

function renderPanel(element) {
  const panel = panels[element.id];
  if (!panel || !element.open || element.closest('[hidden]') || !revision || rendered.get(element.id) === revision) return;
  for (const id of panel.clear || []) $(id).replaceChildren();
  panel.draw(report || {}, certified);
  rendered.set(element.id, revision);
}

function renderView() {
  if (!revision) return;
  const d = report || {};
  if (currentView === 'sinais') agendaUI(d, certified, true);
  if (rendered.get(currentView) !== revision) {
    if (currentView === 'resultados') {
      chart(d);
      resultsSummary(d);
      $('risk').replaceChildren();
      insights(d,'risco');
    }
    if (currentView === 'laboratorio') laboratorySummary(d, certified);
    rendered.set(currentView, revision);
  }
  for (const element of $(currentView).querySelectorAll('details[open][id]')) renderPanel(element);
}

function selectView(moveFocus) {
  const fragment = location.hash.slice(1);
  // Preserve the agenda permalink used in the original dashboard.
  const target = $(fragment === 'agenda' ? 'sinais' : fragment);
  if (fragment === 'conteudo') {
    target.focus({preventScroll:true});
    return;
  }
  const view = target?.closest('.view') || $('overview');
  currentView = view.id;
  if (report) fresh(report, failed);
  for (const element of views) element.hidden = element !== view;
  for (const link of nav) {
    if (link.hash === '#' + currentView) {
      link.setAttribute('aria-current','page');
      $('current-view').textContent = link.textContent.trim();
    } else link.removeAttribute('aria-current');
  }
  const detail = target?.closest('.disclosure');
  if (detail) detail.open = true;
  renderView();
  if (detail) {
    detail.scrollIntoView({block:'start'});
    if (moveFocus) detail.querySelector('summary').focus({preventScroll:true});
  } else if (moveFocus) {
    window.scrollTo(0,0);
    view.querySelector('h1').focus({preventScroll:true});
  }
}

function paint() {
  const d = report || {};
  $('conteudo').setAttribute('aria-busy','false');
  $('load-state').hidden = !!report;
  if (!report) $('load-message').textContent = 'Não foi possível carregar o relatório.';
  fresh(d, failed);
  overview(d, certified);
  if (!report) $('overview-conclusion').textContent = 'A evidência está indisponível.';
  if (currentView !== 'sinais') agendaUI(d, certified, false);
  renderView();
}

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const isCount = value => Number.isSafeInteger(value) && value >= 0;
const isText = value => typeof value === 'string';
const isTimestamp = value => isText(value) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) && Number.isFinite(Date.parse(value));
const isNumber = Number.isFinite;
const isPositive = value => isNumber(value) && value > 0;
const isNullableNumber = value => value === null || isNumber(value);
const hasFields = (value, fields) => isRecord(value) &&
  Object.entries(fields).every(([key, check]) => check(value[key]));
const listOf = check => value => Array.isArray(value) && value.every(check);
const recordOf = check => value => isRecord(value) && Object.values(value).every(check);
const optional = check => value => value === undefined || check(value);
const interval = value => Array.isArray(value) && value.length === 2 && value.every(isNumber);
const nullableInterval = value => value === null || interval(value);

const REFERENCE = 'Q7 Todas as apostas (referência)';
const roiEstimate = value => hasFields(value, {roi:isNumber, ci95:nullableInterval});
function resultBlock(value) {
  return hasFields(value, {bets:isCount, settled:isCount, wins:isCount, pushes:isCount, units:isNumber, avg_odds:isNullableNumber}) &&
    value.wins + value.pushes <= value.settled && value.settled <= value.bets &&
    (value.settled === 0 ? value.roi === null : hasFields(value.roi, {
      'odd-0%':roiEstimate, 'odd-2%':roiEstimate, 'odd-5%':roiEstimate
    }));
}

const accuracyRows = recordOf(value => hasFields(value, {
  signals:isCount, with_result:isCount, hits:isCount, misses:isCount,
  hit_rate:isNullableNumber, break_even:isNullableNumber, ci95:nullableInterval,
  prob_above:optional(isNullableNumber)
}));
const conditions = listOf(value => Array.isArray(value) && value.length === 2 && isText(value[0]));
const hypothesis = value => hasFields(value, {
  id:isText, status:isText, conditions, n:isCount, hits:isCount,
  roi:optional(isNullableNumber), prob_above:optional(isNullableNumber)
});
const boardHypothesis = value => hasFields(value, {
  id:isText, level:isText, conditions, n:isCount, hits:isCount, break_even:isNumber, roi:isNumber,
  edge_ci:optional(nullableInterval)
});
const signal = value => hasFields(value, {
  match_id:isText, match:isText, family:isText, type:isText, market:isText, selection:isText, date_utc:isTimestamp,
  when:phase => phase === 'por começar' || phase === 'a decorrer',
  kickoff_utc:kickoff => kickoff === null || isTimestamp(kickoff),
  odds:isPositive, odds_last:optional(isNullableNumber), live_max_min:optional(isPositive),
  verdict:verdict => Object.hasOwn(AGV, verdict),
  validated:value => typeof value === 'boolean', conflict:optional(value => typeof value === 'boolean'),
  good:listOf(hypothesis), bad:listOf(hypothesis)
}) && (value.when !== 'por começar' || isTimestamp(value.kickoff_utc));

const collectionTime = value => value === null || value === '' || isTimestamp(value);
// Payoff versions: strictly increasing instants, consecutive payoffs distinct, the last one in force.
const settlementHistory = value => Array.isArray(value) && value.length > 0 && value.every(event =>
  Array.isArray(event) && event.length === 2 && isTimestamp(event[0]) && isNullableNumber(event[1])) &&
  value.every((event, i) => i === 0 || (Date.parse(event[0]) > Date.parse(value[i - 1][0]) && event[1] !== value[i - 1][1]));
const settlementHistoryMatches = row => {
  const [at, w] = row.s[row.s.length - 1];
  return Date.parse(row.s[0][0]) > Date.parse(row.d) && w === row.w && (w === null || Date.parse(at) === Date.parse(row.z));
};
const reportShape = {
  generated_at_utc:isTimestamp, messages:isCount, unique_bets:isCount, settled_bets:isCount,
  min_settled:isPositive, first_signal_utc:value => value === null || isTimestamp(value),
  audit:value => hasFields(value, {
    last_collected_at_utc:collectionTime, collector_last_run_utc:optional(collectionTime),
    ingestion_gaps:optional(value => hasFields(value, {count:isCount})),
    name_review:optional(listOf(row => hasFields(row, {bot:listOf(isText), bsd_candidates:listOf(listOf(isText))})))
  }),
  groups:recordOf(resultBlock), questions:recordOf(resultBlock), accuracy:accuracyRows,
  timeline:listOf(value => hasFields(value, {d:isTimestamp, u:isNumber, g:isNumber, w:isNumber, t:isText, m:isText, s:isText, o:isPositive})),
  simulation:optional(value => hasFields(value, {
    total_bets:isCount, timed_settlements:isCount, committed_games:isCount, max_units_per_game:isCount,
    entries:listOf(row => hasFields(row, {d:isTimestamp, z:time => time === null || isTimestamp(time),
      w:isNullableNumber, o:isPositive, t:isText, s:optional(settlementHistory)}) &&
      (row.w === null ? row.z === null : Date.parse(row.z) > Date.parse(row.d)) &&
      (row.s === undefined || settlementHistoryMatches(row)))
  })),
  daily:listOf(value => hasFields(value, {day:isText, bets:isCount, settled:isCount, wins:isCount, units:isNumber})),
  recent_bets:listOf(value => hasFields(value, {
    date_utc:isTimestamp, match_id:isText, group:isText, match:isText, selection:isText, odds:isPositive,
    ft:value => value === null || interval(value), w:isNullableNumber
  })),
  matches:optional(recordOf(value => hasFields(value, {
    events:listOf(isRecord), goals:optional(value => value === null || listOf(Array.isArray)(value))
  }))),
  unsettled:optional(listOf(value => hasFields(value, {
    match:isText, date_utc:isTimestamp, why:value => isText(value) && value.length > 0
  }))),
  operational:optional(value => hasFields(value, {scope:scope => scope === 'eligible_bets_only', accuracy:accuracyRows, regime:optional(listOf(isRecord))})),
  insights:optional(value => hasFields(value, {
    narrative:optional(listOf(isText)), segments:optional(listOf(isRecord)), bot_confidence:optional(listOf(isRecord)),
    calibration:optional(listOf(row => hasFields(row, {band:isText, ci95:nullableInterval}))),
    regime:optional(listOf(row => isRecord(row) && (!row.before || hasFields(row, {before:isRecord, recent:isRecord, state:isText})))),
    price_move:optional(recordOf(row => isRecord(row) && (!row.n || hasFields(row, {when_shortened:isRecord, when_drifted:isRecord})))),
    next_goal:optional(isRecord), luck:optional(isRecord), method:optional(isRecord)
  })),
  brain:optional(value => hasFields(value, {
    counts:optional(recordOf(isCount)), top:optional(listOf(hypothesis)), llm:optional(listOf(hypothesis)),
    status_list:optional(recordOf(isText)),
    validation:optional(row => hasFields(row, {state:isText, method:isText, ledger_valid:v => typeof v === 'boolean', certified:listOf(isText)})),
    presentation:optional(row => hasFields(row, {hypotheses:recordOf(isRecord)})),
    agenda:optional(row => hasFields(row, {
      generated_utc:isTimestamp, items:listOf(signal),
      rules:rules => hasFields(rules, {live_max_min:isPositive, min_results:isPositive})
    })),
    board:optional(row => hasFields(row, {min_results:isPositive, hint_at:isPositive, best:listOf(boardHypothesis), avoid:listOf(boardHypothesis),
      open:listOf(item => hasFields(item, {tactic:optional(isText)}))})),
    watch:optional(listOf(row => hasFields(row, {reasons:listOf(reason => hasFields(reason, {id:isText}))}))),
    notes:optional(listOf(row => hasFields(row, {id:isText}))),
    entry_points:optional(listOf(isRecord)),
    track:optional(row => hasFields(row, {llm:optional(listOf(isRecord))})),
    walk_forward:optional(row => hasFields(row, {rule:isRecord, followed:isRecord, skipped:isRecord}))
  }))
};

function validateReport(next, previous) {
  // The core comes from scangoal/report.py. Optional sections may be absent
  // on first load, and published sections may be explicitly empty.
  if (!hasFields(next, reportShape)) return false;
  const generated = Date.parse(next.generated_at_utc);
  if (generated > Date.now() + 5000) return false;
  const reference = next.questions[REFERENCE], groups = Object.values(next.groups);
  if (reference?.bets !== next.unique_bets || reference.settled !== next.settled_bets ||
      next.timeline.length !== Math.min(next.settled_bets, 600) ||
      next.recent_bets.length !== Math.min(next.unique_bets, 60) ||
      groups.reduce((sum, group) => sum + group.bets, 0) !== next.unique_bets ||
      groups.reduce((sum, group) => sum + group.settled, 0) !== next.settled_bets ||
      (next.unique_bets > 0 && (!next.first_signal_utc || !next.daily.length))) return false;
  if (next.simulation?.entries.some(row =>
      Date.parse(row.d) > generated || (row.z !== null && Date.parse(row.z) > generated) ||
      (row.s || []).some(([at]) => Date.parse(at) > generated))) return false;
  if (!previous) return true;
  if (generated < Date.parse(previous.generated_at_utc)) return false;
  // A response must not silently drop sections already supplied by the producer.
  return ['brain', 'operational', 'insights', 'matches', 'unsettled', 'simulation'].every(key => !Object.hasOwn(previous, key) || Object.hasOwn(next, key)) &&
    ['agenda', 'validation', 'top', 'counts', 'presentation', 'board', 'track', 'walk_forward', 'llm', 'notes', 'watch', 'entry_points']
      .every(key => previous.brain?.[key] === undefined || next.brain?.[key] !== undefined) &&
    Object.keys(previous.insights || {}).every(key => Object.hasOwn(next.insights, key)) &&
    Object.keys(previous.questions).every(key => Object.hasOwn(next.questions, key));
}

async function agendaRefresh() {
  const request = ++requested;
  let text, next, nextCertificates;
  try {
    const response = await fetch('report.json', {cache:'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    text = await response.text();
    if (request < applied) return;
    if (text !== snapshot) {
      next = JSON.parse(text);
      if (!validateReport(next, report)) throw new Error('Formato de relatório inválido');
      nextCertificates = certificates(next.brain);
    }
  } catch {
    if (request < applied) return;
    applied = request;
    failed = true;
    if (report) fresh(report, failed);
    else {
      if (!revision) revision++;
      paint();
    }
    return;
  }

  applied = request;
  failed = false;
  if (!next) {
    fresh(report, failed);
    agendaUI(report, certified, currentView === 'sinais');
    return;
  }
  // Commit only a parsed, validated report and its matching scientific state.
  report = next;
  certified = nextCertificates;
  snapshot = text;
  revision++;
  $('facts').replaceChildren();
  answer(next);
  paint();
}

const systemTheme = matchMedia('(prefers-color-scheme: dark)');
let chosenTheme;
try { chosenTheme = localStorage.getItem('goal-roi-theme'); } catch {}
function showTheme() {
  const dark = chosenTheme ? chosenTheme === 'dark' : systemTheme.matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme').setAttribute('aria-pressed', String(dark));
}
$('theme').onclick = () => {
  chosenTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('goal-roi-theme', chosenTheme); } catch {}
  showTheme();
};
systemTheme.addEventListener('change', showTheme);
showTheme();

for (const element of document.querySelectorAll('.disclosure[id]')) {
  element.addEventListener('toggle', () => renderPanel(element));
}
for (const input of document.querySelectorAll('[name="signal-phase"], #signal-source, #signal-status')) {
  input.addEventListener('change', () => agendaUI(report || {}, certified, true));
}
window.addEventListener('hashchange', () => selectView(true));
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (link?.hash === location.hash && !event.defaultPrevented && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    selectView(true);
  }
});
$('retry').onclick = () => {
  $('conteudo').setAttribute('aria-busy','true');
  $('load-message').textContent = 'A carregar o relatório…';
  agendaRefresh();
};
selectView(false);
agendaRefresh();
setInterval(agendaRefresh, 3 * 60000);
setInterval(() => {
  if (!document.hidden && report) {
    fresh(report, failed);
    agendaUI(report, certified, currentView === 'sinais');
  }
}, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) agendaRefresh(); });

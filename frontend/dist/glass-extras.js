'use strict';
(()=>{
const AW={H:1.008,He:4.0026,Li:6.94,Be:9.0122,B:10.81,C:12.011,N:14.007,O:15.999,F:18.998,Ne:20.18,Na:22.99,Mg:24.305,Al:26.982,Si:28.085,P:30.974,S:32.06,Cl:35.45,Ar:39.948,K:39.098,Ca:40.078,Sc:44.956,Ti:47.867,V:50.942,Cr:51.996,Mn:54.938,Fe:55.845,Co:58.933,Ni:58.693,Cu:63.546,Zn:65.38,Ga:69.723,Ge:72.63,As:74.922,Se:78.971,Br:79.904,Kr:83.798,Rb:85.468,Sr:87.62,Y:88.906,Zr:91.224,Nb:92.906,Mo:95.95,Tc:98,Ru:101.07,Rh:102.91,Pd:106.42,Ag:107.87,Cd:112.41,In:114.82,Sn:118.71,Sb:121.76,Te:127.6,I:126.9,Xe:131.29,Cs:132.91,Ba:137.33,La:138.91,Ce:140.12,Pr:140.91,Nd:144.24,Pm:145,Sm:150.36,Eu:151.96,Gd:157.25,Tb:158.93,Dy:162.5,Ho:164.93,Er:167.26,Tm:168.93,Yb:173.05,Lu:174.97,Hf:178.49,Ta:180.95,W:183.84,Re:186.21,Os:190.23,Ir:192.22,Pt:195.08,Au:196.97,Hg:200.59,Tl:204.38,Pb:207.2,Bi:208.98,Th:232.04,U:238.03};
const mwCache=new Map();
function molarMass(f){if(mwCache.has(f))return mwCache.get(f);let m=null;const s=String(f).trim();
 if(/^([A-Z][a-z]?\d*(\.\d+)?)+$/.test(s)){m=0;for(const [,el,n] of s.matchAll(/([A-Z][a-z]?)(\d*(?:\.\d+)?)/g)){if(!AW[el]){m=null;break}m+=AW[el]*(n===''?1:Number(n))}}
 mwCache.set(f,m);return m}
function toWt(comp){const e=Object.entries(comp||{}).map(([k,v])=>[k,Number(v)]).filter(([k,v])=>Number.isFinite(v)&&v>0);if(!e.length)return null;let sum=0;const w=[];for(const [k,v] of e){const m=molarMass(k);if(!m)return null;w.push([k,v*m]);sum+=v*m}const out={};for(const [k,x] of w)out[k]=Math.round(x/sum*10000)/100;return out}
const WT_LABEL='wt%（由 mol% 换算）';
const SHOW_PRESETS=false,SHOW_WT=false;// 用途预设与 wt% 换算待核对后再开启
let wtMode=SHOW_WT&&localStorage.getItem('glassWt')==='1';
const _summary=compositionSummary;
compositionSummary=function(comp,basis,lim){if(wtMode&&basis==='mol%'){const w=toWt(comp);if(w)return _summary(w,'wt%',lim).replace('质量百分比','质量百分比 · 换算')}return _summary(comp,basis,lim)};
const _get=get;
get=async function(u,...a){const r=await _get(u,...a);if(wtMode&&typeof u==='string'&&/^\/api\/glass\/recipe\/\d+$/.test(u)&&r&&r.basis==='mol%'){const w=toWt(r.comp);if(w){r.comp=w;r.basis=WT_LABEL}}return r};

/* 用途预设 */
const PRESETS=[['低介电高频基板','相对介电常数小于5，介电损耗角正切小于0.005'],['与硅热匹配','线膨胀系数2.5~4 ppm/K'],['高折射','折射率大于1.8'],['低色散','阿贝数大于60'],['耐高温','Tg大于700°C']];
const tools=document.querySelector('#glass .engine-tools');
if(tools){
 const row=document.createElement('div');row.className='glass-presets';row.innerHTML='<span>按用途</span>'+PRESETS.map(([n,t])=>`<button type="button" data-preset="${esc(t)}" title="${esc(t)}（参考阈值，可在条件标签中修改）">${esc(n)}</button>`).join('');
 if(SHOW_PRESETS)tools.after(row);
 row.onclick=e=>{const b=e.target.closest('[data-preset]');if(!b)return;$('intent').value=b.dataset.preset;runIntent()};
 const pb=document.createElement('button');pb.className='text';pb.id='openPlot';pb.textContent='数据图 ⌗';tools.insertBefore(pb,$('browse'));
 pb.onclick=openPlot;
}
/* wt% 切换 */
const actions=document.querySelector('#glass .toolbar-actions');
if(actions&&SHOW_WT){const seg=document.createElement('div');seg.className='segmented basis-toggle';seg.title='mol% 配方可按氧化物摩尔质量换算为 wt% 显示';seg.innerHTML='<button data-basis="mol">mol%</button><button data-basis="wt">wt%</button>';actions.insertBefore(seg,actions.firstChild);
 const paint=()=>seg.querySelectorAll('button').forEach(b=>b.classList.toggle('on',(b.dataset.basis==='wt')===wtMode));paint();
 seg.onclick=e=>{const b=e.target.closest('[data-basis]');if(!b)return;const v=b.dataset.basis==='wt';if(v===wtMode)return;wtMode=v;localStorage.setItem('glassWt',v?'1':'0');paint();search();if($('recipeModal').open&&current)detail(current.id)}}

/* 相近配方 */
const _detail=detail;
detail=async function(id){await _detail(id);if(!current||String(current.id)!==String(id)||!$('recipeModal').open)return;const box=document.createElement('section');box.className='similar-section';box.innerHTML='<h3 class="section-title">相近配方</h3><p class="hint">正在查找组成最接近的配方…</p>';$('detail').appendChild(box);
 try{const d=await _get('/api/glass/recipe/'+id+'/similar');if(String(current.id)!==String(id))return;
  if(!d.items.length){box.innerHTML='<h3 class="section-title">相近配方</h3><p class="hint">'+esc(d.note||'未找到组成相近的配方。')+'</p>';return}
  box.innerHTML='<h3 class="section-title">相近配方 <small>按组成差异从小到大</small></h3><div class="similar-list">'+d.items.map(it=>`<button type="button" class="similar-item" data-similar="${it.id}"><span class="similar-top"><b>${it.family==='厂商产品'?esc(it.name):'M'+it.id}</b><span class="similar-diff">组成差异 ${esc(fmt(it.distance))}%</span></span><span class="similar-comp">${compositionSummary(it.comp,it.basis,5)}</span>${(it.previews||[]).slice(0,3).map(p=>`<span class="similar-prop">${esc(p.kind)} <b>${val(p)}</b> <small>${esc(unitText(p.unit))}</small></span>`).join('')}</button>`).join('')+'</div>';
  box.onclick=e=>{const b=e.target.closest('[data-similar]');if(b){$("recipeModal").close();detail(b.dataset.similar)}};
 }catch(e){box.innerHTML='<h3 class="section-title">相近配方</h3><p class="hint">暂时无法加载相近配方。</p>'}};

/* 数据图 */
const AXES=[['vd','阿贝数 νd'],['nd','折射率 nd'],['tg','玻璃转变温度'],['density','密度'],['cte','线膨胀系数'],['dk','相对介电常数'],['df','介电损耗角正切'],['modulus','杨氏模量'],['comp','组分含量（mol%）']];
const PLOTS=[['阿贝图','vd','nd',true],['Tg 随组分','comp','tg'],['密度随组分','comp','density'],['膨胀随组分','comp','cte'],['介电常数 – 损耗','dk','df']];
const dlg=document.createElement('dialog');dlg.id='plotModal';
const opts=sel=>AXES.map(([k,n])=>`<option value="${k}"${k===sel?' selected':''}>${n}</option>`).join('');
dlg.innerHTML=`<div class="dialog-top"><div><span class="eyebrow">按当前检索条件作图</span><h2>数据图</h2></div><button id="closePlot" class="icon-close" aria-label="关闭数据图">×</button></div><div class="plot-body"><div class="plot-presets">${PLOTS.map((p,i)=>`<button type="button" data-plot="${i}">${p[0]}</button>`).join('')}</div><div class="plot-controls"><label>横轴 <select id="plotX">${opts('vd')}</select></label><input id="plotXComp" placeholder="组分，如 B2O3" hidden><label>纵轴 <select id="plotY">${opts('nd')}</select></label><input id="plotYComp" placeholder="组分，如 B2O3" hidden><label class="check"><input type="checkbox" id="plotRev">横轴反向</label><label class="check"><input type="checkbox" id="plotScope" checked>只用当前检索条件</label><button id="plotGo" class="primary">作图</button></div><p id="plotStatus" class="hint" role="status"></p><div id="plotArea"></div></div>`;
document.body.appendChild(dlg);
const syncComp=()=>{$('plotXComp').hidden=$('plotX').value!=='comp';$('plotYComp').hidden=$('plotY').value!=='comp'};
$('plotX').onchange=$('plotY').onchange=syncComp;
$('closePlot').onclick=()=>dlg.close();
const defComp=()=>components.find(c=>/O/.test(c)&&c!=='SiO2')||components[0]||'B2O3';
dlg.querySelector('.plot-presets').onclick=e=>{const b=e.target.closest('[data-plot]');if(!b)return;const [,x,y,rev]=PLOTS[Number(b.dataset.plot)];$('plotX').value=x;$('plotY').value=y;$('plotRev').checked=!!rev;if(x==='comp'&&!$('plotXComp').value)$('plotXComp').value=defComp();syncComp();draw()};
$('plotGo').onclick=draw;
function openPlot(){if(!$('plotXComp').value)$('plotXComp').value=defComp();syncComp();dlg.showModal();draw()}
const axisSpec=(sel,inp)=>sel.value==='comp'?'comp:'+(inp.value.trim()||'B2O3'):sel.value;
let plotSeq=0;
async function draw(){const n=++plotSeq;$('plotStatus').textContent='正在读取数据…';
 const p=$('plotScope').checked?params():new URLSearchParams();for(const k of ['offset','limit','view','sort','order'])p.delete(k);p.set('x',axisSpec($('plotX'),$('plotXComp')));p.set('y',axisSpec($('plotY'),$('plotYComp')));
 try{const d=await _get('/api/glass/plot?'+p);if(n!==plotSeq)return;render(d)}catch(e){$('plotStatus').textContent='作图失败：'+(e.message||e);$('plotArea').innerHTML=''}}
function ticks(a,b,k=6){if(a===b){a-=1;b+=1}const span=b-a,step0=span/k,mag=10**Math.floor(Math.log10(step0)),step=[1,2,2.5,5,10].map(x=>x*mag).find(x=>span/x<=k)||10*mag;const t=[];for(let v=Math.ceil(a/step)*step;v<=b+1e-9;v+=step)t.push(+v.toPrecision(10));return{lo:a,hi:b,t}}
const u=x=>x.unit&&x.unit!=='1'?`（${x.unit}）`:'';
function render(d){const pts=d.points;$('plotStatus').textContent=pts.length?`共 ${fmt(d.total)} 种材料同时有两项数据${d.hidden_outliers?`，已隐藏 ${fmt(d.hidden_outliers)} 个极端值`:''}${d.shown<d.total-d.hidden_outliers?`，均匀抽取 ${fmt(d.shown)} 个点显示`:''}。点击数据点查看配方。`:'当前条件下没有同时具有这两项数据的材料。';
 if(!pts.length){$('plotArea').innerHTML='';return}
 const W=900,H=520,L=74,R=24,T=18,B=58;const xs=pts.map(p=>p[1]),ys=pts.map(p=>p[2]);
 let X=ticks(Math.min(...xs),Math.max(...xs)),Y=ticks(Math.min(...ys),Math.max(...ys));const pad=(o)=>{const s=(o.hi-o.lo)*.03||1;return ticks(o.lo-s,o.hi+s)};X=pad(X);Y=pad(Y);
 const rev=$('plotRev').checked,sx=v=>rev?W-R-(v-X.lo)/(X.hi-X.lo)*(W-L-R):L+(v-X.lo)/(X.hi-X.lo)*(W-L-R),sy=v=>H-B-(v-Y.lo)/(Y.hi-Y.lo)*(H-T-B);
 const f=v=>Number(v).toLocaleString('zh-CN',{maximumSignificantDigits:5});
 let s=`<svg viewBox="0 0 ${W} ${H}" class="plot-svg" role="img" aria-label="${esc(d.y.label)} 对 ${esc(d.x.label)} 散点图">`;
 s+=X.t.map(v=>`<line x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H-B}" class="grid"/><text x="${sx(v)}" y="${H-B+20}" text-anchor="middle">${f(v)}</text>`).join('');
 s+=Y.t.map(v=>`<line x1="${L}" x2="${W-R}" y1="${sy(v)}" y2="${sy(v)}" class="grid"/><text x="${L-8}" y="${sy(v)+4}" text-anchor="end">${f(v)}</text>`).join('');
 s+=`<rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" class="frame"/><text x="${(L+W-R)/2}" y="${H-12}" text-anchor="middle" class="axis-title">${esc(d.x.label)}${esc(u(d.x))}</text><text transform="translate(18 ${(T+H-B)/2}) rotate(-90)" text-anchor="middle" class="axis-title">${esc(d.y.label)}${esc(u(d.y))}</text>`;
 s+=pts.map(p=>`<circle cx="${sx(p[1]).toFixed(1)}" cy="${sy(p[2]).toFixed(1)}" r="2.6" data-id="${p[0]}"><title>${esc(p[3]||'')} · M${p[0]}\n${esc(d.x.label)} ${f(p[1])}\n${esc(d.y.label)} ${f(p[2])}</title></circle>`).join('');
 $('plotArea').innerHTML=s+'</svg>'}
$('plotArea').onclick=e=>{const c=e.target.closest('circle[data-id]');if(c){dlg.close();detail(c.dataset.id)}};
})();

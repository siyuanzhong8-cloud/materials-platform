// Deep-linked 3D view using the existing bundled viewer: /catalyst?kind=computed|literature&id=<id>&state=bare|OH|O|OOH
if (!document.getElementById('svTitle')) {
  const host = document.createElement('div');
  host.className = 'sv-page';
  host.innerHTML = `
<header class="sv-head"><a href="/#catalyst" class="sv-back">← 催化材料</a><div><span id="svKind" class="sv-eyebrow">3D 结构</span><h1 id="svTitle">正在读取…</h1></div></header>
<main class="sv-main">
  <div class="sv-bar"><div id="svStates" class="sv-states" role="tablist" aria-label="吸附态"></div><div class="sv-actions"><button type="button" id="svReset" disabled>复位视角</button><a id="svDownload" href="#" hidden>下载结构 ↓</a></div></div>
  <div class="sv-stage"><div id="svCanvas" class="sv-canvas" aria-label="原子结构三维视图"></div><div id="svMsg" class="sv-msg">正在载入…</div><div id="svLegend" class="sv-legend"></div></div>
</main>
`;
  const root = document.getElementById('root');
  if (root) root.replaceWith(host); else document.body.prepend(host);
}
const $ = (id) => document.getElementById(id);
const STATES = ['bare', 'OH', 'O', 'OOH'];
const LABEL = { bare: '裸态', OH: 'OH*', O: 'O*', OOH: 'OOH*' };
const COLORS = { H: '#e7e7dc', C: '#4c6267', O: '#dc5d49', N: '#5887cb', Cu: '#bd8251', Cl: '#72aa5c', Ni: '#63a38b', Co: '#697fd0', Fe: '#c57650', S: '#d5b954', F: '#77bb91', Zn: '#8e91a4', Mn: '#b783b2' };
const color = (e) => COLORS[e] || '#9ca9ac';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const qs = new URLSearchParams(location.search);
const kind = qs.get('kind') === 'literature' ? 'literature' : 'computed';
const id = qs.get('id') || '';
const base = kind === 'literature' ? '/api/literature/materials/' : '/api/configurations/';
let lib = null;
let viewer = null;
let current = '';
let available = [];

async function get(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) {
    let m = '';
    try { m = (await r.json()).detail; } catch {}
    throw new Error(m || `读取失败（${r.status}）`);
  }
  return r.json();
}

function message(text, error) {
  $('svMsg').textContent = text || '';
  $('svMsg').classList.toggle('error', !!error);
}

function cellEdges(cell) {
  if (!Array.isArray(cell) || cell.length !== 3 || cell.some((v) => !Array.isArray(v) || v.length !== 3 || v.some((n) => !Number.isFinite(n)))) return [];
  const corner = (b) => { const p = [0, 0, 0]; for (let a = 0; a < 3; a++) if (b & (1 << a)) for (let k = 0; k < 3; k++) p[k] += cell[a][k]; return { x: p[0], y: p[1], z: p[2] }; };
  const out = [];
  for (let b = 0; b < 8; b++) for (let a = 0; a < 3; a++) if (!(b & (1 << a))) out.push([corner(b), corner(b | (1 << a))]);
  return out;
}

async function show(state) {
  current = state;
  for (const b of $('svStates').querySelectorAll('button')) b.classList.toggle('on', b.dataset.state === state);
  history.replaceState(null, '', `?${new URLSearchParams({ kind, id, state })}`);
  $('svDownload').href = `${base}${encodeURIComponent(id)}/structures/${encodeURIComponent(state)}/download`;
  $('svDownload').hidden = false;
  message('正在载入结构…');
  $('svReset').disabled = true;
  try {
    const g = await get(`${base}${encodeURIComponent(id)}/structures/${encodeURIComponent(state)}`);
    if (current !== state) return;
    if (!lib) lib = (await import('/assets/3Dmol-BJNJWXEl.js'))._;
    if (!viewer) viewer = lib.createViewer($('svCanvas'), { backgroundColor: '#eaf0ed', antialias: true });
    viewer.clear();
    const model = viewer.addModel();
    model.addAtoms(g.atoms.map((a) => ({ elem: a.elem, x: a.x, y: a.y, z: a.z })));
    const elems = [...new Set(g.atoms.map((a) => a.elem))];
    for (const e of elems) model.setStyle({ elem: e }, { sphere: { scale: 0.28, color: color(e) }, stick: { radius: 0.1, color: color(e) } });
    for (const [s, t] of cellEdges(g.cell)) viewer.addLine({ start: s, end: t, color: '#7a9695', linewidth: 1 });
    viewer.zoomTo();
    viewer.render();
    $('svLegend').innerHTML = elems.map((e) => `<span><i style="background:${color(e)}"></i>${esc(e)}</span>`).join('') + `<span>${g.atoms.length} 原子</span>`;
    $('svReset').disabled = false;
    message('');
  } catch (e) {
    if (current === state) message(e.message || '当前浏览器无法显示三维结构，可下载原始结构查看。', true);
  }
}

(async () => {
  if (!id) { $('svTitle').textContent = '未指定结构'; message('缺少结构编号', true); return; }
  try {
    const d = await get(base + encodeURIComponent(id));
    $('svKind').textContent = kind === 'literature' ? '文献材料 · 3D 结构' : '预测构型 · 3D 结构';
    const name = kind === 'literature' ? (d.name || d.formula) : [d.formula, d.variant].filter(Boolean).join(' · ');
    $('svTitle').textContent = name || id;
    document.title = `${name || id} · 3D 结构`;
    available = kind === 'literature'
      ? STATES.filter((s) => d.states && d.states[s] && d.states[s].available)
      : STATES.filter((s) => d.structures && d.structures[s]);
    $('svStates').innerHTML = STATES.map((s) => `<button type="button" role="tab" data-state="${s}" ${available.includes(s) ? '' : 'disabled'}>${LABEL[s]}</button>`).join('');
    if (!available.length) { message('该条目没有可显示的结构', true); return; }
    const want = qs.get('state');
    show(available.includes(want) ? want : available[0]);
  } catch (e) {
    $('svTitle').textContent = '无法读取';
    message(e.message || String(e), true);
  }
})();

$('svStates').onclick = (e) => {
  const b = e.target.closest('button[data-state]');
  if (b && !b.disabled && b.dataset.state !== current) show(b.dataset.state);
};
$('svReset').onclick = () => { if (viewer) { viewer.zoomTo(); viewer.render(); } };
window.addEventListener('resize', () => { if (viewer) { viewer.resize(); viewer.render(); } });

'use strict';
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => {
    if (n == null || n === '') return '未提供';
    const v = Number(n);
    if (!Number.isFinite(v)) return esc(n);
    if (Math.abs(v) > 0 && Math.abs(v) < 0.0001) return v.toExponential(4);
    return v.toLocaleString('zh-CN', { maximumSignificantDigits: 6 });
  };
  const chem = (s) => esc(s).replace(/(\d+)/g, '<sub>$1</sub>');
  const statusName = (s) => ({ experimentally_verified: '实验报道', computational_only: '文献计算', unknown: '待核对' }[s] || s || '待核对');
  // 数据类型 (platform-wide): 实验数据 = measured; 计算数据 = first-principles DFT; 预测数据 = UMA / ML-potential inference.
  const DATA_CLASS = { experimental: '实验数据', computed: '计算数据', predicted: '预测数据' };
  const DATA_CLASS_COLOR = { experimental: '#c4572f', computed: '#3c6fb4', predicted: '#087c80' };
  const dataClassOf = (x) => x.data_class || (x.kind === 'computed' ? 'predicted' : (x.experimental_overpotential_V != null ? 'experimental' : 'computed'));
  const dataClassLabel = (x) => DATA_CLASS[dataClassOf(x)] || '计算数据';
  const classTag = (x) => `<small class="status-tag dc-tag dc-${esc(dataClassOf(x))}">${esc(dataClassLabel(x))}</small>`;
  function selectedReactions() {
    return [...document.querySelectorAll('#oerReactionMenu input[name="oerReaction"]:checked')].map((el) => el.value);
  }
  function selectedReactionParam() {
    const sel = selectedReactions();
    return sel.length ? sel.join(',') : 'OER';
  }
  function reactionTag(x) {
    const label = x.reaction_label || ({ OER: 'OER', ORR: 'ORR', CO2: 'CO2RR' }[x.reaction] || x.reaction);
    return label ? `<small class="reaction-tag">${esc(label)}</small>` : '';
  }
  function emptyReactionHint() {
    const sel = selectedReactions();
    if (sel.length && !sel.includes('OER')) {
      return `当前库中还没有 ${sel.join('、')} 的文献数据。已收录的是 OER 材料，可改选 OER，或等待该反应数据接入后再查。`;
    }
    return '请减少元素、放宽过电位范围，或改用关键词、化学式再试。';
  }
  function updateReactionLabel() {
    const sel = selectedReactions();
    $('oerReactionLabel').textContent = !sel.length ? '选择反应' : (sel.length === 3 ? '全部反应' : sel.join('、'));
    const hasOer = sel.includes('OER');
    if (!hasOer && (view === 'computed' || view === 'volcano')) {
      view = 'literature';
      setTabs();
    }
    if ($('oerTabComp')) $('oerTabComp').hidden = !hasOer;
    if ($('oerTabVolcano')) $('oerTabVolcano').hidden = !hasOer;
  }
  function setReactionMenu(open) {
    const menu = $('oerReactionMenu');
    const btn = $('oerReactionBtn');
    if (!menu || !btn) return;
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  async function refreshSummary() {
    summary = await get('/api/oer/summary?' + new URLSearchParams({ reaction: selectedReactionParam() }));
    const lit = summary.literature || {};
    const comp = summary.computed || {};
    $('oerStats').innerHTML = [
      ['文献材料', lit.materials],
      ['实验报道', lit.experimental],
      ['预测构型', comp.configurations],
      ['文献来源', lit.sources],
    ].map(([name, n]) => `<div><strong>${fmt(n)}</strong><span>${name}</span></div>`).join('');
    if ($('oerExpJ')) {
      const opts = (lit.experimental_eta_by_current_density || []).filter((b) => b.current_density_mA_cm2 != null);
      $('oerExpJ').innerHTML = '<option value="">不限（按口径分组显示）</option>' + opts.map((b) => `<option value="${esc(b.current_density_mA_cm2)}">${esc(b.label)}（${fmt(b.n)}）</option>`).join('');
    }
    const sources = lit.source_list || [];
    const keep = $('oerSource').value;
    $('oerSource').innerHTML = '<option value="">全部来源</option>' + sources.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}（${fmt(s.n)}）</option>`).join('');
    if (keep && sources.some((s) => s.id === keep)) $('oerSource').value = keep;
    renderChips();
  }

  const etaBasis = (x) => x.measurement_basis || (x.experimental_current_density_mA_cm2 != null ? `η@${fmt(x.experimental_current_density_mA_cm2)} mA/cm²` : 'η（电流密度未注明）');
  const primaryActivity = (x) => (Array.isArray(x.experimental_activity) ? (x.experimental_activity.find((a) => a && a.primary) || x.experimental_activity[0]) : null);
  const electrolyteName = (s) => ({ acidic: '酸性', alkaline: '碱性', neutral: '中性' }[s] || s);
  const joinPh = (item) => {
    if (item.pH_min == null && item.pH_max == null) return '';
    if (item.pH_min != null && item.pH_max != null && item.pH_min !== item.pH_max) return `pH ${fmt(item.pH_min)}–${fmt(item.pH_max)}`;
    return `pH ${fmt(item.pH_min ?? item.pH_max)}`;
  };

  let summary = null;
  let elements = [];
  let excludeElements = [];
  let filters = { eta: null, exp: null };
  let expJ = null;
  let dataClass = '';
  let warnings = [];
  let view = 'literature';
  let offset = 0;
  let total = 0;
  let limit = 20;
  let seq = 0;
  let lastIntent = '';
  let busy = false;
  const conditions = new Map();

  async function get(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let message;
      try { message = (await r.json()).detail; } catch {}
      throw new Error(message || `数据读取失败（${r.status}）`);
    }
    return r.json();
  }

  function params() {
    const p = new URLSearchParams({
      q: $('oerQuery').value.trim(),
      reaction: selectedReactionParam(),
      elements: elements.join(','),
      exclude_elements: excludeElements.join(','),
      match: $('oerMatch').value,
      status: $('oerStatus').value,
      electrolyte: $('oerElectrolyte').value,
      source_id: $('oerSource').value,
      completeness: $('oerCompleteness').value,
      corpus: $('oerCorpus').value,
      offset: String(offset),
      limit: String(limit),
    });
    for (const [id, key] of [['oerEtaLo', 'eta_min'], ['oerEtaHi', 'eta_max'], ['oerExpEtaLo', 'exp_eta_min'], ['oerExpEtaHi', 'exp_eta_max'], ['oerPhLo', 'pH_min'], ['oerPhHi', 'pH_max']]) {
      if ($(id).value !== '') p.set(key, $(id).value);
    }
    if ($('oerExpJ')) expJ = $('oerExpJ').value === '' ? null : Number($('oerExpJ').value);
    if ($('oerDataClass')) dataClass = $('oerDataClass').value;
    if (expJ != null) p.set('exp_j', String(expJ));
    if (dataClass) p.set('data_class', dataClass);
    return p;
  }

  function storeCondition(id, payload) {
    conditions.set(id, payload);
    return id;
  }

  function conditionButton(id) {
    return `<button type="button" class="condition-link" data-oer-condition="${esc(id)}">条件 ↗</button>`;
  }

  function shortCondition(item) {
    const bits = [];
    const tags = (item.electrolyte_conditions || []).filter((x) => typeof x === 'string').map(electrolyteName);
    if (tags.length) bits.push(tags.join('、') + '电解液');
    else if (item.electrolyte) bits.push(String(item.electrolyte).slice(0, 40));
    const ph = joinPh(item);
    if (ph) bits.push(ph);
    if (item.experimental_current_density_mA_cm2 != null) bits.push(`${fmt(item.experimental_current_density_mA_cm2)} mA/cm²`);
    if (item.temperature_K != null) bits.push(`${fmt(item.temperature_K)} K`);
    if (item.method_summary && !/literature_metadata|DFT_descriptors_only/i.test(item.method_summary)) bits.push(item.method_summary);
    return bits.length ? bits.join(' · ') : '测试条件见详情';
  }

  function structureUrl(kind, id, state) {
    const p = new URLSearchParams({ kind, id });
    if (state) p.set('state', state);
    return '/catalyst?' + p;
  }

  function structureLink(kind, id) {
    return `<a class="structure-link" href="${esc(structureUrl(kind, id))}" target="_blank" rel="noopener">3D 结构 ↗</a>`;
  }

  function testTags(item) {
    const exp = item.experimental_conditions || {};
    const tags = [];
    if (item.experimental_current_density_mA_cm2 != null) tags.push(`@${fmt(item.experimental_current_density_mA_cm2)} mA/cm²`);
    if (exp.catalyst_loading_mg_cm2 != null) tags.push(`负载 ${fmt(exp.catalyst_loading_mg_cm2)} mg/cm²`);
    if (exp.LSV_scan_rate_mV_s != null) tags.push(`扫速 ${fmt(exp.LSV_scan_rate_mV_s)} mV/s`);
    return tags.length ? `<span class="test-tags">${tags.map((t) => `<i>${esc(t)}</i>`).join('')}</span>` : '';
  }

  function rowName(x) {
    const parts = String(x.name || x.formula || '未命名材料').split('|').map((t) => t.trim()).filter(Boolean);
    const head = parts.shift() || '未命名材料';
    return esc(head) + (parts.length ? `<small>${esc(parts.join(' · '))}</small>` : '');
  }

  function etaCell(x) {
    const v3 = (n) => (Number.isFinite(Number(n)) ? Number(n).toFixed(3) : esc(n));
    if (x.kind === 'literature' && x.experimental_overpotential_V != null) return `<b>${v3(x.experimental_overpotential_V)}</b><span class="unit">V</span><span class="theory-tag exp-basis">${esc(etaBasis(x))}</span>`;
    const act = x.kind === 'literature' ? primaryActivity(x) : null;
    if (act && act.value != null) return `<b>${fmt(act.value)}</b><span class="unit">${esc(act.unit || '')}</span><span class="theory-tag exp-basis">${esc(x.measurement_basis || act.metric || '')}</span>`;
    // Non-experimental η: UMA inference (oer.sqlite) = 预测值; literature DFT = 计算值.
    if (x.overpotential_V != null) return `<b>${v3(x.overpotential_V)}</b><span class="unit">V</span><span class="theory-tag">${x.kind === 'computed' ? '预测值' : '计算值'}</span>`;
    return '<b class="none">—</b>';
  }

  function groupHeader(x) {
    const cls = dataClassOf(x);
    if (cls === 'predicted') return '预测数据 · UMA 推理构型';
    if (cls === 'computed') return '计算数据 · 文献 DFT（理论过电位为计算值）';
    const basis = x.measurement_basis || (x.experimental_overpotential_V != null ? etaBasis(x) : '');
    return `实验数据${basis ? ' · ' + basis : ''}${x.high_throughput ? ' · 高通量数据集' : ''}`;
  }

  function sourceText(item) {
    const s = item.source || {};
    if (s.title) return s.title;
    if (s.doi) return s.doi;
    return '';
  }

  function doiHref(doi) {
    if (!doi) return '';
    return /^https?:/i.test(doi) ? doi : 'https://doi.org/' + String(doi).replace(/^doi:/i, '');
  }

  function propertyRows(item) {
    const rows = [];
    if (item.kind === 'literature') {
      if (item.experimental_overpotential_V != null) {
        const id = storeCondition(item.id + ':exp_eta', {
          title: '实验过电位',
          value: item.experimental_overpotential_V,
          unit: 'V',
          note: item.experimental_current_density_mA_cm2 != null ? `@ ${fmt(item.experimental_current_density_mA_cm2)} mA/cm²` : '电流密度以原文献为准',
          rows: [
            ['数值', fmt(item.experimental_overpotential_V) + ' V'],
            ['原始记录', item.experimental_overpotential_raw ? JSON.stringify(item.experimental_overpotential_raw) : '见文献'],
            ['电解液', item.electrolyte || '未提供'],
            ['pH', joinPh(item) || '未提供'],
            ['电流密度', item.experimental_current_density_mA_cm2 != null ? fmt(item.experimental_current_density_mA_cm2) + ' mA/cm²' : '未提供'],
            ['温度', item.temperature_K != null ? fmt(item.temperature_K) + ' K' : '未提供'],
            ['实验条件', item.experimental_conditions || '未提供'],
            ['条件说明', item.condition_basis || '未提供'],
            ['文献', sourceText(item) || '未提供'],
            ['DOI', (item.source || {}).doi || '未提供'],
          ],
        });
        rows.push(`<div><span>实验过电位 ${esc(etaBasis(item))}<em class="dc-inline">${esc(DATA_CLASS.experimental)}</em></span><b>${fmt(item.experimental_overpotential_V)} V</b>${testTags(item)}<small>${esc(shortCondition(item))}</small>${conditionButton(id)}</div>`);
      }
      if (Array.isArray(item.experimental_activity)) {
        item.experimental_activity.filter((a) => a && a.value != null).forEach((a, i) => {
          const id = storeCondition(item.id + ':act' + i, {
            title: '实验活性 · ' + (a.metric || ''),
            value: a.value,
            unit: a.unit || '',
            note: '数据类型：实验数据 · 固定电位下的电流密度，不是过电位，不进入过电位筛选',
            rows: [
              ['数值', fmt(a.value) + ' ' + (a.unit || '')],
              ['定义', a.definition || '未提供'],
              ['电位', a.potential_V_RHE != null ? fmt(a.potential_V_RHE) + ' V vs RHE' : '未提供'],
              ['原始字段', a.key || '未提供'],
              ['电解液', item.electrolyte || '未提供'],
              ['实验条件', item.experimental_conditions || '未提供'],
              ['条件说明', item.condition_basis || '未提供'],
              ['文献', sourceText(item) || '未提供'],
              ['DOI', (item.source || {}).doi || '未提供'],
            ],
          });
          rows.push(`<div><span>${esc(a.metric || '实验活性')}<em class="dc-inline">${esc(DATA_CLASS.experimental)}</em></span><b>${fmt(a.value)} ${esc(a.unit || '')}</b><small>${esc(shortCondition(item))}</small>${conditionButton(id)}</div>`);
        });
      }
      if (item.overpotential_V != null) {
        const id = storeCondition(item.id + ':eta', {
          title: '理论过电位（计算数据）',
          value: item.overpotential_V,
          unit: 'V',
          note: '数据类型：计算数据 · ' + (item.method_summary || '来自文献报道的第一性原理计算'),
          rows: [
            ['数值', fmt(item.overpotential_V) + ' V'],
            ['计算方法', item.method_summary || '未提供'],
            ['计算细节', item.method || '未提供'],
            ['条件说明', item.condition_basis || '未提供'],
            ['文献', sourceText(item) || '未提供'],
            ['DOI', (item.source || {}).doi || '未提供'],
          ],
        });
        rows.push(`<div><span>理论过电位<em class="dc-inline">${esc(DATA_CLASS.computed)}</em></span><b>${fmt(item.overpotential_V)} V</b><small>${esc(item.method_summary || '文献计算')}</small>${conditionButton(id)}</div>`);
      }
    } else {
      const id = storeCondition(item.id + ':eta', {
        title: '预测过电位（预测数据）',
        value: item.overpotential_V,
        unit: 'V',
        note: '数据类型：预测数据 · ' + (item.method_scope || 'UMA 估算；非逐结构振动计算'),
        rows: [
          ['η', fmt(item.overpotential_V) + ' V'],
          ['ΔG₁–ΔG₄', (item.steps_dG_eV || []).map(fmt).join(' / ') + ' eV'],
          ['限制步骤', (item.limiting_steps || []).join('、') || '未提供'],
          ['方法', item.method_name || '未提供'],
          ['温度', item.temperature_K != null ? fmt(item.temperature_K) + ' K' : '未提供'],
          ['电位', item.potential_V_RHE != null ? fmt(item.potential_V_RHE) + ' V (RHE)' : '未提供'],
          ['方法说明', item.method_scope || '未提供'],
          ['方法出处', item.method_source || '未提供'],
          ['导入批次', item.batch_name || '未提供'],
          ['复核状态', item.review_status || '未提供'],
        ],
      });
      const steps = (item.steps_dG_eV || []).map((v) => fmt(v)).join(' / ');
      rows.push(`<div><span>预测过电位 η<em class="dc-inline">${esc(DATA_CLASS.predicted)}</em></span><b>${fmt(item.overpotential_V)} V</b><small>ΔG ${esc(steps)} eV · ${esc(item.method_name || '预测计算')}</small>${conditionButton(id)}</div>`);
    }
    return rows.length ? `<div class="matching-points">${rows.join('')}</div>` : '';
  }

  function renderChips() {
    const html = [
      ...elements.map((k) => `<button type="button" data-remove-el="${esc(k)}" class="component-token">${chem(k)} ×</button>`),
      ...excludeElements.map((k) => `<button type="button" data-remove-ex="${esc(k)}" class="exclude-token">不含 ${chem(k)} ×</button>`),
    ];
    if (filters.eta) html.push(`<button type="button" data-remove-filter="eta" class="property-token">${esc(filters.eta)} ×</button>`);
    if (filters.exp) html.push(`<button type="button" data-remove-filter="exp" class="property-token">${esc(filters.exp)} ×</button>`);
    const elyte = $('oerElectrolyte').value;
    if (elyte) html.push(`<button type="button" data-remove-filter="electrolyte" class="property-token">${esc(electrolyteName(elyte))}电解液 ×</button>`);
    if (expJ != null) html.push(`<button type="button" data-remove-filter="exp_j" class="property-token">实验η电流密度 ${esc(fmt(expJ))} mA/cm² ×</button>`);
    if (dataClass) html.push(`<button type="button" data-remove-filter="data_class" class="property-token">数据类型：${esc(DATA_CLASS[dataClass] || dataClass)} ×</button>`);
    const status = $('oerStatus').value;
    if (status !== 'all') html.push(`<button type="button" data-remove-filter="status" class="property-token">${esc(statusName(status))} ×</button>`);
    $('oerChips').innerHTML = html.join('');
    $('oerElementCount').textContent = elements.length ? `已选 ${elements.length} 种` : '多选元素';
    if ($('oerSelectedPreview')) $('oerSelectedPreview').innerHTML = elements.map((k) => `<button type="button" data-remove-el="${esc(k)}">${chem(k)} ×</button>`).join('');
    const note = $('oerNote');
    if (warnings.length) {
      note.hidden = false;
      note.textContent = warnings.join(' ');
    } else {
      note.hidden = true;
      note.textContent = '';
    }
    $('oerSelectedElements').innerHTML = elements.map((k) => `<button type="button" data-element="${esc(k)}">${chem(k)} ×</button>`).join('');
  }

  function rangeText(lo, hi, unit) {
    if (lo != null && hi != null && lo === hi) return `= ${fmt(lo)} ${unit}`;
    const a = [];
    if (lo != null) a.push('≥ ' + fmt(lo));
    if (hi != null) a.push('≤ ' + fmt(hi));
    return a.join(' 且 ') + ' ' + unit;
  }

  function applyParsed(d) {
    elements = [...new Set(d.elements || [])];
    excludeElements = [...new Set(d.exclude_elements || [])];
    warnings = d.warnings || [];
    $('oerQuery').value = d.q || '';
    $('oerMatch').value = d.match === 'exact' ? 'exact' : 'contains';
    $('oerStatus').value = d.status || 'all';
    $('oerElectrolyte').value = d.electrolyte || '';
    $('oerCorpus').value = d.corpus || 'all';
    $('oerEtaLo').value = d.eta_min ?? '';
    $('oerEtaHi').value = d.eta_max ?? '';
    $('oerExpEtaLo').value = d.exp_eta_min ?? '';
    $('oerExpEtaHi').value = d.exp_eta_max ?? '';
    $('oerPhLo').value = d.pH_min ?? '';
    $('oerPhHi').value = d.pH_max ?? '';
    filters.eta = (d.eta_min != null || d.eta_max != null) ? '理论过电位 ' + rangeText(d.eta_min, d.eta_max, 'V') : null;
    filters.exp = (d.exp_eta_min != null || d.exp_eta_max != null) ? '实验过电位 ' + rangeText(d.exp_eta_min, d.exp_eta_max, 'V') : null;
    expJ = d.exp_j ?? null;
    dataClass = d.data_class || '';
    setSelect('oerExpJ', expJ == null ? '' : String(expJ));
    setSelect('oerDataClass', dataClass);
    if (d.corpus === 'computed') view = 'computed';
    else if (d.corpus === 'literature' || d.status === 'experimentally_verified' || d.electrolyte || d.exp_eta_min != null || d.exp_eta_max != null) view = 'literature';
  }

  function setSelect(id, value) {
    const el = $(id);
    if (!el) return;
    if (value !== '' && ![...el.options].some((o) => o.value === value)) el.add(new Option(`${fmt(value)} mA/cm²`, value));
    el.value = value;
  }

  function reveal() {
    $('catalysis').classList.remove('search-home');
  }

  function renderList(data) {
    const pack = view === 'computed' ? data.computed : data.literature;
    total = pack.total;
    const label = view === 'computed' ? '条预测构型' : '条文献与实验材料';
    $('oerFound').textContent = `共 ${fmt(total)} ${label}`;
    $('oerListTitle').textContent = view === 'computed' ? '预测构型检索结果' : '文献与实验材料';
    $('oerViewLabel').textContent = view === 'computed' ? '计算预测' : '文献数据';
    setTabs();
    $('oerResults').classList.remove('volcano-mode');
    $('oerPager').hidden = false;
    if (!pack.items.length) {
      $('oerResults').innerHTML = `<div class="no-results"><strong>没有找到符合条件的材料</strong>${emptyReactionHint()}</div>`;
    } else if (view === 'computed') {
      $('oerResults').innerHTML = `<div class="oer-list"><div class="oer-group-head dc-${esc('predicted')}">${esc(groupHeader({ kind: 'computed' }))}</div>${pack.items.map((x) => `
        <div class="oer-row" role="button" tabindex="0" data-oer-computed="${esc(x.id)}">
          <span class="oer-row-name">${chem(x.formula)}${x.variant ? `<small>${esc(x.variant)}</small>` : ''}${classTag({ ...x, kind: 'computed' })}</span>
          <span class="oer-row-eta">${etaCell(x)}</span>
        </div>`).join('')}</div>`;
    } else {
      // Rows arrive grouped by 数据类型 and measurement basis; label each group, never interleave.
      let last = null;
      $('oerResults').innerHTML = `<div class="oer-list">${pack.items.map((x) => {
        const head = groupHeader(x);
        const sep = head !== last ? `<div class="oer-group-head dc-${esc(dataClassOf(x))}">${esc(head)}</div>` : '';
        last = head;
        return `${sep}
        <div class="oer-row" role="button" tabindex="0" data-oer-lit="${esc(x.id)}">
          <span class="oer-row-name">${rowName(x)}${classTag(x)}${reactionTag(x)}<small class="status-tag">${esc(x.status_label || statusName(x.experimental_status))}</small></span>
          <span class="oer-row-eta">${etaCell(x)}</span>
        </div>`;
      }).join('')}</div>`;
    }
    $('oerPage').textContent = `${total ? Math.floor(offset / limit) + 1 : 0} / ${Math.max(1, Math.ceil(total / limit))} 页`;
    $('oerPrev').disabled = !offset;
    $('oerNext').disabled = offset + limit >= total;
  }

  function setTabs() {
    $('oerTabLit').classList.toggle('on', view === 'literature');
    $('oerTabComp').classList.toggle('on', view === 'computed');
    $('oerTabVolcano').classList.toggle('on', view === 'volcano');
  }

  function volcanoParams() {
    const p = new URLSearchParams({
      q: $('oerQuery').value.trim(),
      reaction: selectedReactionParam(),
      elements: elements.join(','),
      exclude_elements: excludeElements.join(','),
      match: $('oerMatch').value,
    });
    if ($('oerEtaLo').value !== '') p.set('eta_min', $('oerEtaLo').value);
    if ($('oerEtaHi').value !== '') p.set('eta_max', $('oerEtaHi').value);
    return p;
  }

  function niceTicks(lo, hi, count) {
    const span = hi - lo;
    const raw = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => span / v <= count) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    return out;
  }

  let volcanoPoints = [];

  function renderVolcano(d) {
    setTabs();
    volcanoPoints = d.points || [];
    $('oerViewLabel').textContent = '计算预测';
    $('oerListTitle').textContent = '活性火山图';
    $('oerFound').textContent = `共 ${fmt(d.eligible)} 个构型${d.sampled ? `，显示 ${fmt(d.shown)} 个` : ''}`;
    $('oerPager').hidden = true;
    $('oerResults').classList.add('volcano-mode');
    if (!volcanoPoints.length) {
      $('oerResults').innerHTML = `<div class="no-results"><strong>没有可绘制的构型</strong>${d.excluded_outliers ? `已排除 ${fmt(d.excluded_outliers)} 个 |ΔG|>6 eV 的构型` : ''}</div>`;
      return;
    }
    const W = 960, H = 560, L = 74, R = 24, T = 40, B = 64;
    const xs = volcanoPoints.map((p) => p.x), ys = volcanoPoints.map((p) => p.eta);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    const pad = Math.max(0.1, (x1 - x0) * 0.04);
    x0 -= pad; x1 += pad;
    const y0 = 0, y1 = Math.max(...ys) * 1.05 + 0.05;
    const sx = (v) => L + (v - x0) / (x1 - x0) * (W - L - R);
    const sy = (v) => H - B - (v - y0) / (y1 - y0) * (H - T - B);
    const xt = niceTicks(x0, x1, 8), yt = niceTicks(y0, y1, 6);
    const grid = [
      ...xt.map((v) => `<line x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H - B}" class="vg"/><text x="${sx(v)}" y="${H - B + 24}" text-anchor="middle">${v}</text>`),
      ...yt.map((v) => `<line x1="${L}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}" class="vg"/><text x="${L - 10}" y="${sy(v) + 5}" text-anchor="end">${v}</text>`),
    ].join('');
    // Ideal-scaling reference: ΔG_OOH* = ΔG_OH* + 3.2 eV → η = max(x, 3.2 − x) − 1.23
    const ref = [];
    for (let i = 0; i <= 120; i++) {
      const x = x0 + (x1 - x0) * i / 120;
      const y = Math.max(x, 3.2 - x) - 1.23;
      if (y >= y0 && y <= y1) ref.push(`${sx(x).toFixed(1)},${sy(y).toFixed(1)}`);
    }
    // Colour by 数据类型; different types are drawn as separate groups (currently all UMA = 预测数据).
    const classes = [...new Set(volcanoPoints.map((p) => p.data_class || 'predicted'))];
    const dots = classes.map((cls) => `<g class="dc-group" data-class="${esc(cls)}">${volcanoPoints.map((p, i) => ((p.data_class || 'predicted') === cls
      ? `<circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.eta).toFixed(1)}" r="3.4" data-i="${i}" style="fill:${DATA_CLASS_COLOR[cls] || '#087c80'};stroke:${DATA_CLASS_COLOR[cls] || '#087c80'}"/>` : '')).join('')}</g>`).join('');
    const legend = classes.map((cls, k) => `<g transform="translate(${L + 14} ${T + 14 + k * 22})"><circle r="5" cx="0" cy="-5" style="fill:${DATA_CLASS_COLOR[cls] || '#087c80'};fill-opacity:.7"/><text x="12" y="0" class="vlegend">${esc(DATA_CLASS[cls] || cls)}${cls === 'predicted' ? '（UMA 推理）' : ''}</text></g>`).join('');
    const note = `数据类型：${classes.map((c) => DATA_CLASS[c] || c).join('、')} · 已排除 ${fmt(d.excluded_outliers)} 个 |ΔG|>6 eV 的构型${d.sampled ? ` · 显示 ${fmt(d.shown)} / ${fmt(d.eligible)}` : ''}`;
    $('oerResults').innerHTML = `<div class="volcano-wrap">
      <svg class="volcano" viewBox="0 0 ${W} ${H}" role="img" aria-label="OER 活性火山图">
        <g class="axis">${grid}<line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" class="va"/><line x1="${L}" x2="${L}" y1="${T}" y2="${H - B}" class="va"/></g>
        <polyline class="vref" points="${ref.join(' ')}"/>
        <g class="dots">${dots}</g>
        <g class="legend">${legend}</g>
        <text class="vlabel" x="${(L + W - R) / 2}" y="${H - 14}" text-anchor="middle">ΔG(O*) − ΔG(OH*) / eV</text>
        <text class="vlabel" transform="translate(20 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">预测过电位 η / V（预测数据）</text>
        <text class="vnote" x="${W - R}" y="${T - 14}" text-anchor="end">${esc(note)}</text>
      </svg>
      <div id="oerVolcanoTip" class="volcano-tip" hidden></div>
    </div>`;
  }

  async function loadVolcano(n) {
    const d = await get('/api/oer/volcano?' + volcanoParams());
    if (n !== seq) return;
    $('oerResults').style.opacity = '1';
    renderVolcano(d);
  }

  async function search() {
    reveal();
    const n = ++seq;
    $('oerError').hidden = true;
    $('oerResults').style.opacity = '.45';
    $('oerFound').textContent = '正在查询本地数据…';
    try {
      if (view === 'volcano') { await loadVolcano(n); return; }
      const data = await get('/api/oer/search?' + params());
      if (n !== seq) return;
      $('oerResults').style.opacity = '1';
      if ($('oerCorpus').value === 'computed') view = 'computed';
      else if ($('oerCorpus').value === 'literature') view = 'literature';
      renderList(data);
    } catch (e) {
      if (n !== seq) return;
      $('oerResults').style.opacity = '1';
      $('oerError').hidden = false;
      $('oerError').textContent = e.message || String(e);
      $('oerFound').textContent = '查询未完成，请调整条件后重试';
    }
  }

  async function runIntent() {
    if (busy) return;
    busy = true;
    $('oerSubmit').disabled = true;
    $('oerSubmit').textContent = '正在查找…';
    $('oerError').hidden = true;
    $('oerSuggestions').hidden = true;
    try {
      const d = await get('/api/oer/interpret?' + new URLSearchParams({ text: $('oerIntent').value }));
      applyParsed(d);
      offset = 0;
      renderChips();
      lastIntent = $('oerIntent').value;
      if (d.warnings.length) {
        $('catalysis').classList.add('advanced-open');
        throw new Error('有条件尚未识别完整：' + d.warnings.join(' '));
      }
      await search();
    } catch (e) {
      $('oerError').hidden = false;
      $('oerError').textContent = e.message || String(e);
    } finally {
      busy = false;
      $('oerSubmit').disabled = false;
      $('oerSubmit').innerHTML = '搜索材料 <span>→</span>';
    }
  }

  function renderElementGrid() {
    if (!summary) return;
    const needle = $('oerElementQuery').value.trim().toLowerCase();
    const lit = (summary.literature && summary.literature.elements) || [];
    const comp = (summary.computed && summary.computed.elements) || [];
    const all = [...new Set([...(summary.elements || []), ...lit, ...comp])];
    const shown = all.filter((s) => !needle || s.toLowerCase().includes(needle));
    $('oerElementGrid').innerHTML = shown.length
      ? `<section class="ingredient-section"><h3>元素 <small>${shown.length} 种</small></h3><div class="ingredient-tiles">${shown.map((s) => `<button type="button" class="ingredient-tile atomic ${elements.includes(s) ? 'selected' : ''}" data-element="${esc(s)}" aria-pressed="${elements.includes(s)}"><strong>${esc(s)}</strong><span>元素</span><i aria-hidden="true">${elements.includes(s) ? '✓' : '+'}</i></button>`).join('')}</div></section>`
      : '<div class="no-results">没有匹配的元素。</div>';
    $('oerElementStatus').textContent = `已选 ${elements.length} 种元素`;
    renderChips();
  }

  function toggleElement(symbol) {
    if (elements.includes(symbol)) elements = elements.filter((x) => x !== symbol);
    else elements.push(symbol);
    renderElementGrid();
  }

  function pretty(value) {
    if (value == null || value === '') return '未提供';
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
  }

  function showCondition(id) {
    const p = conditions.get(id);
    if (!p) return;
    $('oerConditionContent').innerHTML = `<div class="condition-lead"><h3>${esc(p.title)}</h3><div class="property-value">${fmt(p.value)} <small>${esc(p.unit || '')}</small></div><p class="hint">${esc(p.note || '')}</p></div>${(p.rows || []).map(([k, v]) => `<div class="condition-definition"><span>${esc(k)}</span><span>${esc(pretty(v))}</span></div>`).join('')}`;
    $('oerConditionModal').showModal();
  }

  function methodLines(method) {
    if (!method) return [['计算方法', '未提供']];
    if (typeof method !== 'object') return [['计算方法', method]];
    return Object.entries(method).map(([k, v]) => [k, v]);
  }

  async function showLiterature(id) {
    $('oerDetail').innerHTML = '<p class="hint">正在读取材料详情…</p>';
    $('oerDetailTitle').textContent = '材料详情';
    $('oerDetailModal').showModal();
    try {
      const d = await get('/api/literature/materials/' + encodeURIComponent(id));
      const card = {
        ...d,
        kind: 'literature',
        status_label: statusName(d.experimental_status),
        method_summary: (function () {
          const m = d.method;
          if (!m) return '';
          if (typeof m === 'string') return m;
          if (typeof m === 'object') return [m.type, m.code, m.functional, m.environment].filter(Boolean).join(' · ');
          return String(m);
        })(),
        source: d.source || {},
      };
      const src = d.source || {};
      const href = src.url || doiHref(src.doi);
      const exp = d.experimental_conditions || {};
      const states = Object.entries(d.states || {}).filter(([, st]) => st && st.available).map(([s]) => s);
      $('oerDetailTitle').textContent = d.name || d.formula || '材料详情';
      $('oerDetail').innerHTML = `
        <div class="detail-overview">
          <section class="detail-section">
            <h3 class="section-title">组成与状态</h3>
            <p class="composition-line">化学式 ${chem(d.formula || '未提供')}</p>
            <p>${(d.elements || []).map((e) => `<span class="pill">${esc(e)}</span>`).join('')}</p>
            <p>${card.reaction_label || card.reaction ? `<span class="pill">${esc(card.reaction_label || card.reaction)}</span>` : ''}<span class="pill dc-pill dc-${esc(dataClassOf(card))}">数据类型：${esc(dataClassLabel(card))}</span>${card.measurement_basis ? `<span class="pill neutral">${esc(card.measurement_basis)}</span>` : ''}<span class="pill">${esc(statusName(d.experimental_status))}</span><span class="pill neutral">${esc(d.data_status_label || d.completeness || '')}</span>${states.length ? structureLink('literature', id) : ''}</p>
            ${propertyRows(card)}
          </section>
          <section class="detail-section">
            <h3 class="section-title">文献出处</h3>
            <div class="ref-block">
              <div class="material-name">${esc(src.title || '来源信息待补充')}</div>
              <div>${src.doi ? 'DOI：' + esc(src.doi) : 'DOI 未提供'}</div>
              ${src.license ? `<div>许可：${esc(src.license)}</div>` : ''}
              ${href ? `<p><a href="${esc(href)}" target="_blank" rel="noopener">查看原文 ↗</a></p>` : ''}
              ${src.notes ? `<p class="detail-note">${esc(src.notes)}</p>` : ''}
            </div>
          </section>
        </div>
        <section class="detail-section">
          <h3 class="section-title">实验 / 测试条件</h3>
          ${[
            ['电解液', d.electrolyte],
            ['电解液类型', (d.electrolyte_conditions || []).filter((x) => typeof x === 'string').map(electrolyteName).join('、')],
            ['pH', joinPh(card) || '未提供'],
            ['数据类型', dataClassLabel(card)],
            ['测量口径', card.measurement_basis || '未提供'],
            ['电流密度（实验过电位对应）', d.experimental_current_density_mA_cm2 != null ? fmt(d.experimental_current_density_mA_cm2) + ' mA/cm²' : '未提供'],
            ['温度', (exp.temperature_K ?? d.temperature_K) != null ? fmt(exp.temperature_K ?? d.temperature_K) + ' K' : '未提供'],
            ['催化剂载量', exp.catalyst_loading_mg_cm2 != null ? fmt(exp.catalyst_loading_mg_cm2) + ' mg/cm²' : '未提供'],
            ['LSV 扫速', exp.LSV_scan_rate_mV_s != null ? fmt(exp.LSV_scan_rate_mV_s) + ' mV/s' : '未提供'],
            ['电解液组成', exp.electrolyte_identity],
            ['质子浓度', exp.proton_concentration_M != null ? fmt(exp.proton_concentration_M) + ' M' : '未提供'],
            ['条件说明', d.condition_basis],
            ['备注', d.notes],
          ].map(([k, v]) => `<div class="condition-definition"><span>${esc(k)}</span><span>${esc(pretty(v || '未提供'))}</span></div>`).join('')}
        </section>
        <section class="detail-section">
          <h3 class="section-title">计算方法</h3>
          ${methodLines(d.method).map(([k, v]) => `<div class="condition-definition"><span>${esc(k)}</span><span>${esc(pretty(v))}</span></div>`).join('')}
        </section>
        <section class="detail-section">
          <h3 class="section-title">结构</h3>
          ${states.length ? `<p>${states.map((st) => `<a href="/api/literature/materials/${esc(encodeURIComponent(id))}/structures/${esc(st)}/download">${esc(st)} 结构 ↓</a>`).join('&nbsp;&nbsp; ')}</p>` : '<p class="detail-note">未提供结构</p>'}
        </section>`;
    } catch (e) {
      $('oerDetail').innerHTML = `<p class="oer-error">${esc(e.message || e)}</p>`;
    }
  }

  async function showComputed(id) {
    $('oerDetail').innerHTML = '<p class="hint">正在读取构型详情…</p>';
    $('oerDetailTitle').textContent = '构型详情';
    $('oerDetailModal').showModal();
    try {
      const d = await get('/api/configurations/' + encodeURIComponent(id));
      const method = d.method || {};
      const card = {
        kind: 'computed',
        id: d.id,
        overpotential_V: d.eta,
        steps_dG_eV: [d.g1, d.g2, d.g3, d.g4],
        limiting_steps: d.limiting_steps,
        method_name: method.name,
        method_scope: method.scope,
        temperature_K: method.temperature_K,
        potential_V_RHE: method.potential_V_RHE,
        method_source: method.source,
        batch_name: d.batch_name,
        review_status: d.review_status,
      };
      const states = Object.keys(d.structures || {});
      $('oerDetailTitle').textContent = d.formula || '构型详情';
      $('oerDetail').innerHTML = `
        <div class="detail-overview">
          <section class="detail-section">
            <h3 class="section-title">组成与位点</h3>
            <p class="material-name">${chem(d.formula || '')}</p>
            <p class="composition-line">完整化学式 ${esc(d.formula_full || d.formula)} · 位点 ${esc(d.site_id || '未提供')}</p>
            <p>${(d.elements || []).map((e) => `<span class="pill">${esc(e)}</span>`).join('')}${states.length ? structureLink('computed', id) : ''}</p>
            <p><span class="pill dc-pill dc-predicted">数据类型：${esc(DATA_CLASS.predicted)}</span><span class="pill neutral">UMA 推理，非 DFT、非实验</span></p>
            ${propertyRows(card)}
          </section>
          <section class="detail-section">
            <h3 class="section-title">计算条件与出处</h3>
            <div class="ref-block">
              <div>${esc(method.name || '方法未提供')}</div>
              <p class="detail-note">${esc(method.scope || '')}</p>
              ${method.source ? `<p><a href="${esc(method.source)}" target="_blank" rel="noopener">方法文献 ↗</a></p>` : ''}
            </div>
          </section>
        </div>
        <section class="detail-section">
          <h3 class="section-title">计算与测试口径</h3>
          ${[
            ['温度', method.temperature_K != null ? fmt(method.temperature_K) + ' K' : '未提供'],
            ['电位', method.potential_V_RHE != null ? fmt(method.potential_V_RHE) + ' V (RHE)' : '未提供'],
            ['导入批次', d.batch_name],
            ['复核状态', d.review_status],
            ['限制步骤', (d.limiting_steps || []).join('、')],
          ].map(([k, v]) => `<div class="condition-definition"><span>${esc(k)}</span><span>${esc(pretty(v || '未提供'))}</span></div>`).join('')}
        </section>
        <section class="detail-section">
          <h3 class="section-title">四态结构</h3>
          ${states.length ? '' : '<p class="detail-note">未提供结构</p>'}
          <p>${states.map((s) => `<a href="/api/configurations/${esc(encodeURIComponent(id))}/structures/${esc(s)}/download">${esc(s)} 结构 ↓</a>&nbsp;&nbsp;`).join('')}</p>
        </section>`;
    } catch (e) {
      $('oerDetail').innerHTML = `<p class="oer-error">${esc(e.message || e)}</p>`;
    }
  }

  function exportUrl() {
    if (view === 'computed' || view === 'volcano') {
      const p = new URLSearchParams();
      if (elements.length) p.set('elements', elements.join(','));
      p.set('match', $('oerMatch').value);
      if ($('oerEtaLo').value !== '') p.set('eta_min', $('oerEtaLo').value);
      if ($('oerEtaHi').value !== '') p.set('eta_max', $('oerEtaHi').value);
      return '/api/export/csv?' + p;
    }
    const p = params();
    p.delete('corpus'); p.delete('offset'); p.delete('limit');
    return '/api/literature/export/csv?' + p;
  }

  function resetAll() {
    elements = [];
    excludeElements = [];
    filters = { eta: null, exp: null };
    expJ = null;
    dataClass = '';
    if ($('oerExpJ')) $('oerExpJ').value = '';
    if ($('oerDataClass')) $('oerDataClass').value = '';
    warnings = [];
    offset = 0;
    view = 'literature';
    document.querySelectorAll('#oerReactionMenu input[name="oerReaction"]').forEach((el) => { el.checked = el.value === 'OER'; });
    updateReactionLabel();
    $('oerIntent').value = '';
    $('oerQuery').value = '';
    $('oerMatch').value = 'contains';
    $('oerStatus').value = 'all';
    $('oerElectrolyte').value = '';
    $('oerSource').value = '';
    $('oerCompleteness').value = 'all';
    $('oerCorpus').value = 'all';
    for (const id of ['oerEtaLo', 'oerEtaHi', 'oerExpEtaLo', 'oerExpEtaHi', 'oerPhLo', 'oerPhHi']) $(id).value = '';
    $('catalysis').classList.add('search-home');
    renderChips();
    $('oerResults').innerHTML = '';
    $('oerFound').textContent = '输入条件后检索本地数据';
  }

  $('oerEngineForm').onsubmit = (e) => { e.preventDefault(); runIntent(); };
  $('oerIntent').onfocus = () => { $('oerSuggestions').hidden = false; };
  $('oerIntent').onkeydown = (e) => { if (e.key === 'Escape') { $('oerSuggestions').hidden = true; setReactionMenu(false); } };
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#oerEngineWrap')) $('oerSuggestions').hidden = true;
    if (!e.target.closest('#oerReactionPicker')) setReactionMenu(false);
  });
  if ($('oerReactionBtn')) {
    $('oerReactionBtn').onclick = (e) => { e.preventDefault(); setReactionMenu($('oerReactionMenu').hidden); };
  }
  if ($('oerReactionMenu')) {
    $('oerReactionMenu').addEventListener('change', async (e) => {
      const box = e.target.closest('input[name="oerReaction"]');
      if (!box) return;
      if (!selectedReactions().length) { box.checked = true; return; }
      updateReactionLabel();
      offset = 0;
      try { await refreshSummary(); } catch (err) {
        $('oerError').hidden = false;
        $('oerError').textContent = err.message || String(err);
      }
      if (!$('catalysis').classList.contains('search-home')) search();
    });
  }
  $('oerSuggestions').onclick = (e) => {
    const b = e.target.closest('[data-example]');
    if (b) { $('oerIntent').value = b.dataset.example; runIntent(); }
  };
  $('oerOpenElements').onclick = $('oerOpenElementsAside').onclick = () => {
    renderElementGrid();
    $('oerElementModal').showModal();
    $('oerElementQuery').focus({ preventScroll: true });
  };
  $('oerCloseElements').onclick = () => $('oerElementModal').close();
  $('oerElementQuery').oninput = renderElementGrid;
  $('oerElementGrid').onclick = $('oerSelectedElements').onclick = (e) => {
    const b = e.target.closest('[data-element]');
    if (b) toggleElement(b.dataset.element);
  };
  $('oerClearElements').onclick = () => { elements = []; renderElementGrid(); };
  $('oerApplyElements').onclick = () => { $('oerElementModal').close(); offset = 0; renderChips(); search(); };
  if ($('oerSelectedPreview')) $('oerSelectedPreview').onclick = (e) => {
    const b = e.target.closest('[data-remove-el]');
    if (!b) return;
    elements = elements.filter((k) => k !== b.dataset.removeEl);
    offset = 0; renderChips(); search();
  };
  $('oerChips').onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.removeEl) elements = elements.filter((k) => k !== b.dataset.removeEl);
    if (b.dataset.removeEx) excludeElements = excludeElements.filter((k) => k !== b.dataset.removeEx);
    if (b.dataset.removeFilter === 'eta') { filters.eta = null; $('oerEtaLo').value = ''; $('oerEtaHi').value = ''; }
    if (b.dataset.removeFilter === 'exp') { filters.exp = null; $('oerExpEtaLo').value = ''; $('oerExpEtaHi').value = ''; }
    if (b.dataset.removeFilter === 'electrolyte') $('oerElectrolyte').value = '';
    if (b.dataset.removeFilter === 'status') $('oerStatus').value = 'all';
    if (b.dataset.removeFilter === 'exp_j') { expJ = null; if ($('oerExpJ')) $('oerExpJ').value = ''; }
    if (b.dataset.removeFilter === 'data_class') { dataClass = ''; if ($('oerDataClass')) $('oerDataClass').value = ''; }
    offset = 0; renderChips(); search();
  };
  $('oerAdvancedToggle').onclick = () => { $('catalysis').classList.toggle('advanced-open'); reveal(); };
  $('oerBrowse').onclick = () => { resetAll(); $('catalysis').classList.remove('search-home'); offset = 0; view = 'literature'; search(); };
  $('oerReset').onclick = () => resetAll();
  $('oerApply').onclick = () => {
    filters.eta = ($('oerEtaLo').value !== '' || $('oerEtaHi').value !== '') ? '理论过电位 ' + rangeText($('oerEtaLo').value || null, $('oerEtaHi').value || null, 'V') : null;
    filters.exp = ($('oerExpEtaLo').value !== '' || $('oerExpEtaHi').value !== '') ? '实验过电位 ' + rangeText($('oerExpEtaLo').value || null, $('oerExpEtaHi').value || null, 'V') : null;
    offset = 0; renderChips(); search();
  };
  $('oerTabLit').onclick = () => { view = 'literature'; offset = 0; if ($('oerCorpus').value === 'computed') $('oerCorpus').value = 'all'; search(); };
  $('oerTabComp').onclick = () => { view = 'computed'; offset = 0; if ($('oerCorpus').value === 'literature') $('oerCorpus').value = 'all'; search(); };
  $('oerTabVolcano').onclick = () => { view = 'volcano'; offset = 0; if ($('oerCorpus').value === 'literature') $('oerCorpus').value = 'all'; search(); };
  $('oerPrev').onclick = () => { if (offset >= limit) { offset -= limit; search(); } };
  $('oerNext').onclick = () => { offset += limit; search(); };
  $('oerExport').onclick = () => { window.location = exportUrl(); };
  $('oerResults').onkeydown = (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const row = e.target.closest('.oer-row');
    if (!row) return;
    e.preventDefault();
    row.click();
  };
  $('oerResults').onmouseover = (e) => {
    const c = e.target.closest('circle[data-i]');
    const tip = $('oerVolcanoTip');
    if (!tip) return;
    if (!c) { tip.hidden = true; return; }
    const p = volcanoPoints[Number(c.dataset.i)];
    if (!p) return;
    tip.innerHTML = `<b>${chem(p.formula)}</b>${p.variant ? ` <small>${esc(p.variant)}</small>` : ''}<br>η ${fmt(p.eta)} V · ΔG(O*)−ΔG(OH*) ${fmt(p.x)} eV<br><small>数据类型：${esc(DATA_CLASS[p.data_class || 'predicted'])}${(p.data_class || 'predicted') === 'predicted' ? '（UMA 推理）' : ''}</small>`;
    const wrap = c.closest('.volcano-wrap').getBoundingClientRect();
    const r = c.getBoundingClientRect();
    tip.hidden = false;
    const left = Math.min(r.left - wrap.left + 12, wrap.width - tip.offsetWidth - 4);
    tip.style.left = Math.max(4, left) + 'px';
    tip.style.top = Math.max(4, r.top - wrap.top - tip.offsetHeight - 8) + 'px';
  };
  $('oerResults').onmouseleave = () => { if ($('oerVolcanoTip')) $('oerVolcanoTip').hidden = true; };
  $('oerResults').onclick = (e) => {
    const dot = e.target.closest('circle[data-i]');
    if (dot) { const p = volcanoPoints[Number(dot.dataset.i)]; if (p) showComputed(p.id); return; }
    const cond = e.target.closest('[data-oer-condition]');
    if (cond) { showCondition(cond.dataset.oerCondition); return; }
    const lit = e.target.closest('[data-oer-lit]');
    if (lit) { showLiterature(lit.dataset.oerLit); return; }
    const comp = e.target.closest('[data-oer-computed]');
    if (comp) showComputed(comp.dataset.oerComputed);
  };
  $('oerDetail').onclick = (e) => {
    const cond = e.target.closest('[data-oer-condition]');
    if (cond) showCondition(cond.dataset.oerCondition);
  };
  $('oerCloseDetail').onclick = () => $('oerDetailModal').close();
  $('oerCloseCondition').onclick = () => $('oerConditionModal').close();

  (async () => {
    try {
      updateReactionLabel();
      await refreshSummary();
    } catch (e) {
      $('oerError').hidden = false;
      $('oerError').textContent = e.message || String(e);
    }
  })();
})();

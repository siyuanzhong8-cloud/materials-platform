{
  const byId = id => document.getElementById(id);
  const molecules = [
    {
      name: 'APTES · 3-氨丙基三乙氧基硅烷', cas: '919-30-2', group: '氨基',
      source: ['OECD SIDS §2.1–2.2', 'https://hpvchemicals.oecd.org/UI/handler.axd?id=058FED27-DCE5-4072-B06B-EDD4F406557E'],
      values: [
        ['熔点', '−70 °C', '报告值，方法未注明'],
        ['沸点', '223 °C', '1013 hPa，计算值'],
        ['沸点', '217 °C', '101.3 kPa，手册引用值']
      ]
    },
    {
      name: 'GPTMS · 3-缩水甘油醚氧基丙基三甲氧基硅烷', cas: '2530-83-8', group: '环氧基',
      source: ['Evonik GLYMO 技术表', 'https://products.evonik.com/assets/n_/us/Dynasylan_GLYMO_EN_TDS_GEN_52000823_en_US.pdf'],
      values: [
        ['沸点下限', '90 °C', '0.7 hPa，DIN 51356'],
        ['密度', '1.070 g/cm³', '20 °C，DIN 51757'],
        ['闪点下限', '122 °C', 'DIN EN ISO 2719'],
        ['折射率', '1.429', '20 °C，D 线，DIN 51423'],
        ['动力黏度', '3.65 mPa·s', '20 °C，DIN 53015']
      ]
    },
    {
      name: 'MPS · 3-甲基丙烯酰氧基丙基三甲氧基硅烷', cas: '2530-85-0', group: '甲基丙烯酸酯基',
      source: ['Shin-Etsu KBM-503', 'https://www.shinetsusilicone-global.com/guide/silanecoup/kbm-503/'],
      values: [
        ['沸点', '255 °C', '厂家数据，压力未注明'],
        ['比重', '1.04', '厂家数据，温度未注明'],
        ['折射率', '1.429', '厂家数据，温度及谱线未注明'],
        ['闪点', '125 °C', '厂家数据，方法未注明']
      ]
    }
  ];

  const verified = new Map(['13521', '17317', '17318'].map((cid, i) => [cid, molecules[i]]));
  const selected = new Set();
  const size = 12;
  let records = [];
  let page = 0;
  let loaded = false;
  let loading = false;

  function appendText(parent, tag, value, css = '') {
    const element = document.createElement(tag);
    element.className = css;
    element.textContent = value;
    parent.append(element);
    return element;
  }

  function showDetails(row, card) {
    const entry = verified.get(row.CID);
    const source = ['PubChem CID ' + row.CID, row.PubChemURL];
    byId('silane-name').textContent = entry?.name ?? row.IUPACName;
    byId('silane-meta').textContent = row.MolecularFormula + ' · CID ' + row.CID +
      (entry ? ' · CAS ' + entry.cas : ' · 偶联用途待核实');
    const properties = [
      ['分子量', row.MolecularWeight + ' g/mol', '计算描述符'],
      ['TPSA', row.TPSA + ' Å²', '计算描述符'],
      ['SMILES', row.SMILES, '结构标识']
    ].map(fields => [...fields, source]);
    if (entry) properties.push(...entry.values.map(fields => [...fields, entry.source]));
    const tbody = byId('silane-values');
    tbody.replaceChildren();
    for (const [label, value, condition, citation] of properties) {
      const tr = tbody.insertRow();
      for (const field of [label, value, condition]) tr.insertCell().textContent = field;
      const link = appendText(tr.insertCell(), 'a', citation[0]);
      link.href = citation[1];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
    byId('silane-directory').hidden = true;
    byId('silane-detail').hidden = false;
    byId('silane-back').onclick = () => {
      byId('silane-detail').hidden = true;
      byId('silane-directory').hidden = false;
      card.focus();
    };
    byId('silane-name').focus();
  }

  function render() {
    if (!loaded) return;
    const query = byId('silane-query').value.trim().toLowerCase();
    const matches = records.filter(row => {
      const entry = verified.get(row.CID);
      return [...selected].every(symbol => row.elements.has(symbol)) &&
        [row.CID, row.IUPACName, row.MolecularFormula, row.FunctionalGroups, entry?.cas, entry?.name]
          .some(value => value?.toLowerCase().includes(query));
    });
    const pages = Math.ceil(matches.length / size);
    byId('silane-selection').textContent = selected.size ? '同时包含 ' + [...selected].join('、') : '点击元素筛选';
    byId('silane-count').textContent = pages ? matches.length + ' 种分子 · 第 ' + (page + 1) + ' / ' + pages + ' 页' : '没有匹配分子';
    byId('silane-prev').disabled = page === 0;
    byId('silane-next').disabled = page + 1 >= pages;
    const results = byId('silane-results');
    results.replaceChildren();
    for (const row of matches.slice(page * size, (page + 1) * size)) {
      const entry = verified.get(row.CID);
      const card = appendText(results, 'button', '', 'silane-card');
      appendText(card, 'span', row.MolecularFormula, 'silane-formula');
      appendText(card, 'span', entry?.name ?? row.IUPACName, 'silane-compound');
      appendText(card, 'span', row.FunctionalGroups, 'silane-cas');
      appendText(card, 'span', entry ? '查看结构与物性 →' : '查看分子详情 →', 'silane-action');
      card.onclick = () => showDetails(row, card);
    }
  }

  function refresh() { page = 0; render(); }

  async function load() {
    if (loaded || loading) return;
    loading = true;
    byId('silane-count').textContent = '正在加载分子…';
    try {
      const response = await fetch('/pubchem-silanes.json');
      if (!response.ok) throw new Error(response.status);
      records = (await response.json()).map(row => ({
        ...row, elements: new Set(row.MolecularFormula.match(/[A-Z][a-z]?/g))
      }));
      const elements = new Set(records.flatMap(row => [...row.elements]));
      const positions = {H:[1,1],B:[13,2],C:[14,2],N:[15,2],O:[16,2],F:[17,2],Si:[14,3],P:[15,3],S:[16,3],Cl:[17,3],Br:[17,4],I:[17,5]};
      for (const [symbol, [column, period]] of Object.entries(positions)) {
        if (!elements.has(symbol)) continue;
        const button = appendText(byId('silane-elements'), 'button', symbol, 'secondary');
        button.style.gridArea = period + ' / ' + column;
        button.setAttribute('aria-pressed', 'false');
        button.onclick = () => {
          if (!selected.delete(symbol)) selected.add(symbol);
          button.setAttribute('aria-pressed', String(selected.has(symbol)));
          refresh();
        };
      }
      loaded = true;
      render();
    } catch {
      byId('silane-count').textContent = '加载失败，请重新点击硅烷偶联剂。';
    } finally {
      loading = false;
    }
  }

  function showSilane() {
    for (const id of ['cat', 'gla']) byId(id).classList.remove('active');
    for (const id of ['catalysis', 'glass']) byId(id).hidden = true;
    byId('silane-tab').classList.add('active');
    byId('silane').hidden = false;
    location.hash = 'silane';
    load();
  }

  for (const id of ['cat', 'gla']) {
    byId(id).addEventListener('click', () => {
      byId('silane-tab').classList.remove('active');
      byId('silane').hidden = true;
    });
  }
  byId('silane-tab').onclick = showSilane;
  byId('silane-query').oninput = refresh;
  byId('silane-reset').onclick = () => {
    selected.clear();
    byId('silane-query').value = '';
    for (const button of byId('silane-elements').children) button.setAttribute('aria-pressed', 'false');
    refresh();
  };
  byId('silane-prev').disabled = byId('silane-next').disabled = true;
  byId('silane-prev').onclick = () => { page--; render(); };
  byId('silane-next').onclick = () => { page++; render(); };
  if (location.hash === '#silane') showSilane();
}

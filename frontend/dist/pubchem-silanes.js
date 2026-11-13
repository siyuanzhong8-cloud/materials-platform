{
  const el = id => document.getElementById('pubchem-' + id);
  const size = 20;
  let data;
  let page = 0;
  let loading = false;

  function render() {
    const query = el('query').value.trim().toLowerCase();
    const matches = data.filter(row =>
      [row.CID, row.IUPACName, row.FunctionalGroups, row.MolecularFormula, row.InChIKey]
        .some(value => value.toLowerCase().includes(query)));
    const pages = Math.ceil(matches.length / size);
    el('status').textContent = pages ? '共 ' + matches.length + ' 条 · 第 ' + (page + 1) + ' / ' + pages + ' 页' : '没有匹配记录';
    el('prev').disabled = page === 0;
    el('next').disabled = page + 1 >= pages;
    el('rows').replaceChildren(...matches.slice(page * size, (page + 1) * size).map(row => {
      const tr = document.createElement('tr');
      const link = document.createElement('a');
      link.href = 'https://pubchem.ncbi.nlm.nih.gov/compound/' + encodeURIComponent(row.CID);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = row.CID;
      tr.insertCell().append(link);
      for (const key of ['IUPACName', 'FunctionalGroups', 'MolecularFormula', 'MolecularWeight', 'TPSA']) {
        tr.insertCell().textContent = row[key] || '—';
      }
      return tr;
    }));
  }

  el('catalog').addEventListener('toggle', async () => {
    if (!el('catalog').open || data || loading) return;
    loading = true;
    el('status').textContent = '正在加载候选数据…';
    try {
      const response = await fetch('/pubchem-silanes.json');
      if (!response.ok) throw new Error(response.status);
      data = await response.json();
      render();
    } catch {
      el('status').textContent = '加载失败，请关闭目录后重新展开。';
    } finally {
      loading = false;
    }
  });
  el('query').addEventListener('input', () => {
    page = 0;
    if (data) render();
  });
  el('prev').addEventListener('click', () => { page--; render(); });
  el('next').addEventListener('click', () => { page++; render(); });
}

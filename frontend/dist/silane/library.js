import { createElementFilter, formulaElements } from "./periodic-table.js?v=search-20261113";
import { createCouplingFilters } from "./coupling-filters.js?v=search-20261113";
import {
  parseIntent, matchesIntent, previewProperties, displayRecordValue,
  propertySpecs, formatFilter, reportedFields,
} from "./search-intent.js?v=search-20261113";

if (window.self !== window.top) document.body.classList.add("embedded-silane");

const query = document.querySelector("#query");
const source = document.querySelector("#source");
const list = document.querySelector("#molecules");
const count = document.querySelector("#count");
const detail = document.querySelector("#detail");
const depiction = document.querySelector("#depiction");
const chips = document.querySelector("#intentChips");
const intentError = document.querySelector("#intentError");
const suggestions = document.querySelector("#searchSuggestions");
const advancedFilters = document.querySelector("#advancedFilters");
const propertyKind = document.querySelector("#property-kind");
const propertyLo = document.querySelector("#property-lo");
const propertyHi = document.querySelector("#property-hi");
const conditionModal = document.querySelector("#conditionModal");
const conditionContent = document.querySelector("#conditionContent");

let molecules = [];
let selectedId = null;
let matchesElements;
let couplingFilters;
let page = 0;
const pageSize = 12;
const pagination = document.querySelector("#pagination");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const elementSets = new Map();
const knownGroups = new Set();
let intent = parseIntent("");
let conditionStore = new Map();
let conditionSeq = 0;
let debounceId = 0;

const computedFields = [
  ["exact_mass", "精确质量", "Da"],
  ["tpsa", "拓扑极性表面积", "Å²"],
  ["hbond_donors", "氢键供体数", ""],
  ["hbond_acceptors", "氢键受体数", ""],
  ["rotatable_bonds", "可旋转键数", ""],
  ["heavy_atoms", "重原子数", ""],
  ["xlogp", "XLogP", ""],
];


function setDownload(link, path) {
  link.hidden = !path;
  if (path) link.href = path;
  else link.removeAttribute("href");
}

function sourceLabel(text, url) {
  const node = document.createElement(/^https?:\/\//i.test(url ?? "") ? "a" : "span");
  node.textContent = text;
  if (node.tagName === "A") {
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
  }
  return node;
}

const dataTypeClass = { "实验": "experimental", "计算": "calculated", "预测": "predicted", "计算（描述符）": "descriptor" };

function dataTag(kind) {
  const tag = document.createElement("span");
  tag.className = "data-tag data-tag-" + (dataTypeClass[kind] ?? "other");
  tag.textContent = kind;
  tag.title = "数据类型：" + kind;
  return tag;
}

let externalData = { meta: {}, molecules: {} };

function propertyRow(label, value) {
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  return [term, description];
}

function formulaHtml(formula) {
  return String(formula ?? "").replace(/(\d+)/g, "<sub>$1</sub>");
}

function siliconEndText(molecule) {
  const groups = molecule.silicon_end_groups
    ?? molecule.coupling_structure?.sites?.flatMap(site => site.groups ?? []);
  if (!groups?.length) return "";
  const counts = new Map();
  for (const entry of groups) counts.set(entry.group, (counts.get(entry.group) ?? 0) + (entry.count ?? 0));
  return [...counts].map(([group, n]) => group + " × " + n).join("、");
}

const byproductNames = {
  "甲氧基": "甲醇", "乙氧基": "乙醇", "Si–Cl": "氯化氢", "乙酰氧基": "乙酸",
  "异丙氧基": "异丙醇", "2-甲氧基乙氧基": "2-甲氧基乙醇", "叔丁氧基": "叔丁醇",
};

function siliconGroupCounts(molecule) {
  const groups = molecule.coupling_structure?.sites?.flatMap(site => site.groups ?? [])
    ?? molecule.silicon_end_groups ?? [];
  const counts = new Map();
  for (const entry of groups) counts.set(entry.group, (counts.get(entry.group) ?? 0) + (entry.count ?? 0));
  return counts;
}

function hydrolysisByproducts(molecule) {
  const names = [];
  for (const group of siliconGroupCounts(molecule).keys()) {
    const name = byproductNames[group];
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

function hydrolysisText(molecule) {
  const data = molecule.coupling_structure;
  if (!data) return "";
  if (!data.hydrolyzable) return "硅端不可水解";
  const parts = ["可水解基团 " + data.hydrolyzable_count + " 个"];
  const byproducts = hydrolysisByproducts(molecule);
  if (byproducts.length) parts.push("水解放出" + byproducts.join("、"));
  return parts.join("，");
}

function linkerText(molecule) {
  const data = molecule.coupling_structure;
  if (!data) return "";
  if (data.linker_status === "no_function") return "无独立有机官能团";
  if (data.linker_status !== "defined") return "";
  const lengths = [...new Set((data.sites ?? []).flatMap(site => site.linkers ?? [])
    .filter(linker => linker.target === "organic").map(linker => linker.length))].sort((a, b) => a - b);
  return lengths.length ? "连接链 " + lengths.join("/") + " 个重原子" : "";
}

const compareIds = [];
const compareLimit = 3;

function rememberCondition(record) {
  const id = String(++conditionSeq);
  conditionStore.set(id, record);
  return id;
}

function showCondition(record) {
  conditionContent.replaceChildren();
  const listNode = document.createElement("dl");
  listNode.className = "condition-definition";
  const parsed = record.parsed?.display ?? displayRecordValue(record);
  const rows = [
    ["性质", record.label ?? reportedFields[record.property] ?? record.property],
    ["显示数值", parsed],
    ["原文数值", record.value == null ? "未提供" : String(record.value)],
    ["单位", record.unit ? record.unit : "原文未注明"],
    ["测试条件", record.conditions ? record.conditions : "未提供或未注明"],
    ["来源", record.source || "未提供"],
    ["收录日期", record.retrieved_on || ""],
  ];
  if (record.parsed?.convertedFrom) rows.splice(2, 0, ["单位换算", "由 " + record.parsed.convertedFrom + " 换算为摄氏度，仅用于数值筛选与对照"]);
  for (const [label, value] of rows) {
    if (value === "") continue;
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    if (label === "来源") {
      description.append(sourceLabel(record.source || "未提供", record.source_url));
      if (record.original_source_url && record.original_source_url !== record.source_url) {
        description.append("（", sourceLabel("原始来源", record.original_source_url), "）");
      }
    } else {
      description.textContent = value;
    }
    listNode.append(term, description);
  }
  conditionContent.append(listNode);
  if (typeof conditionModal.showModal === "function") conditionModal.showModal();
  else conditionModal.setAttribute("open", "");
}

function conditionButton(record) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "condition-link";
  button.dataset.condition = rememberCondition(record);
  button.textContent = "条件";
  return button;
}

function showPhyschem(molecule) {
  const computed = molecule.computed_properties;
  const computedList = document.querySelector("#computed-properties");
  const computedSource = document.querySelector("#computed-source");
  document.querySelector("#computed-section").hidden = !computed;
  computedList.replaceChildren();
  computedSource.replaceChildren();
  if (computed) {
    computedSource.append(sourceLabel(computed.source, computed.source_url));
    for (const [key, label, unit] of computedFields) {
      if (!Object.hasOwn(computed.values, key)) continue;
      const value = computed.values[key];
      const shown = value == null ? "暂无数据" : [value, unit].filter(part => part !== "").join(" ");
      const [term, description] = propertyRow(label, shown);
      if (value != null) description.append(" ", dataTag("计算（描述符）"));
      computedList.append(term, description);
    }
  }

  const reported = molecule.reported_properties ?? [];
  const reportedList = document.querySelector("#reported-properties");
  document.querySelector("#reported-section").hidden = false;
  document.querySelector("#reported-empty").hidden = reported.length > 0;
  reportedList.replaceChildren();
  const byProperty = new Map();
  for (const record of reported) {
    if (!byProperty.has(record.property)) byProperty.set(record.property, new Map());
    const entries = byProperty.get(record.property);
    const value = displayRecordValue(record);
    const conditions = record.conditions ?? "";
    const key = JSON.stringify([value, conditions]);
    if (!entries.has(key)) entries.set(key, { value, conditions, sources: new Map(), record });
    const sourceKey = JSON.stringify([record.source, record.source_url, record.original_source_url]);
    entries.get(key).sources.set(sourceKey, record);
  }
  const order = new Set([...Object.keys(reportedFields), ...byProperty.keys()]);
  for (const property of order) {
    const entries = byProperty.get(property);
    if (!entries) continue;
    const [term, description] = propertyRow(reportedFields[property] ?? property, "");
    const rows = [];
    for (const { value, conditions, sources, record } of entries.values()) {
      const row = document.createElement("div");
      row.className = "reported-value";
      const amount = document.createElement("span");
      amount.textContent = value;
      row.append(amount, dataTag("实验"), conditionButton({ ...record, label: reportedFields[property] ?? property }));
      const annotation = document.createElement("small");
      annotation.className = "property-note";
      if (conditions) annotation.append(conditions + " · ");
      let first = true;
      for (const item of sources.values()) {
        if (!first) annotation.append(" · ");
        annotation.append(sourceLabel(item.source, item.source_url));
        if (item.original_source_url && item.original_source_url !== item.source_url) {
          annotation.append("（", sourceLabel("原始来源", item.original_source_url), "）");
        }
        first = false;
      }
      row.append(annotation);
      rows.push(row);
    }
    description.append(...rows.slice(0, 3));
    if (rows.length > 3) {
      const details = document.createElement("details");
      const title = document.createElement("summary");
      title.textContent = "另外 " + (rows.length - 3) + " 条记录";
      details.append(title, ...rows.slice(3));
      description.append(details);
    }
    reportedList.append(term, description);
  }
}

function showCoupling(molecule) {
  const data = molecule.coupling_structure;
  const summary = document.querySelector("#coupling-summary");
  const sites = document.querySelector("#silicon-sites");
  summary.replaceChildren();
  sites.replaceChildren();
  if (!data) {
    summary.append(...propertyRow("结构标注", "待分类"));
    return;
  }
  summary.append(
    ...propertyRow("有机端官能团", data.functional_group || "待分类"),
    ...propertyRow("硅端水解性", data.hydrolyzable ? "可水解" : "不可水解（硅端无可水解基团）"),
    ...propertyRow("可水解基团总数", data.hydrolyzable_count),
  );
  const byproducts = hydrolysisByproducts(molecule);
  if (data.hydrolyzable && byproducts.length) summary.append(...propertyRow("水解副产物", byproducts.join("、")));
  if (data.linker_status === "no_function") {
    summary.append(...propertyRow("连接段", "无独立有机官能团"));
  } else if (data.linker_status === "unresolved") {
    summary.append(...propertyRow("连接段", "待复核"));
  }
  for (const site of data.sites) {
    const block = document.createElement("div");
    block.className = "silicon-site";
    const title = document.createElement("h4");
    title.textContent = "硅位点 " + site.site + " · " + site.count + " 个可水解基团";
    const groups = document.createElement("p");
    groups.textContent = site.groups.map(({ group, count }) => group + " × " + count).join("、") || "无";
    block.append(title, groups);
    for (const linker of site.linkers) {
      const line = document.createElement("p");
      line.className = "linker-description";
      const target = linker.target === "silicon_bridge" ? "至硅位点 " + linker.target_site : "至有机端";
      const composition = Object.entries(linker.elements).map(([e, n]) => e + " × " + n).join("、");
      line.textContent = [target, linker.length + " 个重原子", composition, ...linker.tags]
        .filter(Boolean).join(" · ");
      const path = document.createElement("small");
      path.className = "linker-path";
      path.textContent = linker.path.join("–");
      block.append(line, path);
    }
    sites.append(block);
  }
}

const comptoxLabels = { "logP (log Kow)": "logP（log Kow）", "water_solubility": "水溶性" };

function showExternal(molecule) {
  const section = document.querySelector("#external-section");
  const listNode = document.querySelector("#external-data");
  listNode.replaceChildren();
  const entry = externalData.molecules?.[molecule.id];
  const meta = externalData.meta ?? {};
  section.hidden = !entry;
  if (!entry) return;
  const addRow = (label, nodes) => {
    const [term, description] = propertyRow(label, "");
    description.append(...nodes);
    listNode.append(term, description);
  };
  const datasetLink = info => sourceLabel(info?.name ?? "", info?.url);
  if (entry.spice?.length) {
    const total = entry.spice.reduce((sum, item) => sum + (item.conformations ?? 0), 0);
    const text = "已收录 · " + total + " 个构象" + (entry.spice.length > 1 ? "（" + entry.spice.length + " 条记录）" : "")
      + " · ωB97M-D3(BJ)/def2-TZVPPD · 能量与力";
    const note = document.createElement("small");
    note.className = "property-note";
    note.append(datasetLink(meta.spice), " · " + (meta.spice?.license ?? ""));
    if (entry.spice.some(item => !String(item.match ?? "").startsWith("InChIKey 完全"))) note.append(" · 匹配方式：" + entry.spice.map(item => item.match).join("、"));
    addRow("SPICE 2", [document.createTextNode(text + " "), dataTag("计算"), note]);
  }
  if (entry.gfn_xtb?.length) {
    const total = entry.gfn_xtb.reduce((sum, item) => sum + (item.configurations ?? 0), 0);
    const note = document.createElement("small");
    note.className = "property-note";
    note.append(datasetLink(meta.gfn_xtb), " · " + (meta.gfn_xtb?.license ?? ""));
    addRow("GFN-xTB", [document.createTextNode("已收录 · " + total + " 个构型 · revPBE · 能量 "), dataTag("计算"), note]);
  }
  for (const item of entry.comptox ?? []) {
    const rowNodes = [document.createTextNode(item.value + " " + (item.unit ?? "") + " "), dataTag("实验")];
    const note = document.createElement("small");
    note.className = "property-note";
    note.append([item.temperature ? item.temperature + " °C" : "", item.reference].filter(Boolean).join(" · ") + " · ", datasetLink(meta.comptox));
    rowNodes.push(note);
    if (item.warning) {
      const warning = document.createElement("small");
      warning.className = "data-warning";
      warning.textContent = "⚠ " + item.warning;
      rowNodes.push(warning);
    }
    addRow(comptoxLabels[item.property] ?? item.property, rowNodes);
  }
}

const detailDialog = document.querySelector("#detailDialog");

function showResults() {
  const wasHome = document.body.classList.contains("search-home");
  document.body.classList.remove("search-home");
  if (wasHome && molecules.length && !list.children.length) {
    intent = currentIntent();
    renderChips(intent);
    renderList();
  }
}

function clearAdvancedFilters() {
  source.value = "";
  propertyKind.value = "";
  propertyLo.value = "";
  propertyHi.value = "";
  document.querySelector("#clear-elements")?.click();
  document.querySelector("#clear-coupling")?.click();
}

function showMolecule(molecule) {
  selectedId = molecule?.id ?? null;
  for (const card of list.querySelectorAll(".molecule-card")) {
    card.setAttribute("aria-current", String(card.dataset.id === selectedId));
  }
  if (!molecule) {
    if (detailDialog?.open) detailDialog.close();
    return;
  }

  document.querySelector("#label").textContent = molecule.label || molecule.name;
  document.querySelector("#state").textContent = molecule.structure_state || "";
  const chineseName = document.querySelector("#name");
  chineseName.hidden = !molecule.label || molecule.label === molecule.name;
  chineseName.textContent = chineseName.hidden ? "" : molecule.name;
  document.querySelector("#structure").hidden = !molecule.depiction;
  if (molecule.depiction) {
    depiction.src = molecule.depiction;
    depiction.alt = molecule.name + "的二维结构";
    document.querySelector("#depiction-caption").textContent = molecule.depiction_source
      ? "二维分子结构 · " + molecule.depiction_source
      : "二维分子结构";
  } else {
    depiction.removeAttribute("src");
  }

  showCoupling(molecule);
  showPhyschem(molecule);
  showExternal(molecule);

  const fields = [
    ["英文名称", molecule.english_name],
    ["分子式", molecule.formula],
    ["分子量", molecule.molecular_weight == null ? null : molecule.molecular_weight + " g/mol"],
    ["CAS", molecule.cas],
    ["PubChem CID", molecule.pubchem_cid],
    ["SMILES", molecule.smiles, true],
    ["InChIKey", molecule.inchikey, true],
    ["来源", molecule.source],
    ["入库日期", molecule.retrieved_on],
  ];
  const properties = document.querySelector("#properties");
  properties.replaceChildren();
  for (const [label, value, isCode] of fields) {
    if (value == null || value === "") continue;
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    if (isCode) {
      const code = document.createElement("code");
      code.textContent = value;
      description.append(code);
    } else if (label === "分子式") {
      description.innerHTML = formulaHtml(value);
    } else {
      description.textContent = value;
    }
    properties.append(term, description);
  }

  const references = document.querySelector("#catalog-references");
  references.replaceChildren();
  const catalog = molecule.catalog_references ?? [];
  references.hidden = catalog.length === 0;
  if (catalog.length) {
    const label = document.createElement("span");
    label.textContent = "目录索引";
    references.append(label);
    for (const reference of catalog) {
      references.append(sourceLabel(reference.catalog + " " + reference.code, reference.url));
    }
  }

  setDownload(document.querySelector("#sdf"), molecule.sdf);
  const pubchem = document.querySelector("#pubchem");
  pubchem.hidden = !molecule.pubchem_cid;
  if (molecule.pubchem_cid) {
    pubchem.href = "https://pubchem.ncbi.nlm.nih.gov/compound/" + encodeURIComponent(molecule.pubchem_cid);
  } else {
    pubchem.removeAttribute("href");
  }
  if (detailDialog && !detailDialog.open) detailDialog.showModal();
}

function manualPropertyFilter() {
  if (!propertyKind.value) return null;
  const spec = propertySpecs.find(item => item.key === propertyKind.value);
  if (!spec) return null;
  const lo = propertyLo.value === "" ? null : Number(propertyLo.value);
  const hi = propertyHi.value === "" ? null : Number(propertyHi.value);
  if (lo != null && !Number.isFinite(lo) || hi != null && !Number.isFinite(hi)) return "invalid";
  if (lo != null && hi != null && lo > hi) return "order";
  if (lo == null && hi == null) return null;
  return { kind: spec.key, label: spec.label, unit: spec.unit, lo, hi, loInclusive: true, hiInclusive: true };
}

function currentIntent() {
  const parsed = parseIntent(query.value, knownGroups);
  const manual = manualPropertyFilter();
  if (manual === "invalid" || manual === "order") {
    return { ...parsed, error: manual === "order" ? "性质最小值不能大于最大值。" : "性质范围必须是有限数值。" };
  }
  if (manual) {
    parsed.filters = [...parsed.filters.filter(filter => filter.kind !== manual.kind), manual];
  }
  return parsed;
}

function hasActiveConditions(current) {
  return Boolean(
    current.q || current.groups.length || current.hydrolysis || current.hydroGroup || current.siteCount
    || current.filters.length || source.value
    || (couplingFilters && !document.querySelector("#clear-coupling").disabled)
    || (document.querySelector("#clear-elements") && !document.querySelector("#clear-elements").disabled),
  );
}

function renderChips(current = intent) {
  chips.replaceChildren();
  const appendChip = (label, action, kind = "") => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label + " ×";
    button.dataset.action = action;
    if (kind) button.className = kind;
    chips.append(button);
  };
  if (current.q) appendChip("关键词 " + current.q, "clear-q");
  current.groups.forEach((group, index) => appendChip(group, "group:" + index, "structure-token"));
  if (current.hydrolysis) appendChip(current.hydrolysis === "yes" ? "可水解" : "不可水解", "hydrolysis", "structure-token");
  if (current.hydroGroup) appendChip(current.hydroGroup, "hydroGroup", "structure-token");
  if (current.siteCount) appendChip("硅端基团 × " + current.siteCount, "siteCount", "structure-token");
  current.filters.forEach((filter, index) => appendChip(formatFilter(filter), "filter:" + index, "property-token"));
  if (source.value) appendChip("来源 " + source.value, "source");
  if (document.querySelector("#clear-elements") && !document.querySelector("#clear-elements").disabled) {
    appendChip("元素筛选已启用", "elements", "structure-token");
  }
  if (document.querySelector("#clear-coupling") && !document.querySelector("#clear-coupling").disabled) {
    appendChip("结构筛选已启用", "coupling", "structure-token");
  }
  const warnings = current.warnings ?? [];
  intentError.hidden = !(current.error || warnings.length);
  intentError.textContent = current.error || (warnings.length ? "有条件尚未识别完整：" + warnings.join(" ") : "");
}

function applyChipAction(action) {
  if (action === "clear-q") query.value = query.value.replace(intent.q, " ").replace(/\s+/g, " ").trim();
  else if (action === "hydrolysis") query.value = query.value.replace(/不可水解|不能水解|非水解|可水解|水解性/g, " ").trim();
  else if (action === "hydroGroup" || action === "siteCount") {
    query.value = query.value.replace(/三甲氧基|三乙氧基|trimethoxy|triethoxy|甲氧基|乙氧基/ig, " ").trim();
  } else if (action.startsWith("group:")) {
    const group = intent.groups[Number(action.slice(6))];
    if (group) query.value = query.value.replace(group, " ").trim();
  } else if (action.startsWith("filter:")) {
    const filter = intent.filters[Number(action.slice(7))];
    if (filter && propertyKind.value === filter.kind) {
      propertyKind.value = "";
      propertyLo.value = "";
      propertyHi.value = "";
    }
    if (filter) {
      const pattern = new RegExp(filter.label + "[^，,;；]*", "g");
      query.value = query.value.replace(pattern, " ").replace(/\s+/g, " ").trim();
    }
  } else if (action === "source") source.value = "";
  else if (action === "elements") document.querySelector("#clear-elements")?.click();
  else if (action === "coupling") document.querySelector("#clear-coupling")?.click();
  runSearch();
}

function moleculeRow(molecule) {
  const row = document.createElement("li");
  const card = document.createElement("div");
  card.className = "molecule-card";
  card.dataset.id = molecule.id;
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-current", String(molecule.id === selectedId));

  const button = document.createElement("div");
  button.className = "molecule-select";
  const identity = document.createElement("span");
  const label = document.createElement("strong");
  label.textContent = molecule.label || molecule.name;
  identity.append(label);
  const metadata = [molecule.cas ? "CAS " + molecule.cas : "CAS 未收录"];
  if (metadata.length) {
    const name = document.createElement("span");
    name.className = "row-name";
    name.textContent = metadata.join(" · ");
    identity.append(name);
  }
  button.append(identity);
  const functionalGroup = molecule.coupling_structure?.functional_group ?? molecule.functional_group;
  if (functionalGroup) {
    const tag = document.createElement("span");
    tag.className = "group-tag";
    tag.textContent = functionalGroup;
    button.append(tag);
  }
  const toggle = document.createElement("label");
  toggle.className = "compare-toggle";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = compareIds.includes(molecule.id);
  box.dataset.compare = molecule.id;
  toggle.append(box, document.createTextNode("对比"));
  button.append(toggle);
  card.append(button);

  const previews = intent.filters.length
    ? previewProperties(molecule, intent.filters).filter(record => record.matched)
    : [];
  if (previews.length) {
    const points = document.createElement("div");
    points.className = "matching-points";
    for (const record of previews) {
      const line = document.createElement("div");
      if (record.matched) line.className = "matched";
      const kind = document.createElement("span");
      kind.textContent = record.label ?? reportedFields[record.property] ?? record.property;
      const value = document.createElement("b");
      value.textContent = record.parsed?.display ?? displayRecordValue(record);
      line.append(kind, value);
      if (record.conditions || record.source) {
        const note = document.createElement("small");
        note.textContent = [record.conditions, record.source].filter(Boolean).join(" · ");
        line.append(note);
      }
      if (record.property !== "molecular_weight") line.append(conditionButton(record));
      points.append(line);
    }
    card.append(points);
  }

  const select = () => showMolecule(molecule);
  card.addEventListener("click", event => {
    if (event.target.closest(".condition-link, .compare-toggle")) return;
    select();
  });
  card.addEventListener("keydown", event => {
    if (event.target.closest(".condition-link, .compare-toggle")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  });
  row.append(card);
  return row;
}

function renderList() {
  const structureMatches = couplingFilters.matcher();
  const visible = molecules.filter(molecule => {
    if (!matchesElements(elementSets.get(molecule.id))) return false;
    if (!structureMatches(molecule)) return false;
    if (source.value && molecule.source !== source.value) return false;
    return matchesIntent(molecule, intent);
  });
  const pages = Math.max(1, Math.ceil(visible.length / pageSize));
  page = Math.min(Math.max(page, 0), pages - 1);
  const start = page * pageSize;
  const pageItems = visible.slice(start, start + pageSize);
  count.textContent = visible.length
    ? "匹配 " + visible.length + " / " + molecules.length + " 条"
    : "没有符合条件的分子";
  pagination.hidden = pages === 1 && visible.length > 0 ? true : pages <= 1;
  if (!visible.length) pagination.hidden = true;
  previousPage.disabled = page === 0;
  nextPage.disabled = page === pages - 1;
  document.querySelector("#page-number").textContent = (page + 1) + " / " + pages;
  conditionStore = new Map();
  conditionSeq = 0;
  const rows = document.createDocumentFragment();
  pageItems.forEach(molecule => rows.append(moleculeRow(molecule)));
  list.replaceChildren(rows);
  updateCompareButton();
  if (!visible.length) {
    const empty = document.createElement("li");
    empty.innerHTML = "<div class=\"row-meta\" style=\"padding:18px 17px\">没有找到符合条件的分子。请减少官能团或放宽性质范围后再试。</div>";
    list.replaceChildren(empty);
    showMolecule(null);
    return;
  }
  const current = pageItems.find(molecule => molecule.id === selectedId);
  if (current) showMolecule(current);
  else showMolecule(null);
}

function runSearch() {
  showResults();
  intent = currentIntent();
  page = 0;
  renderChips(intent);
  renderList();
}

function resetPage() {
  page = 0;
  runSearch();
}

function populateFilter(select, key) {
  const values = [...new Set(molecules.map(molecule => molecule[key]).filter(Boolean))];
  values.sort((a, b) => a.localeCompare(b, "zh-CN"));
  values.forEach(value => select.add(new Option(value, value)));
}

function renderStats() {
  const reportedMolecules = molecules.filter(molecule => (molecule.reported_properties ?? []).length).length;
  const reportedRecords = molecules.reduce((sum, molecule) => sum + (molecule.reported_properties ?? []).length, 0);
  const sources = new Set(molecules.map(molecule => molecule.source).filter(Boolean));
  const items = [
    [molecules.length.toLocaleString("zh-CN"), "种分子"],
    [reportedMolecules.toLocaleString("zh-CN"), "种有公开理化数据"],
    [reportedRecords.toLocaleString("zh-CN"), "条性质记录"],
    [String(sources.size), "个数据来源"],
  ];
  document.querySelector("#stats").innerHTML = items.map(([n, label]) => `<div><strong>${n}</strong><span>${label}</span></div>`).join("");
}

document.querySelector("#engineForm").addEventListener("submit", event => {
  event.preventDefault();
  suggestions.hidden = true;
  runSearch();
});
query.addEventListener("focus", () => { suggestions.hidden = false; });
query.addEventListener("keydown", event => {
  if (event.key === "Escape") suggestions.hidden = true;
});
query.addEventListener("input", () => {
  clearTimeout(debounceId);
  debounceId = setTimeout(runSearch, 280);
});
document.addEventListener("click", event => {
  if (!event.target.closest(".engine-wrap")) suggestions.hidden = true;
});
suggestions.addEventListener("click", event => {
  const button = event.target.closest("[data-example]");
  if (!button) return;
  query.value = button.dataset.example;
  suggestions.hidden = true;
  runSearch();
});
chips.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (button?.dataset.action) applyChipAction(button.dataset.action);
});
document.querySelector("#advancedToggle").addEventListener("click", () => {
  const opening = advancedFilters.hidden;
  advancedFilters.hidden = !opening;
  document.body.classList.toggle("advanced-open", opening);
  document.querySelector("#advancedToggle").textContent = opening ? "收起高级筛选" : "高级筛选";
  if (opening) showResults();
});
document.querySelector("#browse").addEventListener("click", () => {
  query.value = "";
  clearAdvancedFilters();
  runSearch();
});
document.querySelector("#reset").addEventListener("click", () => {
  query.value = "";
  clearAdvancedFilters();
  runSearch();
});
document.querySelector("#closeDetail").addEventListener("click", () => {
  showMolecule(null);
});
detailDialog?.addEventListener("close", () => {
  if (selectedId) {
    selectedId = null;
    for (const card of list.querySelectorAll(".molecule-card")) {
      card.setAttribute("aria-current", "false");
    }
  }
});
for (const input of [propertyKind, propertyLo, propertyHi, source]) {
  input.addEventListener("change", runSearch);
}
document.querySelector("#closeCondition").addEventListener("click", () => conditionModal.close());
document.addEventListener("click", event => {
  const button = event.target.closest("[data-condition]");
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const record = conditionStore.get(button.dataset.condition);
  if (record) showCondition(record);
});


const compareDialog = document.querySelector("#compareDialog");
const compareContent = document.querySelector("#compareContent");
const compareOpen = document.querySelector("#compareOpen");
const compareClear = document.querySelector("#compareClear");
const compareProperties = ["boiling_point", "specific_gravity", "density", "refractive_index", "flash_point"];

function updateCompareButton() {
  compareOpen.textContent = "对比已选 " + compareIds.length + " / " + compareLimit;
  compareOpen.disabled = compareIds.length < 2;
  compareClear.hidden = compareIds.length === 0;
  for (const box of list.querySelectorAll("[data-compare]")) {
    box.checked = compareIds.includes(box.dataset.compare);
    box.disabled = !box.checked && compareIds.length >= compareLimit;
  }
}

function compareCell(content, missing = false) {
  const cell = document.createElement("td");
  if (missing) {
    cell.className = "missing";
    cell.textContent = "未收录";
  } else if (content instanceof Node) cell.append(content);
  else cell.textContent = content;
  return cell;
}

function reportedCell(molecule, key) {
  const records = (molecule.reported_properties ?? []).filter(record => record.property === key);
  if (!records.length) return compareCell("", true);
  const record = records[0];
  const wrap = document.createDocumentFragment();
  wrap.append(document.createTextNode(displayRecordValue(record) + " "), dataTag("实验"));
  const note = document.createElement("small");
  note.textContent = [record.conditions, record.source, records.length > 1 ? "共 " + records.length + " 条记录" : ""]
    .filter(Boolean).join(" · ");
  if (note.textContent) wrap.append(note);
  return compareCell(wrap);
}

function showCompare() {
  const chosen = compareIds.map(id => molecules.find(molecule => molecule.id === id)).filter(Boolean);
  const table = document.createElement("table");
  table.className = "compare-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.append(document.createElement("th"));
  for (const molecule of chosen) {
    const th = document.createElement("th");
    th.textContent = molecule.label || molecule.name;
    headRow.append(th);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  const addRow = (label, cellFor) => {
    const row = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = label;
    row.append(th, ...chosen.map(cellFor));
    body.append(row);
  };
  addRow("结构", molecule => {
    if (!molecule.depiction) return compareCell("", true);
    const img = document.createElement("img");
    img.src = molecule.depiction;
    img.alt = molecule.name + "的二维结构";
    return compareCell(img);
  });
  const text = value => value == null || value === "" ? compareCell("", true) : compareCell(String(value));
  addRow("中文名", molecule => text(molecule.name));
  addRow("CAS", molecule => text(molecule.cas));
  addRow("分子式", molecule => {
    const span = document.createElement("span");
    span.innerHTML = formulaHtml(molecule.formula);
    return compareCell(span);
  });
  addRow("分子量", molecule => text(molecule.molecular_weight == null ? null : molecule.molecular_weight + " g/mol"));
  addRow("有机端官能团", molecule => text(molecule.coupling_structure?.functional_group ?? molecule.functional_group));
  addRow("硅端基团", molecule => text(siliconEndText(molecule)));
  addRow("水解性", molecule => text(hydrolysisText(molecule)));
  addRow("连接链", molecule => text(linkerText(molecule)));
  for (const key of compareProperties) {
    if (!chosen.some(molecule => (molecule.reported_properties ?? []).some(record => record.property === key))) continue;
    addRow(reportedFields[key] ?? key, molecule => reportedCell(molecule, key));
  }
  const descriptor = value => {
    if (value == null || value === "") return compareCell("", true);
    const span = document.createElement("span");
    span.append(String(value) + " ", dataTag("计算（描述符）"));
    return compareCell(span);
  };
  addRow("TPSA", molecule => descriptor(molecule.computed_properties?.values?.tpsa == null ? null : molecule.computed_properties.values.tpsa + " Å²"));
  addRow("可旋转键数", molecule => descriptor(molecule.computed_properties?.values?.rotatable_bonds));
  table.append(head, body);
  compareContent.replaceChildren(table);
  if (!compareDialog.open) compareDialog.showModal();
}

list.addEventListener("change", event => {
  const box = event.target.closest("[data-compare]");
  if (!box) return;
  const id = box.dataset.compare;
  const index = compareIds.indexOf(id);
  if (box.checked && index < 0 && compareIds.length < compareLimit) compareIds.push(id);
  if (!box.checked && index >= 0) compareIds.splice(index, 1);
  updateCompareButton();
});
compareOpen.addEventListener("click", showCompare);
compareClear.addEventListener("click", () => {
  compareIds.length = 0;
  updateCompareButton();
});
document.querySelector("#closeCompare").addEventListener("click", () => compareDialog.close());

previousPage.addEventListener("click", () => {
  page--;
  renderList();
});
nextPage.addEventListener("click", () => {
  page++;
  renderList();
});

try {
  const response = await fetch("molecules.json", { cache: "no-cache" });
  if (!response.ok) throw new Error("HTTP " + response.status);
  molecules = await response.json();
  if (!Array.isArray(molecules)) throw new Error("Expected a molecule array");
  const available = new Set();
  for (const molecule of molecules) {
    const elements = formulaElements(molecule.formula);
    elementSets.set(molecule.id, elements);
    elements.forEach(symbol => available.add(symbol));
    const group = molecule.coupling_structure?.functional_group ?? molecule.functional_group;
    if (group) knownGroups.add(group);
  }
  matchesElements = createElementFilter(
    document.querySelector("#periodic-grid"), document.querySelector("#element-chips"),
    document.querySelector("#element-mode"), document.querySelector("#clear-elements"),
    available, resetPage,
  );
  couplingFilters = createCouplingFilters(molecules, resetPage);
  populateFilter(source, "source");
  for (const spec of propertySpecs) {
    propertyKind.add(new Option(spec.label + (spec.unit ? " · " + spec.unit : ""), spec.key));
  }
  renderStats();
  try {
    const externalResponse = await fetch("external.json", { cache: "no-cache" });
    if (externalResponse.ok) externalData = await externalResponse.json();
  } catch (error) {
    console.warn("external.json 读取失败，外部数据区不显示", error);
  }
} catch (error) {
  showResults();
  count.textContent = "分子数据读取失败，请检查 molecules.json 后刷新。";
  console.error(error);
}

/**
 * Local silane query grammar. Unrecognized text stays a keyword; values are
 * never invented. Only records that can be parsed confidently are used for
 * numeric filters. Missing or unparseable properties do not satisfy a filter.
 */

const NUMBER = String.raw`[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?`;

export const reportedFields = {
  boiling_point: "沸点",
  density: "密度",
  specific_gravity: "相对密度",
  refractive_index: "折射率",
  melting_point: "熔点",
  freezing_point: "凝固点",
  vapor_pressure: "蒸气压",
  vapor_density: "蒸气密度",
  flash_point: "闪点",
  kinematic_viscosity: "运动黏度",
  viscosity: "黏度",
  surface_tension: "表面张力",
  heat_of_vaporization: "汽化热",
};

export const propertySpecs = [
  { key: "boiling_point", label: "沸点", unit: "°C", keys: ["boiling_point"], aliases: ["沸点", "boiling point", "bp"] },
  { key: "melting_point", label: "熔点", unit: "°C", keys: ["melting_point"], aliases: ["熔点", "melting point", "mp"] },
  { key: "flash_point", label: "闪点", unit: "°C", keys: ["flash_point"], aliases: ["闪点", "flash point"] },
  { key: "freezing_point", label: "凝固点", unit: "°C", keys: ["freezing_point"], aliases: ["凝固点", "冰点", "freezing point"] },
  { key: "density", label: "密度", unit: "", keys: ["density", "specific_gravity"], aliases: ["密度", "density"] },
  { key: "specific_gravity", label: "相对密度", unit: "", keys: ["specific_gravity"], aliases: ["相对密度", "比重", "specific gravity"] },
  { key: "refractive_index", label: "折射率", unit: "", keys: ["refractive_index"], aliases: ["折射率", "refractive index"] },
  { key: "kinematic_viscosity", label: "运动黏度", unit: "cSt", keys: ["kinematic_viscosity"], aliases: ["运动黏度", "运动粘度"] },
  { key: "molecular_weight", label: "分子量", unit: "g/mol", keys: ["molecular_weight"], aliases: ["分子量", "摩尔质量", "molecular weight"] },
];

const PROPERTY_ALIASES = [];
for (const spec of propertySpecs) {
  for (const alias of spec.aliases) PROPERTY_ALIASES.push([alias, spec]);
}
PROPERTY_ALIASES.sort((a, b) => b[0].length - a[0].length);

export const functionalGroupAliases = [
  ["甲基丙烯酰胺", "甲基丙烯酰胺"],
  ["丙烯酰胺", "丙烯酰胺"],
  ["甲基丙烯酸酯", "甲基丙烯酸酯"],
  ["不饱和二羧酸酯", "不饱和二羧酸酯"],
  ["氨基甲酸酯", "氨基甲酸酯"],
  ["卤代有机基", "卤代有机基"],
  ["含氟有机基", "含氟有机基"],
  ["芳基/芳烷基", "芳基/芳烷基"],
  ["烷基/其他", "烷基/其他"],
  ["环状氮硅烷", "环状氮硅烷"],
  ["磺酰叠氮", "磺酰叠氮"],
  ["磺酰氯", "磺酰氯"],
  ["硫代羧酸酯", "硫代羧酸酯"],
  ["硫氰酸酯", "硫氰酸酯"],
  ["二硫/多硫", "二硫/多硫"],
  ["磷功能基", "磷功能基"],
  ["异氰酸酯", "异氰酸酯"],
  ["其他烯基", "其他烯基"],
  ["卤代酯", "卤代酯"],
  ["丙烯酸酯", "丙烯酸酯"],
  ["缩水甘油", "环氧"],
  ["环氧基", "环氧"],
  ["巯基", "巯基"],
  ["乙烯基", "乙烯基"],
  ["烯丙基", "烯丙基"],
  ["氰基", "氰基"],
  ["脲基", "脲基"],
  ["酰胺", "酰胺"],
  ["羟基", "羟基"],
  ["醚基", "醚基"],
  ["酯基", "酯基"],
  ["醛基", "醛基"],
  ["酮基", "酮基"],
  ["酸酐", "酸酐"],
  ["叠氮", "叠氮"],
  ["硫醚", "硫醚"],
  ["硫脲", "硫脲"],
  ["亚胺", "亚胺"],
  ["硅醚", "硅醚"],
  ["氨基", "氨基"],
  ["环氧", "环氧"],
  ["methacryloxy", "甲基丙烯酸酯"],
  ["methacryl", "甲基丙烯酸酯"],
  ["glycidoxy", "环氧"],
  ["epoxy", "环氧"],
  ["mercapto", "巯基"],
  ["thiol", "巯基"],
  ["vinyl", "乙烯基"],
  ["allyl", "烯丙基"],
  ["amino", "氨基"],
  ["isocyanate", "异氰酸酯"],
  ["urethane", "氨基甲酸酯"],
  ["acrylate", "丙烯酸酯"],
];

const HYDRO_GROUP_ALIASES = [
  ["三甲氧基", { group: "甲氧基", count: 3 }],
  ["三乙氧基", { group: "乙氧基", count: 3 }],
  ["trimethoxy", { group: "甲氧基", count: 3 }],
  ["triethoxy", { group: "乙氧基", count: 3 }],
  ["甲氧基", { group: "甲氧基" }],
  ["乙氧基", { group: "乙氧基" }],
];

const TEMP_KEYS = new Set(["boiling_point", "melting_point", "flash_point", "freezing_point"]);

function normalizeText(text) {
  return String(text ?? "")
    .normalize("NFKC")
    .replace(/[０-９]/g, digit => "０１２３４５６７８９".indexOf(digit))
    .replace(/[−–—～〜~]/g, "-")
    .replace(/℃/g, "°C")
    .replace(/：/g, ":");
}

function finiteNumber(value) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function pointInterval(value, extra = {}) {
  const number = finiteNumber(value);
  if (number == null) return null;
  return { lo: number, hi: number, loInclusive: true, hiInclusive: true, ...extra };
}

function rangeInterval(lo, hi, loInclusive = true, hiInclusive = true, extra = {}) {
  const left = finiteNumber(lo);
  const right = finiteNumber(hi);
  if (left == null || right == null) return null;
  return {
    lo: Math.min(left, right),
    hi: Math.max(left, right),
    loInclusive,
    hiInclusive,
    ...extra,
  };
}

function fahrenheitToCelsius(value) {
  return (value - 32) * (5 / 9);
}

function looksLikeYear(value) {
  return Number.isInteger(value) && value >= 1900 && value <= 2100;
}

/**
 * Parse a reported property record into a numeric interval used only for
 * filtering. Returns null when the original value cannot be read confidently.
 */
export function parseReportedValue(record, propertyKey = record?.property) {
  if (!record) return null;
  const unit = String(record.unit ?? "").trim();
  const raw = record.value;
  if (typeof raw === "number") return pointInterval(raw, { unit, display: formatNumber(raw, unit) });
  if (raw == null || raw === "") return null;

  const chunks = String(raw).split(/;|；/);
  const parsed = chunks.map(chunk => parseValueChunk(chunk, unit, propertyKey)).filter(Boolean);
  if (!parsed.length) return null;
  if (parsed.length === 1) return parsed[0];
  return { alternatives: parsed, display: parsed.map(item => item.display).join("；") };
}

function parseValueChunk(raw, unit, propertyKey) {
  let text = normalizeText(raw).trim();
  if (!text) return null;
  text = text.replace(/^(?:boiling point|melting point|flash point|freezing point|index of refraction|density|specific gravity|vapor pressure|kinematic viscosity)\s*[=:]\s*/i, "");

  if (TEMP_KEYS.has(propertyKey)) return parseTemperature(text, unit);
  return parseGeneralQuantity(text, unit, propertyKey);
}

function parseTemperature(text, unit) {
  const celsius = [...text.matchAll(new RegExp(`(${NUMBER})\\s*(?:°\\s*C|摄氏度)\\b`, "gi"))].map(match => finiteNumber(match[1]));
  const preferredC = celsius.filter(value => value != null && !looksLikeYear(value));
  if (preferredC.length === 1) {
    const inequality = leadingInequality(text);
    if (inequality && text.indexOf(String(preferredC[0])) < 24) {
      return inequalityInterval(inequality, preferredC[0], "°C", text);
    }
    const range = leadingRange(text);
    if (range) return rangeInterval(range[0], range[1], true, true, { unit: "°C", display: `${formatNumber(range[0])}–${formatNumber(range[1])} °C`, raw: text });
    return pointInterval(preferredC[0], { unit: "°C", display: `${formatNumber(preferredC[0])} °C`, raw: text });
  }

  const range = leadingRange(text);
  if (range && /(?:°\s*C|摄氏度)?\s*$/i.test(text.slice(range.index).replace(new RegExp(`^${NUMBER}\\s*-\\s*${NUMBER}`), ""))) {
    return rangeInterval(range[0], range[1], true, true, { unit: "°C", display: `${formatNumber(range[0])}–${formatNumber(range[1])} °C`, raw: text });
  }

  const inequality = leadingInequality(text);
  if (inequality) {
    const after = text.slice(inequality.length).match(new RegExp(`^\\s*(${NUMBER})\\s*(°\\s*C|摄氏度|°\\s*F)?`));
    if (after) {
      let value = Number(after[1]);
      let shownUnit = "°C";
      let convertedFrom;
      if (/F/i.test(after[2] || unit)) {
        value = fahrenheitToCelsius(value);
        convertedFrom = "°F";
      }
      return inequalityInterval(inequality, value, shownUnit, text, convertedFrom);
    }
  }

  if (/°\s*F/i.test(text) && !/°\s*C/i.test(text)) {
    const match = text.match(new RegExp(`(${NUMBER})\\s*°\\s*F\\b`, "i"));
    if (match) {
      const celsiusValue = fahrenheitToCelsius(Number(match[1]));
      return pointInterval(celsiusValue, {
        unit: "°C",
        convertedFrom: "°F",
        display: `${formatNumber(celsiusValue)} °C`,
        raw: text,
      });
    }
  }

  if (/^°C$/i.test(unit) || unit === "") {
    const lonely = text.match(new RegExp(`^(${NUMBER})(?:\\s*°\\s*C)?\\s*$`));
    if (lonely) return pointInterval(lonely[1], { unit: "°C", display: `${formatNumber(lonely[1])} °C`, raw: text });
  }
  return null;
}

function parseGeneralQuantity(text, unit, propertyKey) {
  const inequality = leadingInequality(text);
  if (inequality) {
    const after = text.slice(inequality.length).match(new RegExp(`^\\s*(${NUMBER})`));
    if (after) return inequalityInterval(inequality, Number(after[1]), unit, text);
  }

  const beforeAt = text.split(/\s*(?:@|at)\s+/i)[0].trim();
  const range = leadingRange(beforeAt);
  if (range) {
    return rangeInterval(range[0], range[1], true, true, {
      unit,
      display: [ `${formatNumber(range[0])}–${formatNumber(range[1])}`, unit ].filter(Boolean).join(" "),
      raw: text,
    });
  }

  if (propertyKey === "refractive_index") {
    const match = text.match(new RegExp(`(${NUMBER})(?=\\s*(?:$|@|atg?\\b|,|，))`, "i"))
      ?? text.match(new RegExp(`(${NUMBER})`));
    if (match) {
      const value = Number(match[1]);
      if (value > 0.8 && value < 3) return pointInterval(value, { unit, display: formatNumber(value), raw: text });
    }
  }

  if (propertyKey === "density" || propertyKey === "specific_gravity") {
    const match = text.match(new RegExp(`(${NUMBER})(?:\\s*(g\\s*/\\s*(?:mL|cm3|cm³|cm\\^3)))?`, "i"));
    if (match) {
      const value = Number(match[1]);
      if (value > 0.2 && value < 8) {
        return pointInterval(value, { unit: unit || (match[2] || "").replace(/\s+/g, ""), display: formatNumber(value), raw: text });
      }
    }
  }

  const first = beforeAt.match(new RegExp(`^(${NUMBER})\\s*(.*)$`));
  if (first && !new RegExp(NUMBER).test(first[2].replace(/g\s*\/\s*(?:mL|cm3|cm³|cm\^3)/ig, ""))) {
    return pointInterval(first[1], { unit, display: [formatNumber(first[1]), unit].filter(Boolean).join(" "), raw: text });
  }

  const only = text.match(new RegExp(`^(${NUMBER})$`));
  if (only) return pointInterval(only[1], { unit, display: [formatNumber(only[1]), unit].filter(Boolean).join(" "), raw: text });
  return null;
}

function leadingInequality(text) {
  const match = text.match(/^\s*(<=|>=|≤|≥|<|>)/);
  return match ? match[1] : "";
}

function inequalityInterval(symbol, value, unit, raw, convertedFrom) {
  const inclusive = symbol === "<=" || symbol === "≤";
  const greater = symbol === ">" || symbol === ">=" || symbol === "≥";
  const extra = {
    unit,
    convertedFrom,
    display: `${symbol}${formatNumber(value)}${unit ? ` ${unit}` : ""}`,
    raw,
  };
  if (greater) return { lo: value, hi: Infinity, loInclusive: symbol === ">=" || symbol === "≥", hiInclusive: false, ...extra };
  return { lo: -Infinity, hi: value, loInclusive: false, hiInclusive: inclusive || symbol === "<=" || symbol === "≤", ...extra };
}

function leadingRange(text) {
  const match = text.match(new RegExp(`(${NUMBER})\\s*(?:-|to)\\s*(${NUMBER})`, "i"));
  if (!match) return null;
  let lo = Number(match[1]);
  let hi = Number(match[2]);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  // Common abbreviated ranges such as 93–4 meaning 93–94.
  if (Number.isInteger(lo) && Number.isInteger(hi) && hi < lo) {
    const left = match[1];
    const right = match[2];
    if (right.length < left.length && left.endsWith(right) === false && left.slice(0, left.length - right.length) + right) {
      const expanded = Number(left.slice(0, left.length - right.length) + right);
      if (Number.isInteger(expanded) && expanded >= lo && expanded - lo < 10) hi = expanded;
    }
  }
  if (hi < lo) return null;
  if (lo === 0 && hi > 1000) return null;
  // Avoid reading "290 °C @760" or "80 °C at 1" as a range.
  if (match.index > 0 && /°|@|\bat\b/i.test(text.slice(0, match.index))) return null;
  return Object.assign([lo, hi], { index: match.index });
}

export function formatNumber(value, unit = "") {
  const number = finiteNumber(value);
  if (number == null) return "";
  const shown = Math.abs(number) > 0 && Math.abs(number) < 0.0001
    ? number.toExponential(4)
    : Number(number.toPrecision(7)).toString();
  return unit ? `${shown} ${unit}` : shown;
}

export function intervalOverlaps(recordInterval, filter) {
  const intervals = recordInterval.alternatives ?? [recordInterval];
  return intervals.some(interval => rangesOverlap(interval, filter));
}

function rangesOverlap(interval, filter) {
  const lo = filter.lo;
  const hi = filter.hi;
  if (lo == null && hi == null) return true;
  const leftOk = lo == null || interval.hi > lo || (interval.hi === lo && interval.hiInclusive && filter.loInclusive !== false);
  const rightOk = hi == null || interval.lo < hi || (interval.lo === hi && interval.loInclusive && filter.hiInclusive !== false);
  return leftOk && rightOk;
}

export function displayRecordValue(record) {
  const parsed = parseReportedValue(record);
  if (parsed?.display) return parsed.display;
  const parts = [record.value, record.unit].filter(part => part != null && part !== "");
  return parts.join(" ");
}

export function collectParsedProperties(molecule, keys) {
  const wanted = new Set(keys);
  const rows = [];
  if (wanted.has("molecular_weight") && molecule.molecular_weight != null) {
    rows.push({
      property: "molecular_weight",
      label: "分子量",
      value: molecule.molecular_weight,
      unit: "g/mol",
      parsed: pointInterval(molecule.molecular_weight, { unit: "g/mol", display: formatNumber(molecule.molecular_weight, "g/mol") }),
      source: molecule.source || "分子记录",
    });
  }
  for (const record of molecule.reported_properties ?? []) {
    if (!wanted.has(record.property)) continue;
    const parsed = parseReportedValue(record, record.property);
    rows.push({ ...record, parsed, label: reportedFields[record.property] ?? record.property });
  }
  return rows;
}

export function matchingPropertyRecords(molecule, filter) {
  const spec = propertySpecs.find(item => item.key === filter.kind) ?? { keys: [filter.kind], label: reportedFields[filter.kind] ?? filter.kind };
  return collectParsedProperties(molecule, spec.keys).filter(record => record.parsed && intervalOverlaps(record.parsed, filter));
}

export function moleculeSatisfiesProperty(molecule, filter) {
  return matchingPropertyRecords(molecule, filter).length > 0;
}

function moleculeGroups(molecule) {
  return new Set([
    molecule.functional_group,
    molecule.coupling_structure?.functional_group,
  ].filter(Boolean));
}

function moleculeHydroGroups(molecule) {
  const sites = molecule.coupling_structure?.sites ?? [];
  return sites.flatMap(site => site.groups ?? []);
}

export function parseIntent(text, knownGroups = []) {
  const original = String(text ?? "");
  if (original.length > 500) {
    return { q: original, groups: [], hydrolysis: "", hydroGroup: "", siteCount: "", filters: [], warnings: ["搜索内容最多 500 字，请缩短后再试。"] };
  }
  let work = normalizeText(original);
  const out = { q: "", groups: [], hydrolysis: "", hydroGroup: "", siteCount: "", filters: [], warnings: [] };
  const spans = [];

  if (/不可水解|不能水解|非水解/.test(work)) {
    out.hydrolysis = "no";
    work = work.replace(/不可水解|不能水解|非水解/g, " ");
  } else if (/可水解|水解性/.test(work)) {
    out.hydrolysis = "yes";
    work = work.replace(/可水解|水解性/g, " ");
  }

  for (const [alias, info] of HYDRO_GROUP_ALIASES) {
    const pattern = new RegExp(escapeRegExp(alias), "ig");
    if (!pattern.test(work)) continue;
    out.hydroGroup = info.group;
    if (info.count) out.siteCount = String(info.count);
    work = work.replace(new RegExp(escapeRegExp(alias), "ig"), " ");
  }

  const groupCatalog = [
    ...functionalGroupAliases,
    ...[...knownGroups].filter(Boolean).map(group => [group, group]),
  ].sort((a, b) => b[0].length - a[0].length);
  const seenGroup = new Set();
  for (const [alias, group] of groupCatalog) {
    if (alias.length < 2) continue;
    const pattern = new RegExp(escapeRegExp(alias), "ig");
    if (!pattern.test(work)) continue;
    if (!seenGroup.has(group)) {
      out.groups.push(group);
      seenGroup.add(group);
    }
    work = work.replace(new RegExp(escapeRegExp(alias), "ig"), " ");
  }

  const names = PROPERTY_ALIASES.map(([alias]) => escapeRegExp(alias)).join("|");
  const namePattern = new RegExp(names, "ig");
  const matches = [...work.matchAll(namePattern)];
  for (let index = 0; index < matches.length; index++) {
    const match = matches[index];
    const spec = PROPERTY_ALIASES.find(([alias]) => alias.toLowerCase() === match[0].toLowerCase())[1];
    const end = index + 1 < matches.length ? matches[index + 1].index : work.length;
    let tail = work.slice(match.index + match[0].length, end).replace(/^\s*(?:在|为|是|约|范围|介于|:|=)\s*/, "");
    const parsed = parseFilterTail(tail, spec);
    if (!parsed) {
      out.warnings.push(`“${spec.label}”缺少可识别的范围，请在高级筛选中设置。`);
      spans.push([match.index, match.index + match[0].length]);
      continue;
    }
    out.filters.push(parsed.filter);
    spans.push([match.index, match.index + match[0].length + parsed.used + (work.slice(match.index + match[0].length, end).length - tail.length)]);
  }
  for (const [start, end] of spans.sort((a, b) => b[0] - a[0])) {
    work = work.slice(0, start) + " " + work.slice(end);
  }

  work = work.replace(/\b(?:CAS(?:\s*号)?|CID|SMILES|InChIKey|InChI)\b/ig, " ");
  work = work.replace(/(?:帮我|给我|我想要|我需要|想找|查找|搜索|寻找|找到|找一下|找|请|一种|一些|具有|分子|偶联剂|硅烷|的|并且|而且|同时|以及|包含|含有|含|和|与|在|为|是|约|之间|范围内|范围|之内)/g, " ");
  work = work.replace(/[,，;；。:：、/\s]+/g, " ").trim();
  out.q = work;
  return out;
}

function parseFilterTail(tail, spec) {
  const range = tail.match(new RegExp(`^\\s*(${NUMBER})\\s*(?:°\\s*C|摄氏度|g\\s*/\\s*(?:mL|cm3|cm³|mol))?\\s*(?:-|至|到)\\s*(${NUMBER})\\s*(?:°\\s*C|摄氏度|g\\s*/\\s*(?:mL|cm3|cm³|mol))?`));
  if (range) {
    const lo = Number(range[1]);
    const hi = Number(range[2]);
    if (lo > hi) return { filter: null, used: 0, error: `${spec.label}的最小值不能大于最大值` };
    return {
      filter: { kind: spec.key, label: spec.label, unit: spec.unit, lo, hi, loInclusive: true, hiInclusive: true },
      used: range[0].length,
    };
  }
  const one = tail.match(new RegExp(`^\\s*(<=|>=|≤|≥|<|>|不高于|不超过|不大于|不低于|不少于|至少|最高|最低|小于等于|大于等于|小于|大于|低于|高于|等于)?\\s*(${NUMBER})\\s*(°\\s*C|摄氏度|g\\s*/\\s*(?:mL|cm3|cm³|mol))?\\s*(以下|以内|以上)?`));
  if (!one) return null;
  const operator = one[1] || (one[4] === "以下" || one[4] === "以内" ? "<=" : one[4] === "以上" ? ">=" : "=");
  const value = Number(one[2]);
  const filter = { kind: spec.key, label: spec.label, unit: spec.unit, lo: null, hi: null, loInclusive: true, hiInclusive: true };
  if (["<", "小于", "低于"].includes(operator)) { filter.hi = value; filter.hiInclusive = false; }
  else if (["<=", "≤", "不高于", "不超过", "不大于", "最高", "小于等于"].includes(operator)) filter.hi = value;
  else if ([">", "大于", "高于"].includes(operator)) { filter.lo = value; filter.loInclusive = false; }
  else if ([">=", "≥", "不低于", "不少于", "至少", "最低", "大于等于"].includes(operator)) filter.lo = value;
  else { filter.lo = value; filter.hi = value; }
  return { filter, used: one[0].length };
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function formatFilter(filter) {
  const range = filter.lo != null && filter.hi != null && filter.lo === filter.hi
    ? `= ${formatNumber(filter.lo)}`
    : [
        filter.lo != null ? `${filter.loInclusive === false ? "> " : "≥ "}${formatNumber(filter.lo)}` : "",
        filter.hi != null ? `${filter.hiInclusive === false ? "< " : "≤ "}${formatNumber(filter.hi)}` : "",
      ].filter(Boolean).join(" 且 ");
  return `${filter.label || reportedFields[filter.kind] || filter.kind} ${range}${filter.unit ? ` ${filter.unit}` : ""}`.trim();
}

export function previewProperties(molecule, filters = []) {
  const shown = [];
  const seen = new Set();
  for (const filter of filters) {
    const matches = matchingPropertyRecords(molecule, filter);
    for (const record of matches.slice(0, 2)) {
      const key = record.property + ":" + (record.parsed?.display ?? displayRecordValue(record));
      if (seen.has(key)) continue;
      seen.add(key);
      shown.push({ ...record, matched: true });
    }
  }
  const preferred = ["boiling_point", "density", "specific_gravity", "refractive_index", "flash_point", "melting_point"];
  for (const key of preferred) {
    if (shown.length >= 4) break;
    if (shown.some(item => item.property === key || (key === "density" && item.property === "specific_gravity"))) continue;
    const records = collectParsedProperties(molecule, [key]).filter(item => item.parsed || item.value != null);
    if (!records.length) continue;
    const record = records.find(item => item.parsed && item.parsed.lo === item.parsed.hi) ?? records[0];
    shown.push({ ...record, matched: false });
  }
  return shown;
}

export function textMatchesMolecule(molecule, term) {
  if (!term) return true;
  const needles = term.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!needles.length) return true;
  const terms = [
    molecule.id, molecule.label, molecule.name, molecule.english_name, molecule.iupac_name,
    molecule.cas, molecule.pubchem_cid, molecule.formula, molecule.smiles, molecule.inchikey,
  ];
  for (const reference of molecule.catalog_references ?? []) terms.push(reference.name, reference.code, reference.catalog);
  const haystack = terms.map(value => String(value ?? "").toLowerCase());
  return needles.every(needle => haystack.some(value => value.includes(needle)));
}

export function matchesIntent(molecule, intent) {
  if (intent.groups.length) {
    const groups = moleculeGroups(molecule);
    if (!intent.groups.some(group => groups.has(group))) return false;
  }
  const structure = molecule.coupling_structure;
  if (intent.hydrolysis) {
    if (!structure || structure.hydrolyzable !== (intent.hydrolysis === "yes")) return false;
  }
  if (intent.hydroGroup || intent.siteCount) {
    const sites = structure?.sites ?? [];
    const needed = Number(intent.siteCount);
    const ok = sites.some(site => {
      if (intent.hydroGroup && !(site.groups ?? []).some(entry => entry.group === intent.hydroGroup)) return false;
      if (intent.siteCount && site.count !== needed) return false;
      return true;
    });
    if (!ok) return false;
  }
  for (const filter of intent.filters) {
    if (!moleculeSatisfiesProperty(molecule, filter)) return false;
  }
  return textMatchesMolecule(molecule, intent.q);
}

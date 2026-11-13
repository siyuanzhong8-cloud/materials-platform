function addOptions(select, values, numeric = false, label = value => value) {
  const unique = [...new Set(values)].filter(value => value != null);
  unique.sort(numeric ? (a, b) => a - b : (a, b) => a.localeCompare(b, "zh-CN"));
  for (const value of unique) select.add(new Option(label(value), String(value)));
}

export function createCouplingFilters(molecules, onChange) {
  const hydrolysis = document.querySelector("#hydrolysis");
  const group = document.querySelector("#group");
  const linkerType = document.querySelector("#linker-type");
  const linkerLength = document.querySelector("#linker-length");
  const hydroGroup = document.querySelector("#hydro-group");
  const siteCount = document.querySelector("#site-count");
  const hydroCount = document.querySelector("#hydro-count");
  const clear = document.querySelector("#clear-coupling");
  const inputs = [hydrolysis, group, linkerType, linkerLength, hydroGroup, siteCount, hydroCount];
  const structures = molecules.map(m => m.coupling_structure).filter(Boolean);
  const sites = structures.flatMap(data => data.sites);
  const linkers = sites.flatMap(site => site.linkers);

  addOptions(group, molecules.map(m => m.coupling_structure?.functional_group ?? m.functional_group));
  addOptions(linkerType, linkers.flatMap(linker => linker.tags));
  if (structures.some(data => data.linker_status === "no_function")) {
    linkerType.add(new Option("无独立有机官能团", "no_function"));
  }
  if (structures.some(data => data.linker_status === "unresolved")) {
    linkerType.add(new Option("待复核", "unresolved"));
  }
  addOptions(linkerLength, linkers.map(linker => linker.length), true,
    length => length === 0 ? "0（直接相连）" : length + " 个重原子");
  addOptions(hydroGroup, sites.flatMap(site => site.groups.map(entry => entry.group)));
  addOptions(siteCount, sites.map(site => site.count), true);
  addOptions(hydroCount, structures.map(data => data.hydrolyzable_count), true);

  function refresh() {
    clear.disabled = inputs.every(input => input.value === "");
    onChange();
  }

  for (const input of inputs) input.addEventListener("change", refresh);
  clear.addEventListener("click", () => {
    for (const input of inputs) input.value = "";
    refresh();
    hydrolysis.focus();
  });
  clear.disabled = true;

  return {
    matcher() {
      const [hydrolysisValue, functional, linkerTag, length, siliconGroup, single, total] =
        inputs.map(input => input.value);
      const specialLinker = ["no_function", "unresolved"].includes(linkerTag);
      const needsLinker = length !== "" || (linkerTag !== "" && !specialLinker);
      const needsSite = siliconGroup !== "" || single !== "" || needsLinker;

      return molecule => {
        const data = molecule.coupling_structure;
        if (functional && functional !== (data?.functional_group ?? molecule.functional_group)) return false;
        if (!data) return !hydrolysisValue && !linkerTag && !needsSite && !total;
        if (hydrolysisValue && data.hydrolyzable !== (hydrolysisValue === "yes")) return false;
        if (total !== "" && data.hydrolyzable_count !== Number(total)) return false;
        if (specialLinker && linkerTag !== data.linker_status) return false;
        if (!needsSite) return true;
        return data.sites.some(site => {
          if (siliconGroup && !site.groups.some(entry => entry.group === siliconGroup)) return false;
          if (single !== "" && site.count !== Number(single)) return false;
          if (!needsLinker) return true;
          return site.linkers.some(linker => (!linkerTag || specialLinker || linker.tags.includes(linkerTag)) &&
            (length === "" || linker.length === Number(length)));
        });
      };
    },
  };
}

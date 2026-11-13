const periods = [
  ["H", ...Array(16).fill(""), "He"],
  ["Li", "Be", ...Array(10).fill(""), "B", "C", "N", "O", "F", "Ne"],
  ["Na", "Mg", ...Array(10).fill(""), "Al", "Si", "P", "S", "Cl", "Ar"],
  "K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr".split(" "),
  "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe".split(" "),
  "Cs Ba La Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn".split(" "),
  "Fr Ra Ac Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split(" "),
  [],
  ["", "", "", ..."Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu".split(" ")],
  ["", "", "", ..."Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr".split(" ")],
];
const atomicOrder = (
  "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr " +
  "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu " +
  "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr " +
  "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split(" ");

export function formulaElements(formula) {
  return new Set((formula?.match(/[A-Z][a-z]?/g) ?? []).filter(symbol => atomicOrder.includes(symbol)));
}

export function createElementFilter(grid, chips, mode, clear, available, onChange) {
  const selected = new Set();
  const buttons = new Map();

  function renderSelection() {
    for (const [symbol, button] of buttons) {
      button.setAttribute("aria-pressed", String(selected.has(symbol)));
      button.classList.toggle("selected", selected.has(symbol));
    }
    chips.replaceChildren();
    if (selected.size === 0) chips.textContent = "全部";
    for (const symbol of selected) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.textContent = symbol + " ×";
      chip.setAttribute("aria-label", "移除 " + symbol);
      chip.addEventListener("click", () => {
        toggle(symbol);
        buttons.get(symbol).focus();
      });
      chips.append(chip);
    }
    clear.disabled = selected.size === 0;
  }

  function toggle(symbol) {
    if (selected.has(symbol)) selected.delete(symbol);
    else selected.add(symbol);
    renderSelection();
    onChange();
  }

  function place(element, row, col) {
    element.style.gridRow = row;
    element.style.gridColumn = col;
    grid.append(element);
  }

  for (let col = 1; col <= 18; col++) {
    const label = document.createElement("span");
    label.className = "group-number";
    label.textContent = col;
    place(label, 1, col);
  }
  periods.forEach((period, row) => {
    period.forEach((symbol, col) => {
      if (!symbol) return;
      const button = document.createElement("button");
      button.type = "button";
      button.disabled = !available.has(symbol);
      button.title = symbol + (button.disabled ? " · 暂无数据" : " · 已收录");
      button.setAttribute("aria-label", symbol);
      const number = document.createElement("small");
      number.textContent = atomicOrder.indexOf(symbol) + 1;
      number.setAttribute("aria-hidden", "true");
      const name = document.createElement("strong");
      name.textContent = symbol;
      button.append(number, name);
      button.addEventListener("click", () => toggle(symbol));
      buttons.set(symbol, button);
      place(button, row + 2, col + 1);
    });
  });
  ["镧系", "锕系"].forEach((series, index) => {
    const label = document.createElement("span");
    label.className = "series-label";
    label.textContent = series;
    place(label, index + 10, "1 / 4");
  });
  clear.addEventListener("click", () => {
    const first = selected.values().next().value;
    selected.clear();
    renderSelection();
    if (first) buttons.get(first).focus();
    onChange();
  });
  mode.addEventListener("change", onChange);
  renderSelection();

  return elements => {
    if (selected.size === 0) return true;
    if (mode.value === "exact" && selected.size !== elements.size) return false;
    return [...selected].every(symbol => elements.has(symbol));
  };
}

{
  const home = document.getElementById("home");
  const homeTab = document.getElementById("home-tab");
  const silaneBtn = document.getElementById("silane-tab");
  const silaneSection = document.getElementById("silane");
  const tabs = [
    {hashes: ["#catalyst", "#catalysis"], button: "cat", section: "catalysis"},
    {hashes: ["#glass"], button: "gla", section: "glass"},
  ].map((tab) => ({
    hashes: tab.hashes,
    button: document.getElementById(tab.button),
    section: document.getElementById(tab.section),
  }));

  function currentKey() {
    const hash = location.hash || "#home";
    if (hash === "#silane") return "silane";
    if (hash === "#home" || hash === "#portal" || hash === "#") return "#home";
    for (const tab of tabs) {
      if (tab.hashes.includes(hash)) return tab.hashes[0];
    }
    return "#home";
  }

  function showCurrentTab() {
    if ((location.hash || "") === "#silane") {
      location.replace("/silane/");
      return;
    }
    const key = currentKey();
    const isHome = key === "#home";
    if (home) home.hidden = !isHome;
    if (silaneSection) silaneSection.hidden = true;
    for (const tab of tabs) {
      const active = !isHome && tab.hashes[0] === key;
      if (tab.button) {
        tab.button.classList.toggle("active", active);
        tab.button.setAttribute("aria-pressed", String(active));
      }
      if (tab.section) tab.section.hidden = !active;
    }
    if (homeTab) {
      homeTab.classList.toggle("active", isHome);
      homeTab.setAttribute("aria-current", isHome ? "page" : "false");
    }
    if (silaneBtn) {
      silaneBtn.classList.remove("active");
      silaneBtn.setAttribute("aria-pressed", "false");
    }
  }

  for (const tab of tabs) {
    if (!tab.button) continue;
    tab.button.onclick = () => {
      location.hash = tab.hashes[0];
      showCurrentTab();
    };
  }
  if (silaneBtn) {
    silaneBtn.onclick = (event) => {
      event.preventDefault();
      location.href = "/silane/";
    };
  }
  window.addEventListener("hashchange", showCurrentTab);
  showCurrentTab();
}

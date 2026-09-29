import { $, guard } from "./api.js";
import * as analyze from "./analyze.js";
import * as classes from "./classes.js";
import * as depth from "./depth.js";
import * as labeling from "./labeling.js";
import * as racks from "./racks.js";
import * as train from "./train.js";

const TABS = { analyze, racks, classes, label: labeling, train, depth };

for (const m of Object.values(TABS)) m.init();

const show = guard(async (name) => {
  if (!TABS[name]) name = "analyze";
  document.querySelectorAll("nav.tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  document.querySelectorAll("section.tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${name}`));
  await TABS[name].onShow();
});

window.addEventListener("hashchange", () => show(location.hash.slice(1)));
show(location.hash.slice(1));
$("#toast").onclick = () => ($("#toast").className = "toast");

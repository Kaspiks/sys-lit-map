import { renderLayout } from "./layout.js";
import { $, api, figure, h } from "./shared.js";

const FIGURES = {
  databases: "Which databases found each record",
  years: "Publication years",
  extraction: "How running experience is reported, and which characteristics were tested as moderators",
};

renderLayout("PRISMA flow");

const noDiagram = h("p", { class: "muted" }, "No flow diagram yet: run the pipeline first.");

$("flow").replaceChildren(h("img", {
  src: "/api/figures/prisma_flow.svg",
  alt: "PRISMA-ScR flow diagram",
  onerror: () => $("flow").replaceChildren(noDiagram),
}));

$("figures").replaceChildren(...Object.entries(FIGURES).map(([name, caption]) => figure(name, caption)));
$("build").addEventListener("click", build);

async function build() {
  const button = $("build");

  button.disabled = true;
  button.textContent = "Generating…";

  try {
    const { files } = await api("/api/report", {});
    $("written").replaceChildren(...files.map((file) => h("li", { class: "mono" }, file)));
  } catch (error) {
    $("written").replaceChildren(h("li", { class: "error" }, error.message));
  } finally {
    button.disabled = false;
    button.textContent = "Generate report files";
  }
}

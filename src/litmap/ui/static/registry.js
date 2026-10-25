import { renderLayout } from "./layout.js";
import {
  $, STATUS_NAMES, api, badge, databaseName, errorText, h, param, readable, table, trailUrl,
} from "./shared.js";

const SHOWN_ROWS = 500;  // enough for the whole pool; a longer list is narrowed with the search box

const view = { rows: [], status: param("status") ?? "all", search: "" };

start();

async function start() {
  renderLayout("Citation registry");

  try {
    view.rows = await api("/api/registry");
  } catch (error) {
    $("registry").replaceChildren(errorText(error));

    return;
  }

  $("search").addEventListener("input", (event) => {
    view.search = event.target.value.trim().toLowerCase();
    render();
  });

  render();
}

function render() {
  const matching = view.rows.filter(matchesSearch);
  const shown = matching.filter((row) => view.status === "all" || row.status === view.status);

  $("filters").replaceChildren(...["all", ...Object.keys(STATUS_NAMES)].map((status) => h("button", {
    type: "button",
    class: "chip",
    "aria-pressed": String(status === view.status),
    onclick: () => {
      view.status = status;
      render();
    },
  }, status === "all" ? "All" : STATUS_NAMES[status],
  ` ${matching.filter((row) => status === "all" || row.status === status).length}`)));

  $("registry").replaceChildren(
    h("p", { class: "muted" }, `${shown.length} of ${view.rows.length} deduplicated records`
      + (shown.length > SHOWN_ROWS ? `; the first ${SHOWN_ROWS} are shown` : "")),
    table(["Record", "Title", "Year", "Found in", "Status"], shown.slice(0, SHOWN_ROWS).map((row) => [
      h("td", { class: "mono" }, h("a", { href: trailUrl(row.record_id) }, row.record_id)),
      row.title ?? "–",
      row.year ?? "–",
      row.databases.split("+").map(databaseName).join(", "),
      h("td", {}, badge(row.status), row.detail && h("div", { class: "muted" }, readable(row.detail))),
    ])),
  );
}

function matchesSearch(row) {
  if (!view.search) return true;

  return [row.record_id, row.title, row.year].some((value) => String(value ?? "").toLowerCase()
    .includes(view.search));
}

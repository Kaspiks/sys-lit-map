import { renderLayout } from "./layout.js";
import { $, api, badge, errorText, h, param, present, table, trailUrl } from "./shared.js";

const LABELS = ["include", "maybe", "exclude"];

start();

async function start() {
  const session = await renderLayout("Second screener");

  try {
    const query = param("screener") ? `?screener=${encodeURIComponent(param("screener"))}` : "";
    render(await api(`/api/agreement${query}`), session);
  } catch (error) {
    $("adjudication").replaceChildren(errorText(error));
  }
}

function render(data, session) {
  const result = data.agreement;

  $("adjudication").replaceChildren(...present([
    intro(data, session),
    result && h("nav", { class: "tabs small", "aria-label": "Second screeners" }, data.second_screeners.map(
      (initials) => h("a", {
        href: `?screener=${initials}`,
        "aria-current": initials === result.second ? "page" : null,
      }, initials),
    )),
    result && summary(result),
    result && h("div", { class: "trail-grid" }, labelTable(result), disagreements(result)),
  ]));
}

function intro(data, session) {
  const input = h("input", {
    type: "text",
    name: "screener",
    placeholder: "Initials",
    "aria-label": "Initials",
    maxlength: "4",
    required: true,
    pattern: "[A-Za-z]{2,4}",
  });

  return h("section", { class: "card panel" },
    h("h2", {}, "Blind second screening"),
    h("p", { class: "panel-note" },
      `A second screener screens a fixed random sample of ${data.sample_size} records from the pool: `
      + `in random order, without the ranker and without seeing ${session.screener}'s decisions. `
      + "Agreement is measured on relevance (include or maybe, against exclude) with Cohen's kappa."),
    h("form", { class: "lookup", action: "/", method: "get" },
      input,
      h("button", { type: "submit" }, "Start blind screening"),
    ),
  );
}

function summary(result) {
  const agreement = result.agreement === null ? "–" : `${Math.round(result.agreement * 100)}%`;

  return h("section", { class: "stats" },
    stat("Cohen's kappa", result.kappa ?? "–", "Agreement beyond chance"),
    stat("Raw agreement", agreement, "Same relevance decision"),
    stat("Screened by both", result.screened_by_both, `${result.primary} and ${result.second}`),
    stat(`Screened by ${result.second}`, `${result.screened_by_second} / ${result.sample_size}`,
      h("a", { href: `/?screener=${result.second}` }, `Continue as ${result.second}`)),
  );
}

function stat(label, value, note) {
  return h("div", { class: "card stat" },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, String(value)),
    h("div", { class: "stat-note" }, note),
  );
}

function labelTable(result) {
  const rows = LABELS.map((mine) => [
    h("th", {}, `${result.primary}: ${mine}`),
    ...LABELS.map((theirs) => h("td", { class: mine === theirs ? "agree" : null },
      String(result.table[mine][theirs]))),
  ]);

  return h("section", { class: "card trail-section" },
    h("h2", {}, "Decisions side by side"),
    h("p", { class: "muted" }, "Rows: your decision. Columns: the second screener's."),
    table(["", ...LABELS.map((label) => `${result.second}: ${label}`)], rows),
  );
}

function disagreements(result) {
  const items = result.disagreements.map((item) => h("li", {},
    h("a", { href: trailUrl(item.record_id) }, item.title ?? item.record_id),
    h("div", { class: "muted" },
      h("span", { class: "mono" }, item.record_id), " · ",
      `${result.primary} `, badge(item.primary), ` ${result.second} `, badge(item.second)),
  ));

  return h("section", { class: "card trail-section" },
    h("h2", {}, `${items.length} disagreements on relevance`),
    h("p", { class: "muted" }, items.length
      ? "Discuss each one; a changed decision is an undo and a new decision, so the log keeps both."
      : `${result.second} and you agree on every record you both screened.`),
    h("ul", { class: "disagreements" }, items),
  );
}

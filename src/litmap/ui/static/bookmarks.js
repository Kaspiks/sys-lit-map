import { renderLayout } from "./layout.js";
import {
  $, STATUS_NAMES, api, badge, errorText, externalLinks, h, param, readable, timeAgo, trailUrl,
} from "./shared.js";

const STATUS_TEXT = { ...STATUS_NAMES, not_in_run: "Not in this search run" };

const screener = param("screener")?.toUpperCase() ?? null;  // a second screener's own bookmarks

const view = { marks: [], status: "all", search: "" };

start();

async function start() {
  renderLayout("Bookmarks", { screener });

  try {
    view.marks = await api(`/api/bookmarks${screener ? `?screener=${encodeURIComponent(screener)}` : ""}`);
  } catch (error) {
    $("bookmarks").replaceChildren(errorText(error));

    return;
  }

  $("search").addEventListener("input", (event) => {
    view.search = event.target.value.trim().toLowerCase();
    render();
  });

  render();
}

function render() {
  const matching = view.marks.filter(matchesSearch);
  const shown = matching.filter((mark) => view.status === "all" || statusOf(mark) === view.status);
  const statuses = ["all", ...new Set(view.marks.map(statusOf))];

  $("filters").replaceChildren(...statuses.map((status) => h("button", {
    type: "button",
    class: "chip",
    "aria-pressed": String(status === view.status),
    onclick: () => {
      view.status = status;
      render();
    },
  }, status === "all" ? "All" : STATUS_TEXT[status],
  ` ${matching.filter((mark) => status === "all" || statusOf(mark) === status).length}`)));

  const empty = view.marks.length
    ? "No bookmarks match."
    : "No bookmarks yet. Press B on the screening page to bookmark the record you're reading.";

  $("bookmarks").replaceChildren(...(shown.length ? shown.map(card) : [h("p", { class: "muted" }, empty)]));
}

function card(mark) {
  const meta = [mark.journal, mark.year, ...externalLinks(mark)].filter(Boolean);
  // after a new search the record has a new id, but its DOI still finds it
  const link = trailUrl(mark.status ? mark.record_id : (mark.doi ?? mark.record_id));

  return h("article", { class: "card bookmark-card" },
    h("div", { class: "tags" },
      h("span", { class: "tag dark mono" }, mark.record_id),
      badge(statusOf(mark), STATUS_TEXT[statusOf(mark)]),
      mark.detail && h("span", { class: "tag" }, readable(mark.detail)),
    ),
    h("h2", {}, h("a", { href: link }, mark.title ?? mark.record_id)),
    h("p", { class: "meta" }, meta.map((item) => h("span", {}, item))),
    noteField(mark),
    h("footer", { class: "record-footer" },
      h("span", {}, `Bookmarked ${timeAgo(mark.timestamp)}`),
      h("button", { type: "button", class: "small", onclick: () => remove(mark) }, "Remove"),
    ),
  );
}

/** Why the study is interesting. Saved when you leave the field or press Enter: a new bookmark event. */
function noteField(mark) {
  const status = h("span", { class: "muted", role: "status" });

  const input = h("input", {
    type: "text",
    value: mark.note ?? "",
    placeholder: "Why it's interesting",
    "aria-label": `Note for ${mark.title ?? mark.record_id}`,
  });

  input.addEventListener("change", async () => {
    try {
      const body = { record_id: mark.record_id, screener, bookmarked: true, note: input.value };
      mark.note = (await api("/api/bookmarks", body)).note;
      status.textContent = "Saved";
    } catch (error) {
      status.textContent = error.message;
    }
  });

  return h("div", { class: "bookmark-note" }, input, status);
}

async function remove(mark) {
  try {
    await api("/api/bookmarks", { record_id: mark.record_id, screener, bookmarked: false });
    view.marks = view.marks.filter((other) => other !== mark);
    render();
  } catch (error) {
    $("bookmarks").prepend(errorText(error));
  }
}

function statusOf(mark) {
  return mark.status ?? "not_in_run";
}

function matchesSearch(mark) {
  if (!view.search) return true;

  return [mark.record_id, mark.title, mark.note, mark.journal, mark.year]
    .some((value) => String(value ?? "").toLowerCase().includes(view.search));
}

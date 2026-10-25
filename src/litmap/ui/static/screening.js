import { renderLayout } from "./layout.js";
import {
  $, api, badge, capitalize, databaseName, externalLinks, h, marked, param, present, timeAgo, trailUrl,
  yearTag,
} from "./shared.js";

const QUEUE_NAMES = {
  random_seed_set: "Seed set: random order",
  ranker: "Ranker order, scores hidden",
  random: "Random order",
  second_screener_sample: "Blind sample",
};

const LONG_ABSTRACT_WORDS = 600;
const SWIPE_PIXELS = 80;
const TOAST_MILLISECONDS = 4000;

const screener = param("screener")?.toUpperCase() ?? null;  // set for a second screener

const view = {
  state: null,
  session: null,
  blockIndex: {},  // concept block -> highlight colour
  mode: "decide",  // decide | reason | note
  pendingReason: null,
  shownAt: Date.now(),
  busy: false,
};

start().catch((error) => $("record").replaceChildren(h("p", { class: "error" }, error.message)));

async function start() {
  const chips = [h("span", { class: "chip" }, "Title & abstract"), sessionTimer()];
  const query = screener ? `?screener=${encodeURIComponent(screener)}` : "";

  const [session, state] = await Promise.all([
    renderLayout("Screening", { screener, chips }),
    api(`/api/state${query}`),
  ]);

  view.session = session;
  render(state);
  document.addEventListener("keydown", onKey);
  listenForSwipes($("record"));
}

function render(state) {
  const recordChanged = state.record?.record_id !== view.state?.record?.record_id;
  const retrained = view.state && state.progress.ranker_iteration !== view.state.progress.ranker_iteration;

  if (retrained) {
    const iteration = state.progress.ranker_iteration;
    showToast(`Ranker retrained (iteration ${iteration}): what follows is in its new order`);
  }

  view.state = state;
  view.blockIndex = Object.fromEntries(state.legend.map((entry, index) => [entry.block, index]));
  view.mode = "decide";
  view.pendingReason = null;

  if (recordChanged) {
    view.shownAt = Date.now();
    window.scrollTo(0, 0);
  }

  renderStats(state.progress);
  renderTermStrip(state.legend);
  renderBanners(state.progress);
  renderRecord(state.record, state.queue);
  renderFeed(state.feed);
  renderReasonKey(state.reasons);
  renderActions();
}

// Decisions

function decide(label, reason = null, note = null) {
  const seconds = Math.round((Date.now() - view.shownAt) / 1000);
  const recordId = view.state.record.record_id;

  return send("/api/decisions", { record_id: recordId, label, reason, note, seconds, screener });
}

function undo() {
  return send("/api/undo", { screener });
}

async function send(path, body) {
  if (view.busy) return;

  view.busy = true;

  try {
    render(await api(path, body));
  } catch (error) {
    showToast(error.message);

    if (error.state) render(error.state);
  } finally {
    view.busy = false;
  }
}

function askReason() {
  view.mode = "reason";
  renderActions();
}

function chooseReason(reason) {
  if (!reason.note_required) return decide("exclude", reason.code);

  view.mode = "note";
  view.pendingReason = reason;
  renderActions();
}

function cancel() {
  view.mode = "decide";
  view.pendingReason = null;
  renderActions();
}

// Keyboard and swipes

function onKey(event) {
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const action = keyAction(event.key.toLowerCase(), event.target);

  if (action) {
    event.preventDefault();
    action();
  }
}

function keyAction(key, target) {
  if (view.busy) return null;  // a key pressed before the reply arrives would act on the old record
  if (key === "escape") return cancel;
  if (target.matches("input, textarea")) return null;
  if (view.mode === "reason") return reasonForKey(key);
  if (key === "u" || key === "backspace") return undo;
  if (!view.state.record || view.mode !== "decide") return null;
  if (key === "e" || key === "arrowleft") return askReason;
  if (key === "m" || key === "arrowup") return () => decide("maybe");
  if (key === "i" || key === "arrowright") return () => decide("include");

  return null;
}

function reasonForKey(key) {
  const reason = view.state.reasons[Number(key) - 1];

  return reason ? () => chooseReason(reason) : null;
}

/** Swipe left to exclude, right to include. Vertical swipes stay free for scrolling. */
function listenForSwipes(element) {
  let start = null;

  element.addEventListener("touchstart", (event) => { start = event.touches[0]; }, { passive: true });
  element.addEventListener("touchend", (event) => {
    const end = event.changedTouches[0];
    const dx = end.clientX - (start?.clientX ?? end.clientX);
    const dy = end.clientY - (start?.clientY ?? end.clientY);
    const horizontal = Math.abs(dx) >= SWIPE_PIXELS && Math.abs(dx) > 2 * Math.abs(dy);

    start = null;

    if (!horizontal || view.mode !== "decide" || !view.state.record) return;

    if (dx < 0) {
      askReason();
    } else {
      decide("include");
    }
  });
}

// Header, progress and terms

function sessionTimer() {
  const openedAt = Date.now();
  const time = h("strong", {}, "00:00:00");

  setInterval(() => {
    const seconds = Math.floor((Date.now() - openedAt) / 1000);
    const parts = [seconds / 3600, (seconds % 3600) / 60, seconds % 60];
    time.textContent = parts.map((part) => String(Math.floor(part)).padStart(2, "0")).join(":");
  }, 1000);

  return h("span", { class: "chip", title: "Time since you opened this page" }, "Session ", time);
}

function renderStats(progress) {
  const percent = progress.pool ? Math.round((progress.screened / progress.pool) * 100) : 0;
  const screened = stat("Screened", progress.screened, progress.pool, `${percent}% of the ${poolName()}`);

  const pace = stat("Pace", progress.average_seconds ? `${progress.average_seconds} s` : "–", null,
    "Per record, on average");

  if (progress.blind) {
    $("stats").replaceChildren(screened, pace);

    return;
  }

  $("stats").replaceChildren(
    stat("Stopping rule 1", progress.irrelevant_in_a_row, progress.stop_after, "Irrelevant in a row"),
    stat("Stopping rule 2", progress.key_studies_found, progress.key_studies_total,
      "Key studies found. Which ones stays hidden", "Blind"),
    screened,
    progress.seed_set_done
      ? stat("Ranker", rankerValue(progress), null, retrainNote(progress))
      : seedSetStat(progress),
    pace,
  );
}

function poolName() {
  return view.state.progress.blind ? "blind sample" : "screening pool";
}

function seedSetStat(progress) {
  const note = "Random order, trains the ranker";

  return stat("Seed set", progress.seed_set_screened, progress.seed_set_size, note);
}

function rankerValue(progress) {
  return progress.ranker_iteration ? `Iteration ${progress.ranker_iteration}` : "Not trained yet";
}

function retrainNote(progress) {
  if (progress.retrain_in === 0) return "Retrains after your next decision";

  return `Retrains in ${progress.retrain_in} decisions`;
}

function stat(label, value, total, note, tag = null) {
  const meter = total
    ? h("div", { class: "meter" }, h("span", { style: `width: ${Math.min(100, (value / total) * 100)}%` }))
    : null;

  return h("div", { class: "card stat" },
    h("div", { class: "stat-label" }, label, tag && h("span", { class: "tag" }, tag)),
    h("div", { class: "stat-value" }, String(value), total ? h("small", {}, ` / ${total}`) : null),
    meter,
    h("div", { class: "stat-note" }, note),
  );
}

function renderTermStrip(legend) {
  $("term-strip").replaceChildren(...present([
    h("span", { class: "strip-label" }, "Highlighted search.yaml terms"),
    legend.map((entry, index) => h("span", { class: "term-chip", title: entry.terms.join(", ") },
      h("mark", { class: `term term-${index}` }, capitalize(entry.block)),
      h("span", {}, entry.terms.join(", ")),
    )),
    h("a", { href: "/config" }, "Config"),
  ]));
}

function renderBanners(progress) {
  const missingKeyStudies = progress.key_studies_total - progress.key_studies_in_pool;
  const primary = view.session.screener;

  $("banners").replaceChildren(...present([
    progress.blind && h("p", { class: "banner" },
      `Blind second screening as ${progress.screener}: a fixed random sample of ${progress.pool} records, `
      + `without the ranker and without ${primary}'s decisions. `,
      h("a", { href: `/adjudication?screener=${progress.screener}` }, "Compare with " + primary)),
    !progress.blind && progress.stopping_rule_met && h("p", { class: "banner" },
      `Stopping rule met: ${progress.stop_after} irrelevant records in a row and every key study found. `
      + "You can stop here or keep screening."),
    !progress.blind && missingKeyStudies > 0 && h("p", { class: "banner warning" },
      `${missingKeyStudies} key studies aren't in the screening pool, so the stopping rule can't be met. `
      + "Check them in the record trail."),
  ]));
}

// The record

function renderRecord(record, queue) {
  if (!record) {
    $("record").replaceChildren(h("div", { class: "done" },
      h("h2", {}, view.state.progress.blind ? "Your sample is screened" : "Every record is screened"),
      h("p", {}, "Your decisions are in logs/screening.jsonl."),
    ));

    return;
  }

  const recordType = record.pub_types?.split("; ").slice(0, 2).join(", ");
  const meta = [record.journal, record.year, ...externalLinks(record)].filter(Boolean);

  $("record").replaceChildren(...present([
    !record.sections.length && h("p", { class: "notice" },
      "No abstract. Decide on the title, or choose Maybe to check the full text."),
    h("div", { class: "tags" },
      h("span", { class: "tag dark mono" }, record.record_id),
      h("span", { class: "tag" }, sourcesText(record)),
      recordType && h("span", { class: "tag" }, recordType),
      yearTag(record),
      h("span", { class: "tag" }, QUEUE_NAMES[queue] ?? queue),
    ),
    h("h1", { class: "record-title" }, marked(record.title, view.blockIndex)),
    h("p", { class: "meta" }, meta.map((item) => h("span", {}, item))),
    conceptCheck(record.concept_check),
    h("div", { class: "abstract-bar" },
      h("h2", {}, "Abstract"),
      record.sections.length > 0 && h("span", { class: "muted" }, `${record.word_count} words`),
      h("button", { type: "button", class: "small", onclick: () => copyCitation(record) }, "Copy citation"),
    ),
    abstract(record),
    authors(record.authors),
    h("footer", { class: "record-footer" },
      h("span", { class: "mono" }, record.record_id),
      h("a", { href: trailUrl(record.record_id) }, "Record trail"),
    ),
  ]));
}

function sourcesText(record) {
  const databases = record.databases.map(databaseName).join(", ");

  if (record.n_records > 1) return `Merged from ${record.n_records} records: ${databases}`;

  return `Found in ${databases}`;
}

function conceptCheck(blocks) {
  const found = blocks.filter((block) => Object.keys(block.terms).length).length;

  return h("section", { class: "concept-check", "aria-label": "Concept check" },
    h("header", {},
      h("h2", {}, "Concept check"),
      h("span", { class: "muted" }, `${found} of ${blocks.length} search.yaml blocks found`),
    ),
    h("div", { class: "concept-grid" }, blocks.map(conceptCard)),
    blocks.filter((block) => block.suggested_reason).map((block) => h("p", { class: "concept-hint" },
      `No ${block.block} terms. If it really has none, exclude with `,
      h("kbd", {}, "E"), " ", h("kbd", {}, String(block.suggested_reason.key)),
      ` ${block.suggested_reason.label}.`,
    )),
  );
}

function conceptCard(block) {
  const terms = Object.entries(block.terms);

  return h("div", { class: `concept-card ${terms.length ? "found" : "missing"}` },
    h("h3", {},
      h("mark", { class: `term term-${view.blockIndex[block.block]}` }, capitalize(block.block)),
      terms.length ? badge("match", "Match") : badge("missing", "Missing"),
    ),
    h("p", {}, terms.map(([term, count]) => `${term} (${count})`).join(", ") || "None of its terms"),
  );
}

function abstract(record) {
  if (!record.sections.length) return null;

  const body = h("div", { class: "abstract" }, record.sections.map((section) => [
    section.label && h("h3", {}, section.label),
    h("p", {}, marked(section.segments, view.blockIndex)),
  ]));

  if (record.word_count <= LONG_ABSTRACT_WORDS) return body;

  body.classList.add("folded");

  const showAll = h("button", {
    type: "button",
    onclick: () => {
      body.classList.remove("folded");
      showAll.remove();
    },
  }, `Show the whole abstract (${record.word_count} words)`);

  return h("div", {}, body, showAll);
}

function authors(names) {
  if (!names.length) return null;

  if (names.length <= 3) return h("p", { class: "authors" }, names.join(", "));

  return h("details", { class: "authors" },
    h("summary", {}, `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`),
    h("p", {}, names.join(", ")),
  );
}

function copyCitation(record) {
  navigator.clipboard.writeText(citationText(record)).then(
    () => showToast("Citation copied"),
    () => showToast("Copying isn't allowed in this browser"),
  );
}

/** "Lee A et al. (2020). Title. Journal. https://doi.org/…" */
function citationText(record) {
  const names = record.authors.length > 3 ? `${record.authors[0]} et al.` : record.authors.join(", ");
  const byline = [names, record.year && `(${record.year})`].filter(Boolean).join(" ");
  const title = record.title.map(([text]) => text).join("");
  const parts = [byline, title, record.journal].filter(Boolean).map((part) => `${part.replace(/\.$/, "")}.`);

  return [...parts, record.doi && `https://doi.org/${record.doi}`].filter(Boolean).join(" ");
}

// Side panels

function renderFeed(feed) {
  const items = feed.map((item) => h("li", { class: `feed-item ${item.event}` },
    h("div", { class: "feed-head" },
      h("span", { class: "mono" }, `#${item.number} · ${item.record_id}`),
      item.event === "undo" ? badge("undo", `Undid ${item.label}`) : badge(item.label),
    ),
    h("a", { class: "feed-title", href: trailUrl(item.record_id) }, item.title ?? item.record_id),
    h("div", { class: "muted" },
      present([item.event === "decision" && item.reason, timeAgo(item.timestamp)]).join(" · ")),
  ));

  $("feed").replaceChildren(...(items.length ? items : [h("li", { class: "muted" }, "Nothing yet.")]));
}

function renderReasonKey(reasons) {
  $("reason-key").replaceChildren(...reasons.map((reason, index) => h("li", {},
    h("kbd", {}, String(index + 1)), reason.label,
  )));
}

// Decision bar

function renderActions() {
  const bar = $("actions");

  if (!view.state.record) {
    bar.replaceChildren(actionButton("Undo", "U", undo));
  } else if (view.mode === "reason") {
    bar.replaceChildren(
      ...view.state.reasons.map((reason, index) => (
        actionButton(reason.label, index + 1, () => chooseReason(reason), "reason")
      )),
      actionButton("Cancel", "Esc", cancel),
    );
  } else if (view.mode === "note") {
    bar.replaceChildren(noteForm(view.pendingReason));
  } else {
    bar.replaceChildren(
      actionButton("Undo", "U", undo),
      actionButton("Exclude", "E ←", askReason, "exclude", `then 1–${view.state.reasons.length}`),
      actionButton("Maybe", "M ↑", () => decide("maybe"), "maybe", "check the full text"),
      actionButton("Include", "I →", () => decide("include"), "include", "goes to full-text reading"),
    );
  }
}

function actionButton(label, key, onclick, kind = "", hint = null) {
  return h("button", { type: "button", class: kind, onclick },
    h("kbd", {}, String(key)),
    h("span", { class: "action-text" }, label, hint && h("small", {}, hint)),
  );
}

function noteForm(reason) {
  const input = h("input", {
    type: "text",
    placeholder: "Why exclude it?",
    "aria-label": `Note for ${reason.label}`,
  });
  const error = h("span", { class: "error", role: "alert" });

  const submit = (event) => {
    event.preventDefault();

    const note = input.value.trim();

    if (!note) {
      error.textContent = "Add a note first.";

      return;
    }

    decide("exclude", reason.code, note);
  };

  input.addEventListener("input", () => { error.textContent = ""; });
  queueMicrotask(() => input.focus());

  return h("form", { class: "note-form", onsubmit: submit },
    h("span", {}, reason.label),
    input,
    h("button", { type: "submit", class: "exclude" }, h("kbd", {}, "Enter"), "Exclude"),
    actionButton("Cancel", "Esc", cancel),
    error,
  );
}

let toastTimer;

function showToast(message) {
  const toast = $("toast");

  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, TOAST_MILLISECONDS);
}

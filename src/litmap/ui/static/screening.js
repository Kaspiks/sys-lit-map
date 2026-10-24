import {
  $, DATABASE_NAMES, api, capitalize, externalLinks, h, marked, present, timeAgo, trailUrl,
} from "./shared.js";

const LABEL_NAMES = { include: "Include", maybe: "Maybe", exclude: "Exclude" };
const LONG_ABSTRACT_WORDS = 600;
const SWIPE_PIXELS = 80;
const TOAST_MILLISECONDS = 4000;

const view = {
  state: null,
  blockIndex: {},  // concept block -> highlight colour
  mode: "decide",  // decide | reason | note
  pendingReason: null,
  shownAt: Date.now(),
  busy: false,
};

start().catch((error) => $("record").replaceChildren(h("p", { class: "error" }, error.message)));

async function start() {
  render(await api("/api/state"));
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

  $("screener").textContent = state.screener;
  $("trail-link").href = state.record ? trailUrl(state.record.record_id) : "/trail";
  renderStats(state.progress);
  renderBanners(state.progress);
  renderRecord(state.record);
  renderLegend(state.legend);
  renderRecent(state.recent);
  renderReasonKey(state.reasons);
  renderActions();
}

// Decisions

function decide(label, reason = null, note = null) {
  const seconds = Math.round((Date.now() - view.shownAt) / 1000);

  return send("/api/decisions", { record_id: view.state.record.record_id, label, reason, note, seconds });
}

function undo() {
  return send("/api/undo", {});
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

// Rendering

function renderStats(progress) {
  $("stats").replaceChildren(
    stat("Screened", progress.screened, progress.pool),
    stat("Irrelevant in a row", progress.irrelevant_in_a_row, progress.stop_after, "Stopping rule, part 1"),
    stat("Key studies found", progress.key_studies_found, progress.key_studies_total,
      "Part 2. Which ones stays hidden"),
    progress.seed_set_done
      ? stat("Ranker", rankerValue(progress), null, retrainNote(progress))
      : stat("Seed set", progress.seed_set_screened, progress.seed_set_size,
        "Random order, trains the ranker"),
    stat("Seconds per record", progress.average_seconds ?? "–", null, "Average so far"),
  );
}

function rankerValue(progress) {
  return progress.ranker_iteration ? `Iteration ${progress.ranker_iteration}` : "Not trained yet";
}

function retrainNote(progress) {
  if (progress.retrain_in === 0) return "Retrains after your next decision";

  return `Retrains in ${progress.retrain_in} decisions`;
}

function stat(label, value, total, note) {
  const meter = total
    ? h("div", { class: "meter" }, h("span", { style: `width: ${Math.min(100, (value / total) * 100)}%` }))
    : null;

  return h("div", { class: "card stat" },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, String(value), total ? h("small", {}, ` / ${total}`) : null),
    meter,
    note ? h("div", { class: "stat-note" }, note) : null,
  );
}

function renderBanners(progress) {
  const missingKeyStudies = progress.key_studies_total - progress.key_studies_in_pool;

  $("banners").replaceChildren(...present([
    progress.stopping_rule_met && h("p", { class: "banner" },
      `Stopping rule met: ${progress.stop_after} irrelevant records in a row and every key study found. `
      + "You can stop here or keep screening."),
    missingKeyStudies > 0 && h("p", { class: "banner warning" },
      `${missingKeyStudies} key studies aren't in the screening pool, so the stopping rule can't be met. `
      + "Check them in the record trail."),
  ]));
}

function renderRecord(record) {
  if (!record) {
    $("record").replaceChildren(h("div", { class: "done" },
      h("h1", {}, "Every record is screened"),
      h("p", {}, "Your decisions are in logs/screening.jsonl."),
    ));

    return;
  }

  const databases = record.databases.map((name) => DATABASE_NAMES[name] ?? name).join(", ");
  const recordType = record.pub_types?.split("; ").slice(0, 2).join(", ");
  const meta = [record.journal, record.year, ...externalLinks(record)].filter(Boolean);

  $("record").replaceChildren(...present([
    h("div", { class: "tags" },
      h("span", { class: "tag" }, `Found in ${databases}`),
      recordType && h("span", { class: "tag" }, recordType),
    ),
    h("h1", { class: "record-title" }, marked(record.title, view.blockIndex)),
    h("p", { class: "meta" }, meta.map((item) => h("span", {}, item))),
    conceptChips(record.concept_hits),
    abstract(record),
    authors(record.authors),
    h("footer", { class: "record-footer" },
      h("span", { class: "mono" }, record.record_id),
      h("a", { href: trailUrl(record.record_id) }, "Record trail"),
    ),
  ]));
}

function conceptChips(hits) {
  return h("div", { class: "concepts", "aria-label": "Concept terms found" },
    Object.entries(hits).map(([block, count]) => (count
      ? h("span", { class: `concept term term-${view.blockIndex[block]}` },
        `${capitalize(block)} · ${count}`)
      : h("span", { class: "concept missing" }, `No ${block} terms`)
    )),
  );
}

function abstract(record) {
  if (!record.sections.length) {
    return h("p", { class: "notice" },
      "No abstract. Decide on the title, or choose maybe to check the full text.");
  }

  const body = h("div", { class: "abstract" }, record.sections.map((section) => [
    section.label && h("h3", {}, section.label),
    h("p", {}, marked(section.segments, view.blockIndex)),
  ]));

  if (record.word_count <= LONG_ABSTRACT_WORDS) return body;

  body.classList.add("folded");

  const showAll = h("button", {
    type: "button",
    class: "button",
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

function renderLegend(legend) {
  $("legend").replaceChildren(...legend.map((entry, index) => h("p", { class: "legend-row" },
    h("mark", { class: `term term-${index}` }, capitalize(entry.block)),
    " ",
    h("span", { class: "muted" }, entry.terms.join(", ")),
  )));
}

function renderRecent(recent) {
  const items = recent.map((decision) => h("li", {},
    h("span", { class: `label ${decision.label}` }, LABEL_NAMES[decision.label]),
    " ",
    h("span", { class: "muted" }, present([decision.reason, timeAgo(decision.timestamp)]).join(" · ")),
    h("a", { class: "recent-title", href: trailUrl(decision.record_id) },
      decision.title ?? decision.record_id),
  ));

  $("recent").replaceChildren(...(items.length ? items : [h("li", { class: "muted" }, "None yet.")]));
}

function renderReasonKey(reasons) {
  $("reason-key").replaceChildren(...reasons.map((reason) => h("li", {}, reason.label)));
}

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
      actionButton("Exclude", "E ←", askReason, "exclude"),
      actionButton("Maybe", "M ↑", () => decide("maybe"), "maybe"),
      actionButton("Include", "I →", () => decide("include"), "include"),
    );
  }
}

function actionButton(label, key, onclick, kind = "") {
  return h("button", { type: "button", class: kind, onclick }, label, h("kbd", {}, String(key)));
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
    h("button", { type: "submit", class: "exclude" }, "Exclude", h("kbd", {}, "Enter")),
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

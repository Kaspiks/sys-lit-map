import { renderLayout } from "./layout.js";
import {
  $, YEAR_FLAGS, api, badge, databaseName, errorText, externalLinks, formatTime, h, param, present,
  readable, sum, table, yearTag,
} from "./shared.js";

const TABS = {
  record: "This record",
  rules: "Excluded by rules",
  merges: "Deduplication merges",
  events: "Event log",
};

const LOGS = ["screening", "search", "dedup", "chase", "extraction", "report"];

const MERGE_RULES = {
  doi: "Same DOI",
  pmid: "Same PMID",
  title_key: "Same title",
  fuzzy_title: "Similar title",
};

const EVENT_NAMES = { decision: "Decision", undo: "Undo", rule_exclusion: "Excluded by rule" };

const FIELD_NAMES = { doi: "DOI", pmid: "PMID" };

const FILE_STATUS = {
  unchanged: "Unchanged",
  changed: "Changed since logged",
  missing: "Missing",
  "not logged": "Not fingerprinted",
};

// the fields that say what an event in any log is about, in the order they're shown
const SUMMARY_FIELDS = [
  "record_id", "label", "reason", "screener", "source", "rule", "value", "iteration", "round",
];

const query = param("q") ?? "";
const tab = param("tab") in TABS ? param("tab") : "record";

start();

async function start() {
  $("query").value = query;
  renderTabs();

  const session = await renderLayout("Record trail");

  renderMetrics();
  renderRecent();

  const show = { record: showRecord, rules: showRuleExclusions, merges: showMerges, events: showEvents }[tab];

  try {
    await show(session);
  } catch (error) {
    $("panel").replaceChildren(errorText(error));
  }
}

function tabUrl(name, extra = {}) {
  const params = new URLSearchParams({ ...(query && { q: query }), tab: name, ...extra });

  return `/trail?${params}`;
}

function renderTabs() {
  $("tabs").replaceChildren(...Object.entries(TABS).map(([name, label]) => (
    h("a", { href: tabUrl(name), "aria-current": name === tab ? "page" : null }, label)
  )));
}

// Numbers for the whole review

async function renderMetrics() {
  try {
    const [counts, adjudication] = await Promise.all([api("/api/prisma"), api("/api/agreement")]);
    const searched = sum(counts.databases);
    const identified = searched + counts.citation_chasing;
    const deduplicated = identified - counts.duplicates_removed;
    const ruledOut = sum(counts.excluded_by_rules);
    const databases = Object.keys(counts.databases).length;
    const chased = counts.citation_chasing ? ` + ${counts.citation_chasing} by citation chasing` : "";
    const removed = counts.duplicates_removed;
    const removedPercent = Math.round((removed / identified) * 100);

    $("metrics").replaceChildren(
      metric("Identified", identified, `${searched} from ${databases} databases${chased}`),
      metric("After deduplication", deduplicated, `${removed} duplicates removed (${removedPercent}%)`),
      metric("Screening pool", deduplicated - ruledOut, `${ruledOut} excluded by rules`),
      metric("Screened", counts.screened,
        `${counts.sought_for_retrieval} relevant · ${sum(counts.excluded_at_screening)} excluded`),
      agreementMetric(adjudication),
    );
  } catch (error) {
    $("metrics").replaceChildren(errorText(error));
  }
}

function agreementMetric({ agreement, sample_size: sampleSize }) {
  if (!agreement) return metric("Second screener", "–", `Not started; blind sample of ${sampleSize}`);

  const kappa = agreement.kappa ?? "–";

  return metric("Cohen's kappa", kappa,
    `${agreement.second}: ${agreement.screened_by_both} records screened by both`);
}

function metric(label, value, note) {
  return h("div", { class: "metric" },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, String(value)),
    h("div", { class: "stat-note" }, note),
  );
}

/** Shortcuts to the records you screened last. The page works without them, so errors are ignored. */
async function renderRecent() {
  const { feed } = await api("/api/state").catch(() => ({ feed: [] }));
  const seen = new Set();  // the feed is newest first, so the first event per record is its latest
  const latest = feed.filter((item) => !seen.has(item.record_id) && seen.add(item.record_id));

  if (!latest.length) return;

  $("recent").replaceChildren(...present(["Recently screened",
    latest.map((item) => h("a", {
      class: "chip",
      href: tabUrl("record", { q: item.record_id }),
      "aria-current": item.record_id === query ? "page" : null,
    }, h("span", { class: "mono" }, item.record_id), item.event === "undo" ? " undone" : ` ${item.label}`)),
  ]));
}

// This record

async function showRecord(session) {
  if (!query) {
    $("panel").replaceChildren(
      h("p", { class: "muted" }, "Enter a record id, DOI or PMID to see its trail."),
    );

    return;
  }

  const [trail, marks] = await Promise.all([
    api(`/api/trail?q=${encodeURIComponent(query)}`),
    api("/api/bookmarks"),
  ]);

  const bookmarked = marks.some((mark) => mark.record_id === trail.record_id);

  $("panel").replaceChildren(
    recordCard(trail, session, bookmarked),
    h("div", { class: "trail-grid" }, foundBySection(trail.search), rulesSection(trail.rules)),
    dedupSection(trail),
    screeningSection(trail.screening),
    filesSection(trail.files),
  );
}

function recordCard(trail, session, bookmarked) {
  const record = trail.record;
  const meta = [record.journal, record.year, ...externalLinks(record)].filter(Boolean);
  const unchanged = trail.files.filter((file) => file.status === "unchanged").length;
  const fingerprinted = trail.files.filter((file) => file.sha256).length;
  const json = new Blob([JSON.stringify(trail, null, 2)], { type: "application/json" });

  return h("section", { class: "card trail-section record-card" },
    h("div", { class: "tags" },
      h("span", { class: "tag dark mono" }, trail.record_id),
      badge(...recordStatus(trail, session.screener)),
      yearTag(record),
    ),
    h("h2", { class: "record-title" }, record.title ?? trail.record_id),
    h("p", { class: "meta" }, meta.map((item) => h("span", {}, item))),
    h("div", { class: "record-tools" },
      bookmarkButton(trail.record_id, bookmarked),
      h("a", {
        class: "button small",
        href: URL.createObjectURL(json),
        download: `${trail.record_id.replace(/[^\w-]+/g, "_")}_trail.json`,
      }, "Download JSON"),
      h("a", {
        class: `chip ${unchanged === fingerprinted ? "ok" : "warning"}`,
        href: "#files",
      }, `${unchanged} of ${fingerprinted} fingerprinted files unchanged`),
      h("span", { class: "muted" }, "Same trail in the terminal: ",
        h("code", {}, `python -m litmap.trail ${trail.record_id}`)),
    ),
  );
}

function bookmarkButton(recordId, bookmarked) {
  const button = h("button", { type: "button", class: "small" });

  const show = (on) => {
    button.textContent = on ? "★ Bookmarked" : "☆ Bookmark";
    button.setAttribute("aria-pressed", String(on));
  };

  button.addEventListener("click", async () => {
    const on = button.getAttribute("aria-pressed") !== "true";

    try {
      show((await api("/api/bookmarks", { record_id: recordId, bookmarked: on })).bookmarked);
    } catch (error) {
      button.title = error.message;
    }
  });

  show(bookmarked);

  return button;
}

/** [status, text] for the badge: a rule's exclusion, else your decision in force, else still to screen. */
function recordStatus(trail, screener) {
  const rule = trail.record.excluded_by;

  if (rule) return ["excluded_by_rule", `Excluded by rule: ${readable(rule)}`];

  const yours = decisionsInForce(trail.screening).find((event) => event.screener === screener);

  return yours ? [yours.label, `${readable(yours.label)} (${screener})`] : ["to_screen", "To screen"];
}

function decisionsInForce(events) {
  const undone = new Set(events.filter((event) => event.event === "undo").map((event) => event.undoes));

  return events.filter((event) => event.event === "decision" && !undone.has(event.decision_id));
}

function section(step, title, intro, ...body) {
  return h("section", { class: "card trail-section" },
    h("h2", {}, step && h("span", { class: "step" }, step), title),
    intro && h("p", { class: "muted" }, intro),
    ...body,
  );
}

function foundBySection(members) {
  const intro = "Each database that returned this record, and the search or chasing round that found it.";

  return section("1", "Found by", intro,
    members.map((member) => h("div", { class: "member" },
      h("h3", {},
        h("span", { class: "tag dark" }, databaseName(member.database)), " ",
        h("span", { class: "mono" }, member.source_id),
      ),
      h("p", { class: "muted" }, foundByText(member)),
      member.query && h("pre", { class: "query" }, formatQuery(member.query)),
    )),
  );
}

function foundByText(member) {
  if (member.method === "chase") {
    const directions = member.directions.join(" and ");

    return `Citation chasing round ${member.run}, ${formatTime(member.searched_at)}, ${directions}`;
  }

  if (!member.run) return "No search event logged for this database";

  return `Search run ${member.run}, ${formatTime(member.searched_at)}, ${member.hit_count} hits`;
}

function rulesSection(rules) {
  const fired = rules.rules.filter((rule) => rule.fired).length;
  const outcome = fired ? "excluded by the first that fired" : "every rule passed";
  const intro = `Rules run ${rules.rules_run}: ${outcome}.`;

  return section("3", "Rule pre-filter", intro,
    h("ul", { class: "rule-list" }, rules.rules.map((rule) => h("li", {},
      h("div", { class: "rule-head" },
        h("span", { class: "mono" }, `${rule.rule} v${rule.version}`),
        rule.fired ? badge("exclude", "Fired") : badge("match", "Passed"),
      ),
      rule.description && h("p", { class: "muted" }, rule.description),
    ))),
  );
}

function dedupSection(trail) {
  const { dedup } = trail;

  if (dedup.members.length === 1) {
    return section("2", "Deduplication", "Found in one database only, so nothing was merged.",
      yearCheck(dedup.year_check));
  }

  const rulesFor = (member) => dedup.merges
    .filter((merge) => merge.record_a === member || merge.record_b === member)
    .map((merge) => MERGE_RULES[merge.rule] ?? merge.rule);

  const members = dedup.members.map((member) => {
    const [database, sourceId] = member.split(/:(.*)/s);
    const kept = member === trail.record_id;

    return h("div", { class: `card member-card ${kept ? "kept" : ""}` },
      h("div", { class: "member-head" },
        kept ? badge("match", "Kept record") : badge("merged", "Merged in"),
        h("span", { class: "muted" }, [...new Set(rulesFor(member))].join(", ")),
      ),
      h("h3", {}, databaseName(database)),
      h("p", { class: "mono" }, sourceId),
    );
  });

  const sources = Object.entries(dedup.field_sources).map(([field, member]) => [
    FIELD_NAMES[field] ?? readable(field),
    h("td", { class: "mono" }, member ?? "–"),
  ]);

  const intro = `${dedup.members.length} records merged into one (run ${dedup.dedup_run}). `
    + "Each field is taken from the first record, in database priority order, that has it.";

  return section("2", "Deduplication", intro,
    h("div", { class: "member-grid" }, members),
    table(["Field", "Taken from"], sources),
    yearCheck(dedup.year_check),
  );
}

/** What years.py did with a doubtful year, and the related works the year came from. */
function yearCheck(check) {
  if (!check) return null;

  const outcome = check.source === "related_works"
    ? `Year corrected from ${check.reported_year} to ${check.year}, the year of its latest related works.`
    : `Year kept as reported (${check.reported_year}): no related work dates it earlier.`;

  const cleared = check.cleared_first_published
    && ` Its publication date (${check.cleared_first_published}) came from the same registration: cleared.`;

  return h("div", { class: "year-check" },
    h("h3", {}, "Year check"),
    h("p", { class: "muted" }, YEAR_FLAGS[check.flag], "."),
    h("p", {}, outcome, cleared),
    check.related_works.length > 0 && h("ul", {}, check.related_works.map((work) => h("li", {},
      h("a", { class: "mono", href: tabUrl("record", { q: work.record_id }) }, work.record_id),
      ` (${work.year}): shares ${work.shared_authors.join(", ")} and ${work.shared_title_words} title words`,
    ))),
  );
}

function screeningSection(events) {
  const lineOf = Object.fromEntries(events.map((event) => [event.decision_id, event.line]));

  const undoneBy = Object.fromEntries(
    events.filter((event) => event.event === "undo").map((event) => [event.undoes, event.line]),
  );

  const items = events.map((event) => {
    const undone = undoneBy[event.decision_id];
    const state = event.event === "decision" ? (undone ? "undone" : "in-force") : event.event;

    return h("li", { class: `event ${state}` },
      h("span", { class: "event-line" }, `#${event.line}`),
      h("div", {},
        h("div", { class: "event-head" },
          h("strong", {}, EVENT_NAMES[event.event] ?? readable(event.event)),
          event.label && badge(event.label),
          event.screener && h("span", { class: "muted" }, event.screener),
          h("span", { class: "muted" }, formatTime(event.timestamp)),
        ),
        h("p", {}, eventDetails(event, undone, lineOf)),
      ),
    );
  });

  const intro = items.length
    ? "Numbered by their line in logs/screening.jsonl. The log is append-only: an undo is a new line."
    : "Not screened yet.";

  return section("4", "Screening events", intro, h("ol", { class: "events" }, items));
}

function eventDetails(event, undoneByLine, lineOf) {
  if (event.event === "undo") return `Takes back decision #${lineOf[event.undoes] ?? "?"}`;
  if (event.event === "rule_exclusion") return `${readable(event.rule)} v${event.rule_version}`;

  return [
    event.reason && readable(event.reason),
    event.note && `“${event.note}”`,
    event.queue && `from the ${readable(event.queue).toLowerCase()} queue`,
    event.seconds_on_record != null && `${event.seconds_on_record} s on the record`,
    undoneByLine ? `undone by #${undoneByLine}` : "in force",
  ].filter(Boolean).join(" · ");
}

function filesSection(files) {
  const rows = files.map((file) => [
    h("td", { class: "mono" }, file.file),
    h("td", { class: "mono" }, file.sha256 ? `${file.sha256.slice(0, 12)}…` : "–"),
    h("td", { class: `status-${file.status.replace(" ", "-")}` }, FILE_STATUS[file.status]),
  ]);

  const intro = "Each file the logs fingerprinted, checked against its SHA-256 now.";
  const card = section(null, "File fingerprints", intro, table(["File", "Logged SHA-256", "Now"], rows));
  card.id = "files";

  return card;
}

function formatQuery(sentQuery) {
  if (typeof sentQuery === "string") return sentQuery;

  if (Array.isArray(sentQuery)) {
    const [term, params] = sentQuery;
    const extra = Object.entries(params ?? {}).map(([name, value]) => `${name}=${value}`);

    return [term, ...extra].join("\n");
  }

  return Object.entries(sentQuery).map(([name, value]) => `${name}=${value}`).join("\n");
}

// The other tabs

async function showRuleExclusions() {
  const excluded = (await api("/api/registry")).filter((row) => row.status === "excluded_by_rule");
  const byRule = Object.groupBy(excluded, (row) => row.detail);
  const counts = Object.entries(byRule).map(([rule, rows]) => `${readable(rule)} ${rows.length}`).join(", ");

  $("panel").replaceChildren(section(null, `${excluded.length} records excluded by rules`, counts,
    table(["Record", "Title", "Year", "Rule"], excluded.map((row) => [
      h("td", { class: "mono" }, h("a", { href: tabUrl("record", { q: row.record_id }) }, row.record_id)),
      row.title ?? "–",
      row.year ?? "–",
      readable(row.detail),
    ])),
  ));
}

async function showMerges() {
  const merges = await api("/api/merges");
  const counts = Object.entries(Object.groupBy(merges, (merge) => merge.rule))
    .map(([rule, rows]) => `${MERGE_RULES[rule] ?? rule} ${rows.length}`).join(", ");
  const link = (member) => h("td", { class: "mono" },
    h("a", { href: tabUrl("record", { q: member }) }, member));

  $("panel").replaceChildren(section(null, `${merges.length} joins in the latest deduplication run`, counts,
    table(["Rule", "Matched on", "Record", "Joined with"], merges.map((merge) => [
      MERGE_RULES[merge.rule] ?? merge.rule,
      h("td", { class: "mono" }, merge.value),
      link(merge.record_a),
      link(merge.record_b),
    ])),
  ));
}

async function showEvents() {
  const log = LOGS.includes(param("log")) ? param("log") : "screening";
  const data = await api(`/api/events?log=${log}`);

  $("panel").replaceChildren(
    h("nav", { class: "tabs small", "aria-label": "Logs" }, LOGS.map((name) => h("a", {
      href: tabUrl("events", { log: name }),
      "aria-current": name === log ? "page" : null,
    }, `${name}.jsonl`))),
    section(null, `logs/${log}.jsonl`,
      data.total > data.events.length
        ? `${data.total} events; the latest ${data.events.length}, newest first.`
        : `${data.total} events, newest first.`,
      h("ol", { class: "events" }, data.events.map(({ line, event }) => h("li", { class: "event" },
        h("span", { class: "event-line" }, `#${line}`),
        h("details", {},
          h("summary", {},
            h("strong", {}, readable(event.event ?? "search")), " ",
            h("span", { class: "muted" }, eventSummary(event)),
          ),
          h("pre", { class: "query" }, JSON.stringify(event, null, 2)),
        ),
      ))),
    ),
  );
}

function eventSummary(event) {
  const fields = SUMMARY_FIELDS.filter((field) => event[field] != null).map((field) => event[field]);
  const time = event.timestamp ?? event.searched_at;

  return [...fields, time && formatTime(time)].filter(Boolean).join(" · ");
}

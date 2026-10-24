import { $, DATABASE_NAMES, api, externalLinks, h } from "./shared.js";

const MERGE_RULES = {
  doi: "Same DOI",
  pmid: "Same PMID",
  title_key: "Same title",
  fuzzy_title: "Similar title",
};

const EVENT_NAMES = { decision: "Decision", undo: "Undo", rule_exclusion: "Excluded by rule" };

const FILE_STATUS = {
  unchanged: "Unchanged",
  changed: "Changed since logged",
  missing: "Missing",
  "not logged": "Not fingerprinted",
};

const query = new URLSearchParams(window.location.search).get("q") ?? "";

$("query").value = query;
show(query);

async function show(text) {
  if (!text) {
    $("trail").replaceChildren(
      h("p", { class: "muted" }, "Enter a record id, DOI or PMID to see its trail."),
    );

    return;
  }

  try {
    const trail = await api(`/api/trail?q=${encodeURIComponent(text)}`);
    $("trail").replaceChildren(
      recordHeader(trail),
      searchSection(trail.search),
      dedupSection(trail.dedup),
      rulesSection(trail.rules),
      screeningSection(trail.screening),
      filesSection(trail.files),
    );
  } catch (error) {
    $("trail").replaceChildren(h("p", { class: "error" }, error.message));
  }
}

function section(step, title, intro, ...body) {
  return h("section", { class: "card trail-section" },
    h("h2", {}, step && h("span", { class: "step" }, step), title),
    intro && h("p", { class: "muted" }, intro),
    ...body,
  );
}

function recordHeader(trail) {
  const record = trail.record;
  const meta = [record.journal, record.year, ...externalLinks(record)].filter(Boolean);
  const status = record.excluded_by ? `Excluded by the rule ${record.excluded_by}` : "In the screening pool";

  return section(null, record.title ?? trail.record_id, null,
    h("p", { class: "meta" }, meta.map((item) => h("span", {}, item))),
    h("p", { class: "meta" }, h("span", { class: "mono" }, trail.record_id), h("span", {}, status)),
  );
}

function searchSection(members) {
  const intro = "Each database that returned this record, and the search or chasing round that found it.";

  return section("1", "Found by", intro,
    members.map((member) => h("div", { class: "member" },
      h("h3", {},
        `${DATABASE_NAMES[member.database] ?? member.database} · `,
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

function dedupSection(dedup) {
  if (dedup.members.length === 1) {
    return section("2", "Deduplication", "Found in one database only, so nothing was merged.");
  }

  const merges = dedup.merges.map((merge) => h("li", {},
    h("strong", {}, MERGE_RULES[merge.rule] ?? merge.rule),
    ` (${merge.value}): `,
    h("span", { class: "mono" }, merge.record_a), " and ", h("span", { class: "mono" }, merge.record_b),
  ));
  const sources = Object.entries(dedup.field_sources).map(([field, member]) => [
    h("td", {}, field.replace("_", " ")),
    h("td", { class: "mono" }, member ?? "–"),
  ]);
  const intro = `${dedup.members.length} records merged into one (run ${dedup.dedup_run}).`;

  return section("2", "Deduplication", intro,
    h("ul", { class: "timeline" }, merges),
    table(["Field", "Taken from"], sources),
  );
}

function rulesSection(rules) {
  const rows = rules.rules.map((rule) => [
    h("td", {}, rule.rule),
    h("td", {}, `v${rule.version}`),
    h("td", { class: rule.fired ? "status-changed" : "status-unchanged" }, rule.fired ? "Fired" : "Passed"),
  ]);

  return section("3", "Rule pre-filter", `Rules run ${rules.rules_run}.`,
    table(["Rule", "Version", "Result"], rows),
  );
}

function screeningSection(events) {
  const items = events.map((event) => h("li", {},
    h("span", { class: "muted" }, `${formatTime(event.timestamp)} · `),
    h("strong", {}, EVENT_NAMES[event.event] ?? event.event),
    event.label && ` · ${event.label}`,
    event.reason && ` (${event.reason})`,
    event.rule && ` · ${event.rule} v${event.rule_version}`,
    event.note && ` · “${event.note}”`,
    event.screener && h("span", { class: "muted" }, ` · ${event.screener}`),
    event.seconds_on_record != null && h("span", { class: "muted" }, ` · ${event.seconds_on_record} s`),
  ));

  return section("4", "Screening", items.length ? null : "Not screened yet.",
    h("ul", { class: "timeline" }, items),
  );
}

function filesSection(files) {
  const rows = files.map((file) => [
    h("td", { class: "mono" }, file.file),
    h("td", { class: "mono" }, file.sha256 ? `${file.sha256.slice(0, 12)}…` : "–"),
    h("td", { class: `status-${file.status.replace(" ", "-")}` }, FILE_STATUS[file.status]),
  ]);

  const intro = "Each file the logs fingerprinted, checked against its SHA-256 now.";

  return section(null, "File fingerprints", intro,
    table(["File", "Logged SHA-256", "Now"], rows),
  );
}

function table(headers, rows) {
  return h("table", {},
    h("thead", {}, h("tr", {}, headers.map((header) => h("th", {}, header)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells))),
  );
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

function formatTime(timestamp) {
  return timestamp ? new Date(timestamp).toLocaleString() : "unknown time";
}

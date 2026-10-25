export const DATABASE_NAMES = { pubmed: "PubMed", europepmc: "Europe PMC", openalex: "OpenAlex" };

export const STATUS_NAMES = {
  include: "Include",
  maybe: "Maybe",
  exclude: "Exclude",
  to_screen: "To screen",
  excluded_by_rule: "Excluded by rule",
};

export const YEAR_FLAGS = {
  current_year: "A dissertation or repository item dated the year of the search, often a re-registration",
  later_than_related_works: "Dated later than its related works, so corrected to their year",
};

export const $ = (id) => document.getElementById(id);

export const param = (name) => new URLSearchParams(window.location.search).get(name);

export const capitalize = (word) => word.charAt(0).toUpperCase() + word.slice(1);

export const readable = (code) => capitalize(code.replaceAll("_", " "));

export const databaseName = (name) => DATABASE_NAMES[name] ?? name;

export const trailUrl = (recordId) => `/trail?q=${encodeURIComponent(recordId)}`;

/** The sum of an object's values: sum({ a: 2, b: 3 }) is 5. */
export const sum = (counts) => Object.values(counts).reduce((total, count) => total + count, 0);

/** Create an element: h("a", { href, class: "x", onclick }, "text", childElement). */
export function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);

  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;

    if (name.startsWith("on")) {
      element.addEventListener(name.slice(2), value);
    } else if (name === "class") {
      element.className = value;
    } else {
      element.setAttribute(name, value === true ? "" : value);
    }
  }

  element.append(...present(children));

  return element;
}

/** Children without the null, undefined and false that conditional rendering leaves behind. */
export function present(children) {
  return children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false);
}

/** GET, or POST when a body is given. Errors carry the server's message (and state, if sent). */
export async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  let response, data;
  while (true) {
    response = await fetch(path, options);
    data = await response.json();
    // Read-only panels recover when the new run is ready. Never replay a mutation.
    if (body !== undefined || response.ok || data.code !== "search_rebuilding") break;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.state = data.state;
    error.code = data.code;
    throw error;
  }

  return data;
}

/** Text pieces from the server ([text, block or null]) as text and <mark> elements. */
export function marked(segments, blockIndex) {
  return segments.map(([text, block]) => (
    block === null ? text : h("mark", { class: `term term-${blockIndex[block] ?? 3}` }, text)
  ));
}

export function externalLinks(record) {
  return [
    record.doi && newTabLink(`https://doi.org/${record.doi}`, `DOI ${record.doi}`),
    record.pmid && newTabLink(`https://pubmed.ncbi.nlm.nih.gov/${record.pmid}/`, `PMID ${record.pmid}`),
  ];
}

function newTabLink(href, text) {
  return h("a", { href, target: "_blank", rel: "noopener" }, text);
}

export function badge(status, text = STATUS_NAMES[status] ?? readable(status)) {
  return h("span", { class: `badge ${status}` }, text);
}

/** A tag for a year years.py flagged: corrected, or kept but unconfirmed. Null for other records. */
export function yearTag(record) {
  if (!record.year_flag) return null;

  const corrected = record.year !== record.year_reported;
  const text = corrected ? `Year corrected from ${record.year_reported}` : `Year ${record.year} unconfirmed`;

  return h("span", { class: "tag warning", title: YEAR_FLAGS[record.year_flag] }, text);
}

/** A table; each cell is a <td> or the content for one. */
export function table(headers, rows) {
  const cell = (content) => (content instanceof HTMLTableCellElement ? content : h("td", {}, content));

  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((header) => h("th", {}, header)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map(cell)))),
  ));
}

/** A chart from the report module, drawn from the logs on every load. Hidden while it has no data. */
export function figure(name, caption) {
  const card = h("figure", { class: "card figure" },
    h("img", { src: `/api/figures/${name}.svg`, alt: caption, onerror: () => card.remove() }),
  );

  return card;
}

export function errorText(error) {
  return h("p", { class: "error" }, error.message);
}

export function formatTime(timestamp) {
  return timestamp ? new Date(timestamp).toLocaleString() : "unknown time";
}

export function timeAgo(timestamp) {
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(timestamp)) / 1000));

  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;

  return new Date(timestamp).toLocaleDateString();
}

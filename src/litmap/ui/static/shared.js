export const DATABASE_NAMES = { pubmed: "PubMed", europepmc: "Europe PMC", openalex: "OpenAlex" };

export const $ = (id) => document.getElementById(id);

export const capitalize = (word) => word.charAt(0).toUpperCase() + word.slice(1);

export const trailUrl = (recordId) => `/trail?q=${encodeURIComponent(recordId)}`;

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
  const response = await fetch(path, options);
  const data = await response.json();

  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.state = data.state;
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

export function timeAgo(timestamp) {
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(timestamp)) / 1000));

  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;

  return new Date(timestamp).toLocaleString();
}

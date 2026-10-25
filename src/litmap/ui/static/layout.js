import { $, api, h, present } from "./shared.js";

const NAV = [
  ["/", "Screening"],
  ["/trail", "Record trail"],
  ["/registry", "Citation registry"],
  ["/prisma", "PRISMA flow"],
  ["/ranker", "Ranker"],
  ["/adjudication", "Second screener"],
  ["/config", "Config"],
];

/**
 * The sidebar and page header every page shares. `screener` is set on the screening page when a
 * second screener is screening; `chips` are extra header chips. Returns the session.
 */
export async function renderLayout(title, { screener = null, chips = [] } = {}) {
  const session = await api("/api/session");

  $("sidebar").replaceChildren(...present([
    h("a", { class: "brand", href: "/" },
      h("strong", {}, "litmap"),
      h("small", {}, `Systematic literature mapping · v${session.version}`),
    ),
    session.question && h("section", { class: "question" },
      h("h2", {}, "Review question"),
      h("p", { title: session.question }, session.question),
    ),
    h("ul", { class: "nav" }, NAV.map(([href, label]) => h("li", {},
      h("a", { href, "aria-current": href === window.location.pathname ? "page" : null }, label),
    ))),
    screenerCard(session, screener),
  ]));

  $("header").replaceChildren(
    h("h1", {}, title),
    h("div", { class: "chips" }, syncChip(session.search_sync), chips),
  );

  return session;
}

function screenerCard(session, screener) {
  const blind = screener && screener !== session.screener;

  return h("section", { class: "screener-card" },
    h("h2", {}, "Screener"),
    h("p", {},
      h("span", { class: "initials" }, screener ?? session.screener),
      blind ? "Second screener, blind" : "Primary screener",
    ),
  );
}

function syncChip(sync) {
  if (!sync) return h("a", { class: "chip warning", href: "/config" }, "No search run yet");

  if (sync.changed.length) {
    return h("a", { class: "chip warning", href: "/config" },
      `search.yaml changed since run ${sync.run}: ${sync.changed.join(", ")}`);
  }

  return h("a", { class: "chip ok", href: "/config", title: `Run ${sync.run}` },
    "search.yaml matches the search run");
}


import { renderLayout } from "./layout.js";
import { $, api, errorText, h, param, present } from "./shared.js";

const view = {
  busy: false,
  config: null,
  timer: null,
};

start();

async function start() {
  try {
    await renderLayout("Config");
    render(await api("/api/config"));
    if (view.config.rebuild.status === "running") schedulePoll();
  } catch (error) {
    $("file").replaceChildren(errorText(error));
  }

  document.addEventListener("keydown", onKey);
}

function render(config) {
  view.config = config;
  const { files, search_sync: sync, rebuild } = config;
  const names = files.map((file) => file.file.split("/").pop());
  const shown = Math.max(0, names.indexOf(param("file")));
  const file = files[shown];

  $("tabs").replaceChildren(
    ...names.map((name, index) =>
      h(
        "a",
        {
          href: `?file=${name}`,
          "aria-current": index === shown ? "page" : null,
        },
        name
      )
    )
  );

  $("file").replaceChildren(
    ...present([
      h(
        "div",
        { class: "file-head" },

        h("h2", { class: "mono" }, file.file),

        h(
          "span",
          { class: "muted" },
          "SHA-256 ",
          h("code", {}, file.sha256)
        ),

        actionButton(
          rebuild.status === "running" ? "Starting fresh…" : "Start new search & reset screening",
          "R",
          restore,
          "reload",
          "archive the previous run and start from scratch"
        )
      ),

      h("p", {
        id: "rebuild-status", role: "status",
        class: rebuild.status === "failed" ? "banner warning" : "banner",
      }, rebuildMessage(rebuild)),

      names[shown] === "search.yaml" && syncNote(sync),

      h(
        "p",
        { class: "panel-note" },
        "Read-only here: edit the file in config/, so every change goes through " +
          "the file, its fingerprint and the logs."
      ),

      h("pre", { class: "config-text" }, file.text),
    ])
  );
}

function syncNote(sync) {
  if (!sync) {
    return h(
      "p",
      { class: "banner warning" },
      "No search run yet. Use Start new search & reset screening to begin."
    );
  }

  if (sync.changed.length) {
    return h(
      "p",
      { class: "banner warning" },
      `Changed since search run ${sync.run}: ${sync.changed.join(", ")}. The records on disk come from the ` +
        "saved search configuration. Start a new search to apply these changes from scratch."
    );
  }

  return h(
    "p",
    { class: "banner" },
    `The query sections match the copy saved with search run ${sync.run}.`
  );
}

function rebuildMessage(job) {
  if (job.status === "running") {
    return `${job.stage}… This can take a while. You can leave this page and return to check progress.`;
  }
  if (job.status === "complete") {
    return `Search results rebuilt: ${job.found} search records, ${job.records} after deduplication, ` +
      `${job.to_screen} eligible for screening. Screening restarted at 0 with a fresh seed order. ` +
      `Previous run archived in ${job.archive}.`;
  }
  if (job.status === "failed") {
    return `Rebuild failed during ${job.stage}: ${job.error}. ` +
      (job.restored ? "The previous run was restored. You can retry with the button." :
        `Recovery needs attention; the previous files are in ${job.archive}.`);
  }
  return "Starts from scratch: searches all configured databases, deduplicates, and applies current filters. " +
    "Previous results, decisions, rankings, citation chasing, and extraction are archived; bookmarks are kept. " +
    "Screening restarts at 0.";
}

async function restore() {
  if (view.busy || view.config.rebuild.status === "running") return;

  view.busy = true;
  render(view.config);

  try {
    const job = await api("/api/config/rebuild", {});
    render({ ...view.config, rebuild: job });
    schedulePoll();
  } catch (error) {
    $("rebuild-status").textContent = error.message;
    schedulePoll(); // The server may have accepted the job even if the response was lost.
  } finally {
    view.busy = false;
    document.querySelector("button.reload").disabled = view.config.rebuild.status === "running";
  }
}

function schedulePoll() {
  clearTimeout(view.timer);
  view.timer = setTimeout(poll, 1500);
}

async function poll() {
  try {
    const job = await api("/api/config/rebuild");
    render({ ...view.config, rebuild: job });
    if (job.status === "running") {
      schedulePoll();
    } else {
      const [config] = await Promise.all([api("/api/config"), renderLayout("Config")]);
      render(config);
    }
  } catch (error) {
    $("rebuild-status").textContent = `Unable to refresh rebuild status: ${error.message}. Retrying…`;
    schedulePoll();
  }
}

function actionButton(label, key, onclick, kind = "", hint = null) {
  return h(
    "button",
    {
      type: "button",
      class: kind,
      onclick,
      title: hint || label,
      disabled: view.busy || view.config.rebuild.status === "running",
    },
    h("kbd", {}, String(key)),
    h(
      "span",
      { class: "action-text" },
      label,
      hint && h("small", {}, hint)
    )
  );
}

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
  if (key === "r") return restore;
  return null;
}

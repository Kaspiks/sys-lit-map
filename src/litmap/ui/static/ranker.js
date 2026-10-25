import { renderLayout } from "./layout.js";
import { $, api, badge, errorText, figure, formatTime, h, readable, table } from "./shared.js";

start();

async function start() {
  renderLayout("Ranker");
  $("curve").replaceChildren(figure("screening_progress", "Relevant records found while screening"));

  try {
    const ranker = await api("/api/ranker");
    renderStats(ranker);
    renderIterations(ranker.iterations);
  } catch (error) {
    $("iterations").replaceChildren(errorText(error));
  }
}

function renderStats({ progress, settings }) {
  const seedSet = `${progress.seed_set_screened} / ${progress.seed_set_size}`;
  const retrain = progress.retrain_in === null ? "After the seed set" : `In ${progress.retrain_in} decisions`;

  $("stats").replaceChildren(
    stat("Model", readable(settings.model ?? "random_forest"), `Batch size ${settings.batch_size ?? 25}`),
    stat("Features", (settings.features ?? []).join(", ") || "all", "screening.yaml ranker.features"),
    stat("Seed set", seedSet, "Screened in random order first"),
    stat("Latest iteration", progress.ranker_iteration ?? "–", `Retrains: ${retrain.toLowerCase()}`),
  );
}

function stat(label, value, note) {
  return h("div", { class: "card stat" },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, String(value)),
    h("div", { class: "stat-note" }, note),
  );
}

function renderIterations(iterations) {
  if (!iterations.length) {
    $("iterations").replaceChildren(h("p", { class: "muted" }, "No iterations yet: the ranker first trains "
      + "when the seed set is screened."));

    return;
  }

  $("iterations").replaceChildren(table(
    ["Iteration", "Trained", "Model", "Trained on", "Features", "Replay"],
    iterations.slice().reverse().map((iteration) => [
      String(iteration.iteration),
      formatTime(iteration.timestamp),
      readable(iteration.model),
      `${iteration.trained_on} decisions, ${iteration.relevant} relevant`,
      iteration.features.join(", "),
      h("td", {}, replayCell(iteration)),
    ]),
  ));
}

function replayCell(iteration) {
  if (!iteration.replayable) return h("span", { class: "muted" }, "Logged before replay was possible");

  const cell = h("span", {});
  const onclick = () => replay(iteration, cell);
  cell.append(h("button", { type: "button", class: "small", onclick }, "Replay"));

  return cell;
}

async function replay(iteration, cell) {
  cell.replaceChildren(h("span", { class: "muted" }, "Retraining…"));

  try {
    const result = await api(`/api/ranker/replay/${iteration.iteration}`, {});
    cell.replaceChildren(result.reproduced
      ? badge("match", "Reproduced exactly")
      : badge("exclude", "Different ranking"));
  } catch (error) {
    cell.replaceChildren(h("span", { class: "error" }, error.message));
  }
}

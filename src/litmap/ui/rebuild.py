"""One background search rebuild for the local UI, with progress shared across browser tabs."""

import shutil
import uuid
from threading import Lock, Thread

from litmap import bookmarks, common, dedup, rules, search

KEPT_LOGS = (f"{bookmarks.LOG}.jsonl",)  # your own notes, not results of the run: they stay


class RunArchive:
  """Move the active run aside, keeping its original paths for recovery."""

  def __init__(self):
    self.path = common.ROOT / "data" / "archive" / f"{common.run_id()}-{uuid.uuid4().hex[:8]}"

    self.paths = (
      common.RAW_DIR, common.INTERIM_DIR, common.PROCESSED_DIR,
      common.MODELS_DIR, common.OUTPUTS_DIR, common.LOG_DIR,
    )

    self.saved = []
    self.missing = []

  def capture(self):
    self.path.mkdir(parents=True)

    if common.CONFIG_DIR.exists():
      shutil.copytree(common.CONFIG_DIR, self.path / "config")

    for path in self.paths:
      if path.exists():
        saved = self.path / path.relative_to(common.ROOT)
        saved.parent.mkdir(parents=True, exist_ok=True)
        path.rename(saved)
        self.saved.append(path)
      else:
        self.missing.append(path)

      path.mkdir(parents=True, exist_ok=True)

      if path in self.saved and (saved / ".gitkeep").exists():
        shutil.copy2(saved / ".gitkeep", path / ".gitkeep")

    for name in KEPT_LOGS:
      saved = self.path / common.LOG_DIR.relative_to(common.ROOT) / name

      if saved.exists():
        shutil.copy2(saved, common.LOG_DIR / name)

  def restore(self):
    """Keep failed outputs alongside the archive and put the previous run back."""

    for path in reversed(self.saved + self.missing):
      relative = path.relative_to(common.ROOT)

      if path.exists():
        failed = self.path / "failed" / relative
        failed.parent.mkdir(parents=True, exist_ok=True)
        path.rename(failed)

      saved = self.path / relative

      if path in self.saved:
        saved.rename(path)


class SearchRebuild:
  def __init__(self):
    self._lock = Lock()
    self.access_lock = Lock()
    self._state = {"status": "idle"}
    self.thread = None

  def state(self):
    with self._lock:
      return dict(self._state)

  def start(self, cfg):
    with self._lock:
      if self._state["status"] == "running":
        return dict(self._state)

      self._state = {"status": "running", "stage": "Archiving the previous run"}
      self.thread = Thread(target=self._run, args=(cfg,), daemon=True)

      try:
        self.thread.start()
      except Exception:
        self._state = {
          "status": "failed", "stage": "Starting the rebuild", "restored": True,
          "error": "Could not start the rebuild; existing files are unchanged.",
        }

        raise

      return dict(self._state)

  def _update(self, **state):
    with self._lock:
      self._state.update(state)

  def _run(self, cfg):
    # Wait for any in-flight screening action, then keep the active files consistent.
    with self.access_lock:
      archive = RunArchive()

      try:
        self._update(archive=common.relative(archive.path))
        archive.capture()
        self._update(stage="Searching databases")
        found = search.run_search(cfg=cfg)
        self._update(stage="Deduplicating records")
        merged = dedup.deduplicate()
        self._update(stage="Applying filters")
        filtered = rules.apply_rules(cfg=cfg)

        self._update(
          status="complete", stage="Finished", found=len(found), records=len(merged),
          to_screen=int(filtered["excluded_by"].isna().sum()),
        )
      except Exception as error:
        try:
          archive.restore()
        except Exception as restore_error:
          self._update(status="failed", error=f"{error}. Recovery failed: {restore_error}", restored=False)
        else:
          self._update(status="failed", error=str(error), restored=True)

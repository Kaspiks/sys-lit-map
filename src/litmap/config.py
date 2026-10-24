"""Reading the config files: config/search.yaml and config/screening.yaml."""
from dataclasses import dataclass, field

import yaml

from litmap import common

CONFIG_PATH = common.CONFIG_DIR / "search.yaml"
SCREENING_CONFIG_PATH = common.CONFIG_DIR / "screening.yaml"


@dataclass(frozen=True)
class ScopeLimits:
  """The `filters` section of search.yaml: which languages and publication years to keep."""

  languages: tuple = ()
  year_from: int | None = None
  year_to: int | None = None


@dataclass(frozen=True)
class ExclusionReason:
  code: str
  label: str
  note_required: bool = False


@dataclass(frozen=True)
class ScreeningSettings:
  """config/screening.yaml."""

  screener: str
  seed: int
  seed_set_size: int
  stop_after_irrelevant: int
  exclusion_reasons: tuple
  ranker: dict = field(default_factory=dict)  # free-form while you experiment, see rank.py

  def reason(self, code):
    """The exclusion reason with this code, or None."""

    return next((reason for reason in self.exclusion_reasons if reason.code == code), None)


def load_config(path=CONFIG_PATH):
  return _read_yaml(path)


def scope_limits(cfg):
  limits = cfg.get("filters") or {}

  return ScopeLimits(
    languages=tuple(limits.get("languages") or ()),
    year_from=limits.get("year_from"),
    year_to=limits.get("year_to"),
  )


def load_screening_settings(path=SCREENING_CONFIG_PATH):
  settings = _read_yaml(path)

  return ScreeningSettings(
    screener=settings["screener"],
    seed=settings["seed"],
    seed_set_size=settings["seed_set_size"],
    stop_after_irrelevant=settings["stopping_rule"]["irrelevant_in_a_row"],
    exclusion_reasons=tuple(ExclusionReason(**reason) for reason in settings["exclusion_reasons"]),
    ranker=settings.get("ranker") or {},
  )


def _read_yaml(path):
  with open(path) as config_file:
    return yaml.safe_load(config_file)

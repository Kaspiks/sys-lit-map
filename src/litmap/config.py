"""Reading the config files: search.yaml, screening.yaml and extraction.yaml in config/."""
from dataclasses import dataclass, field

import yaml

from litmap import common

CONFIG_PATH = common.CONFIG_DIR / "search.yaml"
SCREENING_CONFIG_PATH = common.CONFIG_DIR / "screening.yaml"
EXTRACTION_CONFIG_PATH = common.CONFIG_DIR / "extraction.yaml"
FIELD_TYPES = ("text", "integer", "number", "choice", "choices")


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
  when_missing: str | None = None  # a search.yaml concept block: suggested when none of its terms occur


@dataclass(frozen=True)
class ScreeningSettings:
  """config/screening.yaml."""

  screener: str
  seed: int
  seed_set_size: int
  stop_after_irrelevant: int
  exclusion_reasons: tuple
  ranker: dict = field(default_factory=dict)  # free-form while you experiment, see rank.py
  second_sample_size: int = 150  # records the blind second screener screens

  def reason(self, code):
    """The exclusion reason with this code, or None."""

    return next((reason for reason in self.exclusion_reasons if reason.code == code), None)


@dataclass(frozen=True)
class FormField:
  """One column of the data-charting form (config/extraction.yaml)."""

  name: str
  type: str
  label: str = ""
  options: tuple = ()
  required_for: tuple = ()  # full-text decisions for which it must be filled


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
    second_sample_size=(settings.get("second_screener") or {}).get("sample_size", 150),
  )


def load_extraction_form(path=EXTRACTION_CONFIG_PATH):
  """The fields of the data-charting form, checked so a typo in the form fails early."""

  fields = tuple(
    FormField(**{
      **field,
      "options": tuple(field.get("options", ())),
      "required_for": tuple(field.get("required_for", ())),
    })
    for field in _read_yaml(path)["fields"]
  )

  for field in fields:
    if field.type not in FIELD_TYPES:
      raise ValueError(f"Form field {field.name}: unknown type {field.type!r}; use one of {FIELD_TYPES}")

    if field.type in ("choice", "choices") and not field.options:
      raise ValueError(f"Form field {field.name}: a {field.type} field needs options")

  return fields


def _read_yaml(path):
  with open(path) as config_file:
    return yaml.safe_load(config_file)

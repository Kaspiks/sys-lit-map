import pytest

from litmap.config import ExclusionReason, ScreeningSettings


@pytest.fixture
def settings():
  """Screening settings for tests: a small seed set and a short stopping rule."""

  return ScreeningSettings(
    screener="KM",
    seed=1,
    seed_set_size=3,
    stop_after_irrelevant=2,
    exclusion_reasons=(
      ExclusionReason("not_running", "Not running"),
      ExclusionReason("other", "Other", note_required=True),
    ),
  )

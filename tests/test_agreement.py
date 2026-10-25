from litmap import agreement, screening


def _decision(decision_id, record_id, label, screener):
  return {
    "event": "decision",
    "stage": screening.STAGE,
    "decision_id": decision_id,
    "record_id": record_id,
    "label": label,
    "screener": screener,
  }


def test_kappa_is_undefined_for_too_few_records_or_a_single_category():
  assert agreement.kappa(["relevant"], ["relevant"]) is None
  assert agreement.kappa(["relevant"] * 3, ["relevant"] * 3) is None
  assert agreement.kappa(["relevant", "not relevant"], ["relevant", "not relevant"]) == 1.0


def test_agreement_compares_relevance_on_the_records_both_screened(settings):
  events = [
    _decision("a", "r1", "include", "KM"),
    _decision("b", "r2", "exclude", "KM"),
    _decision("c", "r3", "exclude", "KM"),
    _decision("d", "r4", "include", "KM"),  # the second screener's sample doesn't hold r4
    _decision("e", "r1", "maybe", "XY"),  # maybe and include are both relevant: they agree
    _decision("f", "r2", "include", "XY"),
    _decision("g", "r3", "exclude", "XY"),
  ]

  result = agreement.agreement("XY", events=events, settings=settings)

  assert agreement.second_screeners(events, settings) == ["XY"]
  assert (result["screened_by_second"], result["screened_by_both"]) == (3, 3)
  assert result["agreement"] == 0.667
  assert result["table"]["include"]["maybe"] == 1
  assert result["disagreements"] == [{"record_id": "r2", "primary": "exclude", "second": "include"}]

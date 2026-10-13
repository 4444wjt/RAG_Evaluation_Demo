"""Gate credibility question 4: the uncertainty analysis is reproducible from frozen evidence and never changes the recorded gate."""

import pytest

from tools.competition import gate_uncertainty as uncertainty


def test_formal_run_uncertainty_is_reproducible():
    report = uncertainty.analyze()
    faith = report["metrics"]["faithfulness"]
    assert faith["questions"] == 20 and faith["mean"] == 0.7375
    assert faith["bootstrap95"] == [0.55, 0.9]
    assert faith["shareOfResamplesAtOrAboveThreshold"] == 0.27895
    assert faith["state"] == "证据不足"
    ctx = report["metrics"]["context_precision"]
    assert ctx["state"] == "确定通过" and ctx["bootstrap95"][0] >= 0.8
    assert uncertainty.analyze() == report


def test_aggregation_views_are_reported_without_changing_the_gate():
    report = uncertainty.analyze()
    views = report["faithfulnessViews"]
    assert views["perQuestionRefusalAsZero"]["value"] == 0.7375
    pooled = views["pooledStatements"]
    assert (pooled["supported"], pooled["statements"]) == (43, 52)
    assert [round(x, 3) for x in pooled["wilson95"]] == [0.703, 0.906]
    excluded = views["perQuestionRefusalExcluded"]
    assert (excluded["value"], excluded["questions"]) == (0.921875, 16)
    assert report["recordedGatePassed"] is False


@pytest.mark.parametrize("low, high, state", [(0.81, 0.95, "确定通过"), (0.5, 0.79, "确定不通过"), (0.55, 0.9, "证据不足"), (0.8, 0.8, "确定通过")])
def test_three_state_reading(low, high, state):
    assert uncertainty.three_state(low, high, 0.8) == state


def test_wilson_interval_contains_point_estimate():
    low, high = uncertainty.wilson(43, 52)
    assert low < 43 / 52 < high

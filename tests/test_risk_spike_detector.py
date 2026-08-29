from itertools import pairwise

import pytest

from workers.risk_spike_detector import RiskSpikeDetector


@pytest.mark.unit
def test_gradual_risk_score_rise_does_not_detect_spike():
    detector = RiskSpikeDetector()

    risk_scores = [0.20, 0.25, 0.30, 0.35]

    results = [
        detector.detect_spike(previous, current)
        for previous, current in pairwise(risk_scores)
    ]

    assert all(not result["spike_detected"] for result in results)


@pytest.mark.unit
def test_sudden_risk_score_rise_detects_spike():
    detector = RiskSpikeDetector()

    result = detector.detect_spike(0.20, 0.50)

    assert result["spike_detected"] is True
    assert result["increase"] == 0.30


@pytest.mark.unit
def test_risk_score_increase_at_threshold_detects_spike():
    detector = RiskSpikeDetector()

    result = detector.detect_spike(0.20, 0.45)

    assert result["spike_detected"] is True
    assert result["increase"] == 0.25

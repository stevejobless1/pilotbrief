import pytest
from services.weather.crosswind import CrosswindCalculator

def test_direct_headwind():
    # Runway 31 (heading 310) with wind 310 at 15kt
    comps = CrosswindCalculator.calculate_components(310, 310, 15.0)
    assert comps["headwind"] == 15.0
    assert comps["crosswind"] == 0.0
    assert comps["tailwind"] == 0.0

def test_direct_crosswind():
    # Runway 31 (heading 310) with wind 220 at 10kt (90 deg left crosswind)
    comps = CrosswindCalculator.calculate_components(310, 220, 10.0)
    assert comps["headwind"] == 0.0
    assert comps["crosswind"] == 10.0
    assert comps["crosswind_side"] == "Left"

def test_quartering_headwind():
    # Runway 31 (heading 310) with wind 280 at 14kt (30 deg angle)
    comps = CrosswindCalculator.calculate_components(310, 280, 14.0)
    assert comps["headwind"] > 10.0
    assert comps["crosswind"] > 5.0

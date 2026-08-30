import pytest
from services.weather.minima_checker import MinimaChecker

def test_minima_go_conditions():
    metar = {
        "wind_speed": 8,
        "wind_gust": None,
        "visibility_sm": 10.0,
        "ceiling_ft": 4500
    }
    rwy_evals = [{"runway_id": "31", "crosswind": 4.0, "crosswind_gust": None}]
    eval_res = MinimaChecker.evaluate(metar, rwy_evals)
    assert eval_res["is_go"] is True
    assert len(eval_res["violations"]) == 0

def test_minima_crosswind_violation():
    metar = {
        "wind_speed": 18,
        "wind_gust": 24,
        "visibility_sm": 10.0,
        "ceiling_ft": 4000
    }
    # Best runway has 15kt crosswind against default 10kt limit
    rwy_evals = [{"runway_id": "31", "crosswind": 15.0, "crosswind_gust": 19.0}]
    eval_res = MinimaChecker.evaluate(metar, rwy_evals)
    assert eval_res["is_go"] is False
    assert any("crosswind" in v.lower() for v in eval_res["violations"])

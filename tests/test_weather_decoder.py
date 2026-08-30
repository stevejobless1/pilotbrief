import pytest
from services.weather.decoder import METARDecoder

def test_flight_categories():
    # VFR: Vis > 5, Ceil > 3000
    assert METARDecoder.determine_flight_category(10.0, 5000) == "VFR"
    assert METARDecoder.determine_flight_category(6.0, 3500) == "VFR"

    # MVFR: Ceil 1000-3000 or Vis 3-5
    assert METARDecoder.determine_flight_category(4.0, 5000) == "MVFR"
    assert METARDecoder.determine_flight_category(10.0, 2500) == "MVFR"

    # IFR: Ceil 500-<1000 or Vis 1-<3
    assert METARDecoder.determine_flight_category(2.0, 5000) == "IFR"
    assert METARDecoder.determine_flight_category(10.0, 800) == "IFR"

    # LIFR: Ceil < 500 or Vis < 1
    assert METARDecoder.determine_flight_category(0.5, 4000) == "LIFR"
    assert METARDecoder.determine_flight_category(10.0, 400) == "LIFR"

def test_density_altitude_calculation():
    # Sea level at standard temp 15C and 29.92 inHg
    pa, da = METARDecoder.calculate_density_altitude(0, 15.0, 29.92)
    assert abs(pa - 0) <= 2
    assert abs(da - 0) <= 10

    # Hot day: 35C at 2000ft elevation, altimeter 29.80
    pa_hot, da_hot = METARDecoder.calculate_density_altitude(2000, 35.0, 29.80)
    assert da_hot > pa_hot

def test_carb_icing_assessment():
    # High risk: 15C temp, 14C dewpoint (spread = 1C)
    risk = METARDecoder.assess_carb_icing_risk(15.0, 14.0)
    assert "HIGH" in risk

    # Low risk: 20C temp, 0C dewpoint (spread = 20C)
    low_risk = METARDecoder.assess_carb_icing_risk(20.0, 0.0)
    assert "LOW" in low_risk

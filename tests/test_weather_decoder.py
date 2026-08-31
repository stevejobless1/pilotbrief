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

def test_altimeter_inhg_parsing():
    # US standard METAR A2993
    raw_metar = "METAR KPAO 292347Z 30014KT 10SM SKC 25/11 A2993 RMK TEST"
    altim = METARDecoder.parse_altimeter_inhg(raw_metar, 1013.6)
    assert altim == 29.93

    # Fallback to hPa conversion if no raw string
    altim_conv = METARDecoder.parse_altimeter_inhg("", 1013.25)
    assert altim_conv == 29.92

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
    assert "SERIOUS" in risk or "HIGH" in risk

    # Low risk: 10C temp, 0C dewpoint (rel_hum = 50%)
    low_risk = METARDecoder.assess_carb_icing_risk(10.0, 0.0)
    assert "Low" in low_risk or "LOW" in low_risk

    # Unlikely: 25C temp, 0C dewpoint
    unlikely = METARDecoder.assess_carb_icing_risk(25.0, 0.0)
    assert "Unlikely" in unlikely

def test_decode_taf_periods():
    # Real AWC JSON response structure for KTUS
    taf_payload = {
        "icaoId": "KTUS",
        "rawTAF": "TAF KTUS 302327Z 3100/3124 26008KT P6SM BKN100 OVC200 FM310200 20010G19KT P6SM VCSH BKN100 OVC200 FM310500 17009KT P6SM VCTS BKN080CB OVC150 FM310900 14007KT P6SM BKN100 OVC150",
        "fcsts": [
            {
                "timeFrom": 1788134400,
                "timeTo": 1788141600,
                "fcstChange": None,
                "wdir": 260,
                "wspd": 8,
                "visib": "6+",
                "clouds": [{"cover": "BKN", "base": 10000}, {"cover": "OVC", "base": 20000}]
            },
            {
                "timeFrom": 1788141600,
                "timeTo": 1788152400,
                "fcstChange": "FM",
                "wdir": 200,
                "wspd": 10,
                "wgst": 19,
                "visib": "6+",
                "wxString": "VCSH",
                "clouds": [{"cover": "BKN", "base": 10000}, {"cover": "OVC", "base": 20000}]
            },
            {
                "timeFrom": 1788152400,
                "timeTo": 1788166800,
                "fcstChange": "FM",
                "wdir": 170,
                "wspd": 9,
                "visib": "6+",
                "wxString": "VCTS",
                "clouds": [{"cover": "BKN", "base": 8000, "type": "CB"}, {"cover": "OVC", "base": 15000}]
            },
            {
                "timeFrom": 1788166800,
                "timeTo": 1788220800,
                "fcstChange": "FM",
                "wdir": 140,
                "wspd": 7,
                "visib": "6+",
                "clouds": [{"cover": "BKN", "base": 10000}, {"cover": "OVC", "base": 15000}]
            }
        ]
    }

    decoded = METARDecoder.decode_taf(taf_payload, origin_station="KRYN")
    assert decoded["station"] == "KTUS"
    assert decoded["is_nearby_fallback"] is True
    assert len(decoded["forecasts"]) == 4

    # Verify first period
    p1 = decoded["forecasts"][0]
    assert p1["type"] == "INITIAL"
    assert "260" in p1["wind"]
    assert p1["category"] == "VFR"

    # Verify gusts in second period
    p2 = decoded["forecasts"][1]
    assert "G19kt" in p2["wind"]
    assert p2["weather"] == "VCSH"

    # Verify CB cloud and VCTS weather in third period
    p3 = decoded["forecasts"][2]
    assert p3["weather"] == "VCTS"
    assert "CB" in p3["clouds"]

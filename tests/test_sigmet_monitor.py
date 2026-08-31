import pytest
from services.weather.sigmet_monitor import sigmet_monitor

def test_point_in_polygon():
    # Polygon around KPAO (-122.11, 37.46)
    poly = [[-122.3, 37.3], [-122.3, 37.6], [-121.9, 37.6], [-121.9, 37.3], [-122.3, 37.3]]
    assert sigmet_monitor.point_in_polygon(-122.11, 37.46, poly) is True
    # Point outside (Seattle)
    assert sigmet_monitor.point_in_polygon(-122.33, 47.60, poly) is False

def test_min_distance_to_polygon():
    poly = [[-122.3, 37.3], [-122.3, 37.6], [-121.9, 37.6], [-121.9, 37.3], [-122.3, 37.3]]
    # Point inside returns 0
    assert sigmet_monitor.min_distance_to_polygon_nm(-122.11, 37.46, poly) == 0.0
    # Point ~25nm north
    dist = sigmet_monitor.min_distance_to_polygon_nm(-122.11, 38.0, poly)
    assert 20.0 <= dist <= 30.0

def test_evaluate_convective_hazard_overhead():
    # Mock Convective SIGMET feature directly covering KPAO
    mock_sigmets = [
        {
            "properties": {
                "airSigmetId": "1W",
                "hazard": "CONVECTIVE",
                "altitudeHi1": 45000,
                "validTimeFrom": "2026-08-31T00:00:00Z",
                "validTimeTo": "2026-08-31T02:00:00Z",
                "rawAirSigmet": "SIGMET CONVECTIVE 1W..."
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-122.3, 37.3], [-122.3, 37.6], [-121.9, 37.6], [-121.9, 37.3], [-122.3, 37.3]]]
            }
        }
    ]

    hazards = sigmet_monitor.evaluate_airport_convective_hazards("KPAO", mock_sigmets, proximity_nm=20.0)
    assert len(hazards) == 1
    assert hazards[0]["is_overhead"] is True
    assert hazards[0]["distance_nm"] == 0.0
    assert hazards[0]["top_fl"] == "FL450"

def test_evaluate_ignores_non_convective():
    # Mock Turbulence SIGMET
    mock_turb = [
        {
            "properties": {
                "airSigmetId": "OSCAR 1",
                "hazard": "TURB",
                "rawAirSigmet": "SIGMET TURB OSCAR 1..."
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-122.3, 37.3], [-122.3, 37.6], [-121.9, 37.6], [-121.9, 37.3], [-122.3, 37.3]]]
            }
        }
    ]
    hazards = sigmet_monitor.evaluate_airport_convective_hazards("KPAO", mock_turb, proximity_nm=20.0)
    assert len(hazards) == 0

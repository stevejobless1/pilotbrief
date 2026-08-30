import pytest
from services.calendar.event_extractor import FlightEventExtractor

def test_is_flight_event():
    assert FlightEventExtractor.is_flight_event("Flight Lesson with CFI Bob") is True
    assert FlightEventExtractor.is_flight_event("Solo practice C172") is True
    assert FlightEventExtractor.is_flight_event("Dual XC to KSTS") is True
    assert FlightEventExtractor.is_flight_event("Dentist Appointment") is False

def test_extract_airports_route():
    dep, dest = FlightEventExtractor.extract_airports("Flight Lesson KPAO to KSTS")
    assert dep == "KPAO"
    assert dest == "KSTS"

def test_extract_airports_arrow():
    dep, dest = FlightEventExtractor.extract_airports("Solo XC KSQL -> KMRY")
    assert dep == "KSQL"
    assert dest == "KMRY"

def test_extract_single_airport_with_fallback():
    dep, dest = FlightEventExtractor.extract_airports("Pattern work at KRHV", default_home="KPAO")
    assert dep == "KRHV"
    assert dest is None

def test_extract_default_home_fallback():
    dep, dest = FlightEventExtractor.extract_airports("Flight lesson #4", default_home="KPAO")
    assert dep == "KPAO"
    assert dest is None

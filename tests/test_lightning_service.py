import pytest
import time
from services.weather.lightning_service import LightningService, decompress_lzw

def test_lzw_decompression():
    # Test LZW decompression with standard strings
    raw = '{"time":1700000000,"lat":37.5,"lon":-122.1}'
    decomp = decompress_lzw(raw)
    assert '{"time":1700000000' in decomp
    assert '"lat":37.5' in decomp

def test_lightning_service_buffering():
    svc = LightningService(max_history_hours=1.0)
    now_ms = int(time.time() * 1000)

    # Add strikes across different locations and times
    svc.add_strike(time_ms=now_ms - 5000, lat=37.46, lon=-122.11)  # KPAO
    svc.add_strike(time_ms=now_ms - 10000, lat=37.62, lon=-122.37) # KSFO
    svc.add_strike(time_ms=now_ms - 60000, lat=40.71, lon=-74.00)  # NYC

    # 1. Query all strikes
    all_strikes = svc.get_strikes()
    assert len(all_strikes) == 3

    # 2. Query Bay Area BBox
    bay_strikes = svc.get_strikes(bbox=(36.0, -123.5, 38.5, -121.0))
    assert len(bay_strikes) == 2
    assert bay_strikes[0]["lat"] in (37.46, 37.62)

    # 3. Query Time Window (last 15s)
    recent_strikes = svc.get_strikes(since_ms=now_ms - 15000)
    assert len(recent_strikes) == 2

def test_lightning_stats():
    svc = LightningService(max_history_hours=1.0)
    now_ms = int(time.time() * 1000)
    svc.add_strike(time_ms=now_ms, lat=37.0, lon=-122.0)
    stats = svc.get_stats()
    assert stats["total_buffered"] == 1
    assert stats["rate_per_min"] >= 1

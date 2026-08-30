import pytest
from services.radar.map_generator import radar_map_generator

@pytest.mark.asyncio
async def test_generate_briefing_map():
    # Test local flight map generation
    png_bytes = await radar_map_generator.generate_briefing_map(
        dep_icao="KPAO",
        dest_icao="KSTS",
        dep_fltcat="VFR",
        dest_fltcat="MVFR"
    )
    assert png_bytes is not None
    assert len(png_bytes) > 1000
    # Check PNG signature: \x89PNG
    assert png_bytes[:4] == b"\x89PNG"

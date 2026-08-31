import pytest
import discord
from datetime import datetime, timezone
from bot.views.briefing_embeds import BriefingEmbedBuilder, BriefingView

def test_build_briefing_embed_local_flight():
    metar_dep = {
        "station": "KPAO",
        "raw": "METAR KPAO 302347Z 30014G20KT 10SM CLR 25/11 A2992 RMK AO2",
        "category": "VFR",
        "category_color": 0x00B894,
        "category_emoji": "🟢",
        "wind_str": "300° at 14kt G20kt",
        "visibility_str": "10+ SM",
        "clouds_str": "Clear / SKC",
        "temp_str": "25.0°C",
        "dew_str": "11.0°C",
        "temp_dew_spread": "14.0°C",
        "altimeter_inhg": 29.92,
        "altimeter_hpa": 1013,
        "pressure_altitude": 50,
        "density_altitude": 1250,
        "carb_icing_risk": "🟢 Risk Unlikely"
    }

    runway_evals = [
        {
            "runway_id": "31",
            "heading": 310.0,
            "headwind": 13.8,
            "tailwind": 0.0,
            "crosswind": 2.4,
            "crosswind_side": "Left",
            "crosswind_gust": 3.5
        },
        {
            "runway_id": "13",
            "heading": 130.0,
            "headwind": 0.0,
            "tailwind": 13.8,
            "crosswind": 2.4,
            "crosswind_side": "Right",
            "crosswind_gust": 3.5
        }
    ]

    minima_eval = {
        "is_go": True,
        "decision": "✅ GO (Conditions within student minimums)",
        "violations": [],
        "warnings": []
    }

    taf_dep = {
        "station": "KPAO",
        "is_nearby_fallback": False,
        "raw": "TAF KPAO ...",
        "forecasts": [
            {
                "type": "INITIAL",
                "time_window": "3023:00Z - 3106:00Z",
                "wind": "300° at 12kt",
                "vis": "6+ SM",
                "weather": None,
                "clouds": "SKC",
                "category_emoji": "🟢"
            }
        ]
    }

    sigmets = [
        {
            "properties": {
                "hazard": "CONVECTIVE",
                "altitudeHi1": 45000
            }
        }
    ]

    notams = [
        {
            "priority": "HIGH",
            "category": "RUNWAY",
            "text": "RWY 13/31 CLSD DAILY FOR MAINTENANCE"
        }
    ]

    embed = BriefingEmbedBuilder.build_briefing_embed(
        event_title="Pattern Solo Practice",
        departure_icao="KPAO",
        destination_icao=None,
        start_time_utc=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
        metar_dep=metar_dep,
        metar_dest=None,
        taf_dep=taf_dep,
        runway_evals=runway_evals,
        minima_eval=minima_eval,
        notams=notams,
        sigmets=sigmets,
        milestone_label="PREFLIGHT BRIEFING"
    )

    assert isinstance(embed, discord.Embed)
    assert "PREFLIGHT BRIEFING • KPAO (Local)" in embed.title
    assert "Pattern Solo Practice" in embed.description
    assert "✅ GO" in embed.description

    field_names = [f.name for f in embed.fields]
    assert any("Departure (KPAO)" in n for n in field_names)
    assert any("Runway & Wind Analysis" in n for n in field_names)
    assert any("Forecast / TAF" in n for n in field_names)
    assert any("Active SIGMETs" in n for n in field_names)
    assert any("Key NOTAM Highlights" in n for n in field_names)

    dep_field = next(f for f in embed.fields if "Departure (KPAO)" in f.name)
    assert "300° at 14kt G20kt" in dep_field.value
    assert "10+ SM" in dep_field.value
    assert "25.0°C / 11.0°C" in dep_field.value
    assert "29.92\"" in dep_field.value
    assert "1,250 ft" in dep_field.value

def test_build_briefing_embed_cross_country_with_violations():
    metar_dep = {
        "station": "KPAO",
        "category": "VFR",
        "category_color": 0x00B894,
        "category_emoji": "🟢",
        "wind_str": "310° at 18kt G25kt",
        "visibility_str": "10+ SM",
        "clouds_str": "FEW030",
        "temp_str": "20.0°C",
        "dew_str": "10.0°C",
        "temp_dew_spread": "10.0°C",
        "altimeter_inhg": 29.95,
        "altimeter_hpa": 1014,
        "pressure_altitude": 20,
        "density_altitude": 800,
        "carb_icing_risk": "🟢 Risk Unlikely"
    }

    metar_dest = {
        "station": "KSTS",
        "category": "MVFR",
        "category_color": 0x0984E3,
        "category_emoji": "🔵",
        "wind_str": "280° at 10kt",
        "visibility_str": "5 SM",
        "clouds_str": "BKN025",
        "altimeter_inhg": 29.98,
        "density_altitude": 600
    }

    minima_eval = {
        "is_go": False,
        "decision": "🛑 NO-GO / REVIEW (Exceeds personal minimums)",
        "violations": ["Surface wind (18kt) exceeds student limit (15kt)"],
        "warnings": ["Wind gust factor (+7kt) exceeds comfort limit"]
    }

    embed = BriefingEmbedBuilder.build_briefing_embed(
        event_title="Dual Cross-Country to Santa Rosa",
        departure_icao="KPAO",
        destination_icao="KSTS",
        start_time_utc=datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc),
        metar_dep=metar_dep,
        metar_dest=metar_dest,
        taf_dep=None,
        runway_evals=[],
        minima_eval=minima_eval,
        notams=[],
        sigmets=[],
        milestone_label="2 HOURS OUT BRIEFING"
    )

    assert "KPAO ➔ KSTS" in embed.title
    assert "🛑 NO-GO" in embed.description
    field_names = [f.name for f in embed.fields]
    assert "🛑 Personal Minimums Exceeded" in field_names
    assert any("Destination (KSTS)" in n for n in field_names)

@pytest.mark.asyncio
async def test_briefing_view_init():
    view = BriefingView(
        raw_metar="METAR KPAO 302347Z...",
        raw_taf="TAF KPAO 302327Z...",
        tactical_map_bytes=b"tactical",
        sectional_map_bytes=b"sectional",
        raw_dest_metar="METAR KSTS 302347Z..."
    )
    assert view.raw_metar == "METAR KPAO 302347Z..."
    assert view.raw_dest_metar == "METAR KSTS 302347Z..."
    assert view.raw_taf == "TAF KPAO 302327Z..."

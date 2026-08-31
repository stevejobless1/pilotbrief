import os
import io
import sys
import math
import time
import json
import logging
import asyncio
import aiohttp
import platform
import ctypes
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from aiohttp import web

from config.settings import settings
from services.weather.awc_client import AWCClient
from services.weather.decoder import METARDecoder
WeatherDecoder = METARDecoder
from services.weather.crosswind import airport_db, CrosswindCalculator
from services.weather.sigmet_monitor import SigmetMonitor
from services.weather.lightning_service import lightning_service
from services.radar.map_generator import RadarMapGenerator

logger = logging.getLogger("PilotBrief.Web")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "static"

# Server start timestamp and telemetry metrics
_SERVER_START_TIME = time.time()
_REQUEST_COUNTERS = {
    "total_requests": 0,
    "last_minute_requests": 0,
    "last_minute_ts": time.time()
}

_BANDWIDTH_COUNTERS = {
    "total_bytes_sent": 0,
    "total_bytes_received": 0,
    "interval_bytes_sent": 0,
    "interval_bytes_received": 0,
    "last_calc_ts": time.time(),
    "sent_rate_kbps": 0.0,
    "recv_rate_kbps": 0.0
}

# Caches to prevent API overpolling
_sigmets_cache = {
    "data": None,
    "timestamp": 0
}

_radar_frames_cache = {
    "data": None,
    "timestamp": 0
}

_adsb_cache = {
    "data": None,
    "timestamp": 0,
    "key": ""
}

_metars_cache = {}

# Global regional METARs cache with spatial filtering
_regional_metars_cache = {
    "stations": [],
    "timestamp": 0
}


def _update_bandwidth(bytes_in: int, bytes_out: int):
    """Updates real-time network throughput and data transfer counters."""
    now = time.time()
    _BANDWIDTH_COUNTERS["total_bytes_received"] += bytes_in
    _BANDWIDTH_COUNTERS["total_bytes_sent"] += bytes_out
    _BANDWIDTH_COUNTERS["interval_bytes_received"] += bytes_in
    _BANDWIDTH_COUNTERS["interval_bytes_sent"] += bytes_out

    dt = now - _BANDWIDTH_COUNTERS["last_calc_ts"]
    if dt >= 2.0:
        _BANDWIDTH_COUNTERS["recv_rate_kbps"] = (_BANDWIDTH_COUNTERS["interval_bytes_received"] / 1024.0) / dt
        _BANDWIDTH_COUNTERS["sent_rate_kbps"] = (_BANDWIDTH_COUNTERS["interval_bytes_sent"] / 1024.0) / dt
        _BANDWIDTH_COUNTERS["interval_bytes_received"] = 0
        _BANDWIDTH_COUNTERS["interval_bytes_sent"] = 0
        _BANDWIDTH_COUNTERS["last_calc_ts"] = now


def _get_system_telemetry() -> Dict[str, Any]:
    """
    Collects system, memory, network bandwidth, and runtime diagnostics.
    """
    now = time.time()
    uptime_sec = int(now - _SERVER_START_TIME)

    # Request rate tracking
    if now - _REQUEST_COUNTERS["last_minute_ts"] >= 60:
        _REQUEST_COUNTERS["last_minute_requests"] = 0
        _REQUEST_COUNTERS["last_minute_ts"] = now

    # Memory info - cross platform (Windows, Linux/Docker, macOS)
    total_ram_mb = 0
    avail_ram_mb = 0
    used_percent = 0

    try:
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_ram_mb = round(stat.ullTotalPhys / (1024 * 1024))
            avail_ram_mb = round(stat.ullAvailPhys / (1024 * 1024))
            used_percent = int(stat.dwMemoryLoad)
        elif os.path.exists("/proc/meminfo"):
            mem_info = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        mem_info[key] = int(val)
            if "MemTotal" in mem_info:
                total_ram_mb = round(mem_info["MemTotal"] / 1024)
                avail = mem_info.get("MemAvailable", mem_info.get("MemFree", 0))
                avail_ram_mb = round(avail / 1024)
                used_ram = total_ram_mb - avail_ram_mb
                used_percent = round((used_ram / total_ram_mb) * 100) if total_ram_mb > 0 else 0
    except Exception:
        pass

    # Total network transferred
    tot_rx_bytes = _BANDWIDTH_COUNTERS["total_bytes_received"]
    tot_tx_bytes = _BANDWIDTH_COUNTERS["total_bytes_sent"]
    tot_mb = round((tot_rx_bytes + tot_tx_bytes) / (1024 * 1024), 2)

    return {
        "server": {
            "uptime_seconds": uptime_sec,
            "uptime_formatted": f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s",
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "pid": os.getpid()
        },
        "memory": {
            "total_ram_mb": total_ram_mb,
            "avail_ram_mb": avail_ram_mb,
            "used_ram_mb": max(0, total_ram_mb - avail_ram_mb),
            "used_percent": used_percent
        },
        "network": {
            "total_bytes_sent": tot_tx_bytes,
            "total_bytes_received": tot_rx_bytes,
            "total_mb_transferred": tot_mb,
            "tx_rate_kbps": round(_BANDWIDTH_COUNTERS["sent_rate_kbps"], 1),
            "rx_rate_kbps": round(_BANDWIDTH_COUNTERS["recv_rate_kbps"], 1),
            "total_requests": _REQUEST_COUNTERS["total_requests"],
            "requests_per_min": _REQUEST_COUNTERS["last_minute_requests"]
        },
        "requests": {
            "total_served": _REQUEST_COUNTERS["total_requests"],
            "requests_per_min": _REQUEST_COUNTERS["last_minute_requests"]
        },
        "caches": {
            "regional_metars_count": len(_regional_metars_cache["stations"]),
            "regional_metars_age_sec": int(now - _regional_metars_cache["timestamp"]) if _regional_metars_cache["timestamp"] else None,
            "sigmets_count": len(_sigmets_cache["data"].get("features", [])) if _sigmets_cache["data"] else 0,
            "sigmets_age_sec": int(now - _sigmets_cache["timestamp"]) if _sigmets_cache["timestamp"] else None,
            "radar_frames_count": len(_radar_frames_cache["data"].get("frames", [])) if _radar_frames_cache["data"] else 0,
            "radar_frames_age_sec": int(now - _radar_frames_cache["timestamp"]) if _radar_frames_cache["timestamp"] else None,
            "adsb_aircraft_count": len(_adsb_cache["data"].get("aircraft", [])) if _adsb_cache["data"] else 0,
            "adsb_age_sec": int(now - _adsb_cache["timestamp"]) if _adsb_cache["timestamp"] else None
        },
        "traffic": {
            "aircraft_tracked": len(_adsb_cache["data"].get("aircraft", [])) if _adsb_cache["data"] else 0,
            "last_query_ts": _adsb_cache["timestamp"],
            "provider": "adsb.lol" if _adsb_cache["data"] else "Standby"
        },
        "lightning": lightning_service.get_stats()
    }


def _calculate_course_and_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, float]:
    """
    Calculates great-circle distance in NM and initial true course in degrees.
    """
    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)
    delta_phi = phi2 - phi1
    delta_lambda = lambda2 - lambda1

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist_nm = c * 3440.065

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    return round(dist_nm, 1), round(bearing, 1)


class WebHandlers:
    def __init__(self):
        self.awc_client = AWCClient()
        self.map_generator = RadarMapGenerator()

    async def handle_index(self, request: web.Request) -> web.Response:
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        return web.Response(text="PilotBrief Web Map Server Running", content_type="text/plain")

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "service": "PilotBrief Aviation Web Deck",
            "home_icao": settings.HOME_ICAO,
            "version": "2.1.0"
        })

    async def handle_config(self, request: web.Request) -> web.Response:
        return web.json_response({
            "home_icao": settings.HOME_ICAO,
            "default_intervals": settings.DEFAULT_ALERT_INTERVALS,
            "carto_api_key": settings.CARTO_API_KEY
        })

    async def handle_insights(self, request: web.Request) -> web.Response:
        return web.json_response(_get_system_telemetry())

    async def handle_metar(self, request: web.Request) -> web.Response:
        icao = request.query.get("icao", "").strip().upper()
        if not icao:
            return web.json_response({"error": "Missing 'icao' parameter"}, status=400)

        now = time.time()
        if icao in _metars_cache and (now - _metars_cache[icao]["ts"]) < 60:
            return web.json_response(_metars_cache[icao]["data"])

        raw_data = await self.awc_client.get_metar(icao)
        if not raw_data:
            return web.json_response({"error": f"No METAR available for {icao}"}, status=404)

        elev = airport_db.get_elevation(icao) or 0.0
        decoded = WeatherDecoder.decode_metar(raw_data, elevation_ft=elev)

        result = {
            "icao": icao,
            "raw_data": raw_data,
            "decoded": decoded
        }
        _metars_cache[icao] = {"data": result, "ts": now}
        return web.json_response(result)

    async def handle_taf(self, request: web.Request) -> web.Response:
        icao = request.query.get("icao", "").strip().upper()
        if not icao:
            return web.json_response({"error": "Missing 'icao' parameter"}, status=400)

        taf_data, station_used, dist_nm = await self.awc_client.get_best_taf(icao)
        if not taf_data:
            return web.json_response({"error": f"No TAF available for {icao}"}, status=404)

        decoded = WeatherDecoder.decode_taf(taf_data, origin_station=icao)
        decoded["station_used"] = station_used
        decoded["distance_from_origin_nm"] = dist_nm

        return web.json_response({
            "icao": icao,
            "station_used": station_used,
            "distance_nm": dist_nm,
            "raw_data": taf_data,
            "decoded": decoded
        })

    async def handle_regional_metars(self, request: web.Request) -> web.Response:
        """
        Returns regional METAR stations within requested bbox.
        Uses in-memory spatial cache with a 120s TTL to prevent overpolling NOAA AWC API.
        """
        global _regional_metars_cache
        bbox = request.query.get("bbox", "")
        if not bbox:
            bbox = "36.0,-123.5,38.8,-121.0"

        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            min_lat, min_lon, max_lat, max_lon = parts[0], parts[1], parts[2], parts[3]
        except Exception:
            return web.json_response({"error": "Invalid bbox format. Expected: min_lat,min_lon,max_lat,max_lon"}, status=400)

        now = time.time()
        # Fetch or refresh global regional cache if expired
        if not _regional_metars_cache["stations"] or (now - _regional_metars_cache["timestamp"]) > 120:
            # Query broad US/coverage bounding box to populate spatial cache
            fresh_stations = await self.map_generator._fetch_regional_metars(-130.0, 23.0, -65.0, 52.0)
            if fresh_stations:
                parsed_list = []
                for stn in fresh_stations:
                    try:
                        stn_icao = stn.get("icaoId", "").upper()
                        if not stn_icao:
                            continue
                        stn_lat = float(stn.get("lat", 0))
                        stn_lon = float(stn.get("lon", 0))
                        fltcat = str(stn.get("fltcat") or stn.get("fltCat", "VFR")).upper()
                        wdir = stn.get("wdir")
                        wspd = stn.get("wspd")
                        wgst = stn.get("wgst")
                        visib = stn.get("visib")
                        altim = stn.get("altim")
                        temp = stn.get("temp")
                        dewp = stn.get("dewp")
                        cover = stn.get("cover")
                        ceil = stn.get("ceil")
                        raw = stn.get("rawOb", "")

                        parsed_list.append({
                            "icao": stn_icao,
                            "name": stn.get("name") or stn_icao,
                            "lat": stn_lat,
                            "lon": stn_lon,
                            "fltcat": fltcat,
                            "wdir": wdir,
                            "wspd": wspd,
                            "wgst": wgst,
                            "visib": visib,
                            "altim": altim,
                            "temp": temp,
                            "dewp": dewp,
                            "cover": cover,
                            "ceil": ceil,
                            "raw": raw
                        })
                    except Exception:
                        continue
                _regional_metars_cache = {
                    "stations": parsed_list,
                    "timestamp": now
                }

        # Filter stations matching requested bbox
        results = [
            stn for stn in _regional_metars_cache["stations"]
            if (min_lat - 0.2) <= stn["lat"] <= (max_lat + 0.2) and (min_lon - 0.2) <= stn["lon"] <= (max_lon + 0.2)
        ]

        # If cache was empty and returned 0, try direct fetch for bbox as fallback
        if not results and (now - _regional_metars_cache["timestamp"]) > 30:
            direct_fetch = await self.map_generator._fetch_regional_metars(min_lon, min_lat, max_lon, max_lat)
            for stn in direct_fetch:
                try:
                    stn_icao = stn.get("icaoId", "").upper()
                    if not stn_icao:
                        continue
                    results.append({
                        "icao": stn_icao,
                        "name": stn.get("name") or stn_icao,
                        "lat": float(stn.get("lat", 0)),
                        "lon": float(stn.get("lon", 0)),
                        "fltcat": str(stn.get("fltcat") or stn.get("fltCat", "VFR")).upper(),
                        "wdir": stn.get("wdir"),
                        "wspd": stn.get("wspd"),
                        "wgst": stn.get("wgst"),
                        "visib": stn.get("visib"),
                        "altim": stn.get("altim"),
                        "temp": stn.get("temp"),
                        "dewp": stn.get("dewp"),
                        "cover": stn.get("cover"),
                        "ceil": stn.get("ceil"),
                        "raw": stn.get("rawOb", "")
                    })
                except Exception:
                    continue

        return web.json_response({"count": len(results), "stations": results})

    async def handle_sigmets(self, request: web.Request) -> web.Response:
        """
        Returns active NOAA SIGMET & AIRMET polygons as GeoJSON with hazard styling.
        """
        global _sigmets_cache
        now = time.time()
        if _sigmets_cache["data"] and (now - _sigmets_cache["timestamp"]) < 120:
            return web.json_response(_sigmets_cache["data"])

        url = "https://aviationweather.gov/api/data/airsigmet?format=geojson"
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": "PilotBrief-Web/1.0"}) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        geojson_data = await resp.json()
                        features = geojson_data.get("features", [])

                        for feat in features:
                            props = feat.get("properties", {})
                            hazard = str(props.get("hazard", "")).upper()
                            alpha = str(props.get("alphaChar", "")).upper()
                            raw = str(props.get("rawAirSigmet") or props.get("rawText", "")).upper()

                            if "CONVECTIVE" in hazard or "TS" in hazard or "C" in alpha or "CONVECTIVE" in raw:
                                feat_type = "CONVECTIVE"
                                color = "#FF3838"
                                label = "⚡ Convective SIGMET"
                            elif "TURB" in hazard or "TURBULENCE" in raw:
                                feat_type = "TURBULENCE"
                                color = "#FF9F1A"
                                label = "💨 Turbulence AIRMET/SIGMET"
                            elif "ICE" in hazard or "ICING" in hazard or "ICING" in raw:
                                feat_type = "ICING"
                                color = "#00D2D3"
                                label = "❄️ Icing AIRMET/SIGMET"
                            elif "IFR" in hazard or "MTN" in hazard or "MOUNTAIN" in raw:
                                feat_type = "IFR_OBSCURATION"
                                color = "#A29BFE"
                                label = "⛰️ IFR / Mountain Obscuration"
                            else:
                                feat_type = "OTHER"
                                color = "#FA8231"
                                label = f"⚠️ {hazard or 'Hazard Area'}"

                            props["hazard_category"] = feat_type
                            props["display_color"] = color
                            props["display_label"] = label

                        _sigmets_cache = {
                            "data": geojson_data,
                            "timestamp": now
                        }
                        return web.json_response(geojson_data)
        except Exception as e:
            logger.error(f"Error fetching NOAA SIGMETs: {e}")

        if _sigmets_cache["data"]:
            return web.json_response(_sigmets_cache["data"])
        return web.json_response({"type": "FeatureCollection", "features": []})

    async def handle_radar_frames(self, request: web.Request) -> web.Response:
        """
        Returns available radar frames for the past 2-3 hours for timeline replay.
        """
        global _radar_frames_cache
        now = time.time()
        if _radar_frames_cache["data"] and (now - _radar_frames_cache["timestamp"]) < 120:
            return web.json_response(_radar_frames_cache["data"])

        url = "https://api.rainviewer.com/public/weather-maps.json"
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": "PilotBrief-Web/1.0"}) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        host = data.get("host", "https://tilecache.rainviewer.com")
                        past_frames = data.get("radar", {}).get("past", [])
                        nowcast_frames = data.get("radar", {}).get("nowcast", [])

                        formatted_frames = []
                        all_frames = past_frames + nowcast_frames

                        for f in all_frames:
                            t_sec = f.get("time", 0)
                            dt = datetime.fromtimestamp(t_sec, tz=timezone.utc)
                            path = f.get("path", "")
                            rel_mins = int((t_sec - now) / 60)
                            tile_url = f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"

                            formatted_frames.append({
                                "time": t_sec,
                                "time_ms": t_sec * 1000,
                                "utc_time": dt.strftime("%H:%MZ"),
                                "iso": dt.isoformat(),
                                "path": path,
                                "tile_url": tile_url,
                                "relative_mins": rel_mins,
                                "is_nowcast": (t_sec > now)
                            })

                        result = {
                            "host": host,
                            "count": len(formatted_frames),
                            "frames": formatted_frames
                        }
                        _radar_frames_cache = {
                            "data": result,
                            "timestamp": now
                        }
                        return web.json_response(result)
        except Exception as e:
            logger.error(f"Error fetching radar replay frames: {e}")

        # Fallback to IEM WMS relative frames if RainViewer is unavailable
        iem_frames = []
        for m in [120, 105, 90, 75, 60, 50, 40, 30, 20, 10, 5, 0]:
            t_sec = int(now - m * 60)
            dt = datetime.fromtimestamp(t_sec, tz=timezone.utc)
            layer_name = f"nexrad-n0q-m{m:02d}m" if m > 0 else "nexrad-n0q-m05m"
            wms_url = (
                f"https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi?"
                f"SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS={layer_name}&"
                f"FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:3857"
            )
            iem_frames.append({
                "time": t_sec,
                "time_ms": t_sec * 1000,
                "utc_time": dt.strftime("%H:%MZ"),
                "iso": dt.isoformat(),
                "path": layer_name,
                "wms_url": wms_url,
                "relative_mins": -m,
                "is_nowcast": False
            })

        return web.json_response({
            "host": "https://mesonet.agron.iastate.edu",
            "count": len(iem_frames),
            "frames": iem_frames
        })

    async def handle_lightning(self, request: web.Request) -> web.Response:
        """
        Returns live and historical lightning strikes within requested time window and bounding box.
        """
        now_ms = int(time.time() * 1000)

        since_ms = request.query.get("since_ms")
        until_ms = request.query.get("until_ms")
        window_mins = request.query.get("window_mins")
        bbox_str = request.query.get("bbox")

        since_val = None
        until_val = None

        if since_ms:
            try:
                since_val = int(since_ms)
            except ValueError:
                pass

        if until_ms:
            try:
                until_val = int(until_ms)
            except ValueError:
                pass

        if window_mins and not since_val:
            try:
                wm = float(window_mins)
                since_val = now_ms - int(wm * 60 * 1000)
            except ValueError:
                pass

        bbox_tuple = None
        if bbox_str:
            try:
                parts = [float(x.strip()) for x in bbox_str.split(",")]
                if len(parts) == 4:
                    bbox_tuple = (parts[0], parts[1], parts[2], parts[3])
            except Exception:
                pass

        strikes = lightning_service.get_strikes(
            since_ms=since_val,
            until_ms=until_val,
            bbox=bbox_tuple,
            max_results=4000
        )

        return web.json_response({
            "count": len(strikes),
            "now_ms": now_ms,
            "stats": lightning_service.get_stats(),
            "strikes": strikes
        })

    async def handle_lightning_stats(self, request: web.Request) -> web.Response:
        return web.json_response(lightning_service.get_stats())

    async def handle_airports_search(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip().upper()
        if not query or len(query) < 2:
            return web.json_response({"results": []})

        matches = []
        for icao, apt in airport_db._airports.items():
            name = apt.get("name", "")
            if query in icao or query in name.upper():
                matches.append({
                    "icao": icao,
                    "name": name,
                    "lat": apt.get("lat"),
                    "lon": apt.get("lon"),
                    "elevation": apt.get("elevation", 0),
                    "runways": apt.get("runways", [])
                })
                if len(matches) >= 15:
                    break

        if not matches and len(query) in (3, 4):
            icao_candidate = query if len(query) == 4 else f"K{query}"
            apt = airport_db.fetch_dynamic_airport(icao_candidate)
            if apt:
                matches.append({
                    "icao": icao_candidate,
                    "name": apt.get("name", icao_candidate),
                    "lat": apt.get("lat"),
                    "lon": apt.get("lon"),
                    "elevation": apt.get("elevation", 0),
                    "runways": apt.get("runways", [])
                })

        return web.json_response({"results": matches})

    async def handle_airport_details(self, request: web.Request) -> web.Response:
        icao = request.match_info.get("icao", "").strip().upper()
        if not icao:
            return web.json_response({"error": "Missing ICAO"}, status=400)

        coord = airport_db.get_coordinates(icao)
        if not coord:
            apt = airport_db.fetch_dynamic_airport(icao)
            if not apt:
                return web.json_response({"error": f"Airport {icao} not found"}, status=404)

        elev = airport_db.get_elevation(icao) or 0.0
        runways = airport_db.get_runways(icao)
        lat, lon = airport_db.get_coordinates(icao) or (0.0, 0.0)

        metar_raw = await self.awc_client.get_metar(icao)
        runway_analysis = []
        decoded_metar = None

        if metar_raw:
            decoded_metar = WeatherDecoder.decode_metar(metar_raw, elevation_ft=elev)
            wdir = decoded_metar.get("wind_dir")
            wspd = decoded_metar.get("wind_speed", 0)
            wgst = decoded_metar.get("wind_gust")

            for rwy in runways:
                ident = rwy.get("ident", "")
                hdg = rwy.get("heading", 0)
                length = rwy.get("length_ft", 0)
                comp = CrosswindCalculator.calculate_components(hdg, wdir, wspd, wgst)
                runway_analysis.append({
                    "ident": ident,
                    "heading": hdg,
                    "length_ft": length,
                    "headwind": comp["headwind"],
                    "tailwind": comp["tailwind"],
                    "crosswind": comp["crosswind"],
                    "crosswind_gust": comp["crosswind_gust"],
                    "crosswind_side": comp["crosswind_side"],
                    "is_favorable": False
                })

            if runway_analysis:
                best = max(runway_analysis, key=lambda x: (x["headwind"] - x["crosswind"] * 0.4))
                best["is_favorable"] = True

        return web.json_response({
            "icao": icao,
            "name": airport_db._airports.get(icao, {}).get("name", icao),
            "lat": lat,
            "lon": lon,
            "elevation_ft": elev,
            "runways": runway_analysis if runway_analysis else runways,
            "metar": decoded_metar
        })

    async def handle_route(self, request: web.Request) -> web.Response:
        dep = request.query.get("dep", "").strip().upper()
        dest = request.query.get("dest", "").strip().upper()

        if not dep:
            return web.json_response({"error": "Missing departure ICAO 'dep'"}, status=400)

        dep_coord = airport_db.get_coordinates(dep)
        if not dep_coord:
            return web.json_response({"error": f"Departure airport '{dep}' coordinates not found"}, status=404)

        dep_lat, dep_lon = dep_coord
        response_data = {
            "dep": {
                "icao": dep,
                "lat": dep_lat,
                "lon": dep_lon,
                "elevation_ft": airport_db.get_elevation(dep) or 0
            }
        }

        if dest:
            dest_coord = airport_db.get_coordinates(dest)
            if dest_coord:
                dest_lat, dest_lon = dest_coord
                dist_nm, true_course = _calculate_course_and_distance(dep_lat, dep_lon, dest_lat, dest_lon)

                mid_lat = (dep_lat + dest_lat) / 2.0
                mid_lon = (dep_lon + dest_lon) / 2.0

                response_data["dest"] = {
                    "icao": dest,
                    "lat": dest_lat,
                    "lon": dest_lon,
                    "elevation_ft": airport_db.get_elevation(dest) or 0
                }
                response_data["route"] = {
                    "distance_nm": dist_nm,
                    "true_course_deg": true_course,
                    "midpoint": [mid_lat, mid_lon],
                    "coordinates": [
                        [dep_lat, dep_lon],
                        [dest_lat, dest_lon]
                    ]
                }

        nm_to_meters = 1852.0
        rings = []
        for nm in [25, 50, 75, 100]:
            rings.append({
                "nm": nm,
                "radius_meters": nm * nm_to_meters
            })
        response_data["range_rings"] = rings

        return web.json_response(response_data)

    async def handle_map_render(self, request: web.Request) -> web.Response:
        dep = request.query.get("dep", settings.HOME_ICAO).strip().upper()
        dest = request.query.get("dest")
        if dest:
            dest = dest.strip().upper()

        radius = 95.0
        try:
            if request.query.get("radius"):
                radius = float(request.query.get("radius"))
        except Exception:
            pass

        sigmets = None
        if _sigmets_cache["data"]:
            sigmets = _sigmets_cache["data"].get("features", [])

        img_bytes = await self.map_generator.generate_sectional_overview_map(
            dep_icao=dep,
            dest_icao=dest,
            sigmets=sigmets,
            radius_nm=radius
        )

        if not img_bytes:
            return web.Response(text="Could not render map", status=500)

        return web.Response(body=img_bytes, content_type="image/png")

    async def handle_adsb_traffic(self, request: web.Request) -> web.Response:
        """
        Fetches live ADS-B aircraft traffic from free community aggregators (adsb.lol / OpenSky).
        Supports spatial querying by bounding box (bbox=min_lat,min_lon,max_lat,max_lon)
        or center coordinate + radius (lat, lon, radius_nm).
        """
        global _adsb_cache
        now = time.time()

        bbox_str = request.query.get("bbox", "").strip()
        lat_str = request.query.get("lat", "").strip()
        lon_str = request.query.get("lon", "").strip()
        dist_str = request.query.get("radius_nm", "").strip() or request.query.get("dist", "120").strip()

        center_lat = 32.1422
        center_lon = -111.1746
        radius_nm = 120.0
        min_lat, min_lon, max_lat, max_lon = None, None, None, None

        if bbox_str:
            try:
                parts = [float(x.strip()) for x in bbox_str.split(",")]
                if len(parts) == 4:
                    min_lat, min_lon, max_lat, max_lon = parts[0], parts[1], parts[2], parts[3]
                    center_lat = (min_lat + max_lat) / 2.0
                    center_lon = (min_lon + max_lon) / 2.0
                    lat_span_nm = abs(max_lat - min_lat) * 60.0
                    lon_span_nm = abs(max_lon - min_lon) * 60.0 * math.cos(math.radians(center_lat))
                    radius_nm = max(25.0, min(250.0, math.sqrt(lat_span_nm**2 + lon_span_nm**2) / 1.8))
            except Exception:
                pass
        else:
            if lat_str and lon_str:
                try:
                    center_lat = float(lat_str)
                    center_lon = float(lon_str)
                except ValueError:
                    pass
            else:
                home_coord = airport_db.get_coordinates(settings.HOME_ICAO)
                if home_coord:
                    center_lat, center_lon = home_coord

            try:
                radius_nm = min(250.0, max(15.0, float(dist_str)))
            except ValueError:
                radius_nm = 120.0

        cache_key = f"{round(center_lat, 2)}_{round(center_lon, 2)}_{int(radius_nm)}"
        if _adsb_cache["data"] and _adsb_cache["key"] == cache_key and (now - _adsb_cache["timestamp"]) < 4:
            return web.json_response(_adsb_cache["data"])

        aircraft_list = []
        provider_used = "adsb.lol"

        # 1. Query adsb.lol free API
        adsb_url = f"https://api.adsb.lol/v2/point/{center_lat:.4f}/{center_lon:.4f}/{int(radius_nm)}"
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": "PilotBrief-ADS-B/2.0"}) as session:
                async with session.get(adsb_url, timeout=aiohttp.ClientTimeout(total=4.5)) as resp:
                    if resp.status == 200:
                        raw_json = await resp.json()
                        raw_ac = raw_json.get("ac", [])
                        for ac in raw_ac:
                            ac_lat = ac.get("lat")
                            ac_lon = ac.get("lon")
                            if ac_lat is None or ac_lon is None:
                                continue

                            if min_lat is not None:
                                if not (min_lat - 0.1 <= ac_lat <= max_lat + 0.1 and min_lon - 0.1 <= ac_lon <= max_lon + 0.1):
                                    continue

                            alt_baro = ac.get("alt_baro")
                            alt_geom = ac.get("alt_geom")
                            is_ground = (alt_baro == "ground" or ac.get("ground") is True)
                            alt_ft = 0 if is_ground else (int(alt_baro) if isinstance(alt_baro, (int, float)) else (int(alt_geom) if isinstance(alt_geom, (int, float)) else None))

                            hex_code = str(ac.get("hex", "")).strip().upper()
                            flight = str(ac.get("flight") or "").strip()
                            reg = str(ac.get("r") or "").strip()
                            ac_type = str(ac.get("t") or "").strip()
                            gs = float(ac.get("gs") or 0)
                            track = float(ac.get("track") or ac.get("nav_heading") or ac.get("true_heading") or 0)
                            baro_rate = int(ac.get("baro_rate") or ac.get("geom_rate") or 0)
                            squawk = str(ac.get("squawk") or "")
                            category = str(ac.get("category") or "")

                            aircraft_list.append({
                                "hex": hex_code,
                                "callsign": flight or reg or hex_code,
                                "flight": flight,
                                "reg": reg,
                                "type": ac_type,
                                "lat": round(ac_lat, 5),
                                "lon": round(ac_lon, 5),
                                "alt_ft": alt_ft,
                                "on_ground": is_ground,
                                "gs_kts": round(gs, 1),
                                "track_deg": round(track, 1),
                                "vertical_rate_fpm": baro_rate,
                                "squawk": squawk,
                                "category": category,
                                "seen_sec": round(float(ac.get("seen") or 0), 1)
                            })
        except Exception as e:
            logger.debug(f"adsb.lol fetch failed, trying OpenSky fallback: {e}")

        # 2. Fallback: OpenSky Network if adsb.lol returned no planes
        if not aircraft_list:
            provider_used = "opensky-network"
            box_min_lat = (min_lat if min_lat is not None else center_lat - (radius_nm / 60.0))
            box_max_lat = (max_lat if max_lat is not None else center_lat + (radius_nm / 60.0))
            box_min_lon = (min_lon if min_lon is not None else center_lon - (radius_nm / 60.0))
            box_max_lon = (max_lon if max_lon is not None else center_lon + (radius_nm / 60.0))

            opensky_url = f"https://opensky-network.org/api/states/all?lamin={box_min_lat:.3f}&lomin={box_min_lon:.3f}&lamax={box_max_lat:.3f}&lomax={box_max_lon:.3f}"
            try:
                async with aiohttp.ClientSession(headers={"User-Agent": "PilotBrief-ADS-B/2.0"}) as session:
                    async with session.get(opensky_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            states = data.get("states") or []
                            for st in states:
                                if len(st) >= 12:
                                    hex_code = str(st[0] or "").upper()
                                    callsign = str(st[1] or "").strip()
                                    st_lon = st[5]
                                    st_lat = st[6]
                                    if st_lat is None or st_lon is None:
                                        continue
                                    baro_alt = st[7]
                                    on_gnd = bool(st[8])
                                    velocity_ms = st[9] or 0
                                    gs_kts = round(velocity_ms * 1.94384, 1)
                                    track = round(float(st[10] or 0), 1)
                                    vrate_ms = st[11] or 0
                                    vrate_fpm = round(vrate_ms * 196.85)

                                    alt_ft = int(baro_alt * 3.28084) if (baro_alt and not on_gnd) else (0 if on_gnd else None)

                                    aircraft_list.append({
                                        "hex": hex_code,
                                        "callsign": callsign or hex_code,
                                        "flight": callsign,
                                        "reg": "",
                                        "type": "",
                                        "lat": round(st_lat, 5),
                                        "lon": round(st_lon, 5),
                                        "alt_ft": alt_ft,
                                        "on_ground": on_gnd,
                                        "gs_kts": gs_kts,
                                        "track_deg": track,
                                        "vertical_rate_fpm": vrate_fpm,
                                        "squawk": str(st[14] or ""),
                                        "category": "",
                                        "seen_sec": 0
                                    })
            except Exception as e:
                logger.debug(f"OpenSky fallback error: {e}")

        result = {
            "count": len(aircraft_list),
            "timestamp": int(now * 1000),
            "provider": provider_used,
            "center": [round(center_lat, 4), round(center_lon, 4)],
            "radius_nm": round(radius_nm, 1),
            "aircraft": aircraft_list
        }

        _adsb_cache = {
            "data": result,
            "timestamp": now,
            "key": cache_key
        }

        return web.json_response(result)


def create_web_app() -> web.Application:
    app = web.Application()
    handlers = WebHandlers()

    # Request metrics, Bandwidth tracking, and CORS middleware
    @web.middleware
    async def metrics_and_cors_middleware(request, handler):
        global _REQUEST_COUNTERS
        _REQUEST_COUNTERS["total_requests"] += 1
        _REQUEST_COUNTERS["last_minute_requests"] += 1

        bytes_in = request.content_length or len(request.query_string.encode("utf-8", errors="ignore")) + len(request.path.encode("utf-8", errors="ignore"))
        bytes_out = 0

        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                resp = ex
            except Exception as e:
                logger.exception("Unhandled error in request")
                resp = web.json_response({"error": str(e)}, status=500)

        if hasattr(resp, "body") and resp.body is not None:
            bytes_out = len(resp.body)
        elif hasattr(resp, "content_length") and resp.content_length:
            bytes_out = resp.content_length
        else:
            bytes_out = 256

        _update_bandwidth(bytes_in, bytes_out)

        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    app.middlewares.append(metrics_and_cors_middleware)

    # Startup & Cleanup hooks for lightning worker
    async def on_startup(app_instance):
        lightning_service.start()

    async def on_cleanup(app_instance):
        lightning_service.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # API Routes
    app.router.add_get("/api/health", handlers.handle_health)
    app.router.add_get("/api/config", handlers.handle_config)
    app.router.add_get("/api/system/insights", handlers.handle_insights)
    app.router.add_get("/api/weather/metar", handlers.handle_metar)
    app.router.add_get("/api/weather/taf", handlers.handle_taf)
    app.router.add_get("/api/weather/regional-metars", handlers.handle_regional_metars)
    app.router.add_get("/api/weather/sigmets", handlers.handle_sigmets)
    app.router.add_get("/api/weather/radar-frames", handlers.handle_radar_frames)
    app.router.add_get("/api/weather/lightning", handlers.handle_lightning)
    app.router.add_get("/api/weather/lightning-stats", handlers.handle_lightning_stats)
    app.router.add_get("/api/traffic/adsb", handlers.handle_adsb_traffic)
    app.router.add_get("/api/airports/search", handlers.handle_airports_search)
    app.router.add_get("/api/airports/{icao}", handlers.handle_airport_details)
    app.router.add_get("/api/route", handlers.handle_route)
    app.router.add_get("/api/map/render", handlers.handle_map_render)

    # Static assets and index
    if STATIC_DIR.exists():
        app.router.add_static("/static", path=str(STATIC_DIR), name="static")
    app.router.add_get("/", handlers.handle_index)

    return app


async def run_web_server(host: str = "0.0.0.0", port: int = 8000) -> web.AppRunner:
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"PilotBrief Web Server listening on http://{host}:{port}")
    return runner

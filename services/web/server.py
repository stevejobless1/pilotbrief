import os
import io
import math
import time
import json
import logging
import asyncio
import aiohttp
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

# Cache for NOAA SIGMETs GeoJSON
_sigmets_cache = {
    "data": None,
    "timestamp": 0
}

_radar_frames_cache = {
    "data": None,
    "timestamp": 0
}

_metars_cache = {}


def _calculate_course_and_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, float]:
    """
    Calculates great-circle distance in NM and initial true course in degrees.
    """
    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)
    delta_phi = phi2 - phi1
    delta_lambda = lambda2 - lambda1

    # Haversine distance
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist_nm = c * 3440.065  # Earth radius in NM

    # Initial bearing
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
            "version": "2.0.0"
        })

    async def handle_config(self, request: web.Request) -> web.Response:
        return web.json_response({
            "home_icao": settings.HOME_ICAO,
            "default_intervals": settings.DEFAULT_ALERT_INTERVALS
        })

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
        bbox = request.query.get("bbox", "")
        if not bbox:
            # Default to NorCal bounding box around KPAO if not provided
            bbox = "36.0,-123.5,38.8,-121.0"

        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            min_lat, min_lon, max_lat, max_lon = parts[0], parts[1], parts[2], parts[3]
        except Exception:
            return web.json_response({"error": "Invalid bbox format. Expected: min_lat,min_lon,max_lat,max_lon"}, status=400)

        stations = await self.map_generator._fetch_regional_metars(min_lon, min_lat, max_lon, max_lat)
        
        # Enrich stations with flight category color and key metrics
        results = []
        for stn in stations:
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

                results.append({
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

        return web.json_response({"count": len(results), "stations": results})

    async def handle_sigmets(self, request: web.Request) -> web.Response:
        """
        Returns active NOAA SIGMET & AIRMET polygons as GeoJSON with hazard styling.
        """
        global _sigmets_cache
        now = time.time()
        if _sigmets_cache["data"] and (now - _sigmets_cache["timestamp"]) < 60:
            return web.json_response(_sigmets_cache["data"])

        url = "https://aviationweather.gov/api/data/airsigmet?format=geojson"
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": "PilotBrief-Web/1.0"}) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        geojson_data = await resp.json()
                        features = geojson_data.get("features", [])
                        
                        # Enrich each feature with color codes, hazard labels, and altitude top
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

        # Fallback if cached version exists or return empty FeatureCollection
        if _sigmets_cache["data"]:
            return web.json_response(_sigmets_cache["data"])
        return web.json_response({"type": "FeatureCollection", "features": []})

    async def handle_radar_frames(self, request: web.Request) -> web.Response:
        """
        Returns available radar frames for the past 2-3 hours for timeline replay.
        """
        global _radar_frames_cache
        now = time.time()
        if _radar_frames_cache["data"] and (now - _radar_frames_cache["timestamp"]) < 60:
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
                            
                            # Tile template for Leaflet
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

        # Parse query params
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
            max_results=3500
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
        # Search local airport_db
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

        # If not found locally and exactly 3 or 4 letters, try dynamic fetch
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

        # Try to get live METAR to compute runway crosswind components
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

            # Identify best runway (max headwind, minimum crosswind)
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
                
                # Midpoint calculation
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

        # Calculate Range Rings around departure (25, 50, 75, 100 NM)
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

        # Fetch sigmets if possible
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


def create_web_app() -> web.Application:
    app = web.Application()
    handlers = WebHandlers()

    # Enable CORS for all API responses
    @web.middleware
    async def cors_middleware(request, handler):
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

        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    app.middlewares.append(cors_middleware)

    # Lifecycle hooks to start/stop lightning service
    async def on_startup(app_instance):
        lightning_service.start()

    async def on_cleanup(app_instance):
        lightning_service.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # API Routes
    app.router.add_get("/api/health", handlers.handle_health)
    app.router.add_get("/api/config", handlers.handle_config)
    app.router.add_get("/api/weather/metar", handlers.handle_metar)
    app.router.add_get("/api/weather/taf", handlers.handle_taf)
    app.router.add_get("/api/weather/regional-metars", handlers.handle_regional_metars)
    app.router.add_get("/api/weather/sigmets", handlers.handle_sigmets)
    app.router.add_get("/api/weather/radar-frames", handlers.handle_radar_frames)
    app.router.add_get("/api/weather/lightning", handlers.handle_lightning)
    app.router.add_get("/api/weather/lightning-stats", handlers.handle_lightning_stats)
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

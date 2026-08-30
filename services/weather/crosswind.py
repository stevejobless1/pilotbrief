import json
import math
import urllib.request
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

AIRPORTS_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "airports.json"

class AirportDatabase:
    def __init__(self, file_path: Path = AIRPORTS_DATA_PATH):
        self.file_path = file_path
        self._airports: Dict[str, Any] = {}
        self.load()

    def load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8-sig") as f:
                    self._airports = json.load(f)
            except Exception as e:
                logger.error(f"Error loading airports.json: {e}")

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self._airports, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving airports.json: {e}")

    def fetch_dynamic_airport(self, icao: str) -> Optional[Dict[str, Any]]:
        """
        Dynamically fetches airport coordinates, elevation, and runway geometries
        from NOAA AWC API for any US or international airport not in local cache.
        """
        icao = icao.strip().upper()
        url = f"https://aviationweather.gov/api/data/airport?ids={icao}&format=json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PilotBrief/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data and isinstance(data, list) and len(data) > 0:
                    apt = data[0]
                    runways = []
                    for r in apt.get("runways", []):
                        r_id = r.get("id", "")
                        align = r.get("alignment")
                        dim = r.get("dimension", "")
                        length_ft = 3000
                        width_ft = 75
                        if "x" in str(dim):
                            parts = str(dim).split("x")
                            if parts[0].isdigit():
                                length_ft = int(parts[0])
                            if len(parts) > 1 and parts[1].isdigit():
                                width_ft = int(parts[1])

                        if "/" in r_id and align is not None:
                            id1, id2 = r_id.split("/")
                            try:
                                h1 = int(align)
                                h2 = (h1 + 180) % 360
                                runways.append({"id": id1, "heading": h1, "length_ft": length_ft, "width_ft": width_ft})
                                runways.append({"id": id2, "heading": h2, "length_ft": length_ft, "width_ft": width_ft})
                            except Exception:
                                pass
                        elif align is not None:
                            try:
                                runways.append({"id": r_id, "heading": int(align), "length_ft": length_ft, "width_ft": width_ft})
                            except Exception:
                                pass

                    entry = {
                        "name": apt.get("name", "").strip(),
                        "city": apt.get("state", ""),
                        "state": apt.get("state", ""),
                        "lat": float(apt["lat"]),
                        "lon": float(apt["lon"]),
                        "elevation_ft": float(apt.get("elev", 0)),
                        "runways": runways
                    }
                    self._airports[icao] = entry
                    logger.info(f"Dynamically cached airport metadata for {icao}")
                    return entry
        except Exception as e:
            logger.warning(f"Could not fetch dynamic airport for {icao}: {e}")

        # Fallback to METAR endpoint to at least obtain lat/lon/elev
        try:
            metar_url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json"
            req = urllib.request.Request(metar_url, headers={"User-Agent": "PilotBrief/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data and isinstance(data, list) and len(data) > 0:
                    mob = data[0]
                    entry = {
                        "name": mob.get("name", icao),
                        "city": "",
                        "state": "",
                        "lat": float(mob["lat"]),
                        "lon": float(mob["lon"]),
                        "elevation_ft": float(mob.get("elev", 0)),
                        "runways": []
                    }
                    self._airports[icao] = entry
                    return entry
        except Exception as e:
            logger.warning(f"Could not fetch METAR fallback coords for {icao}: {e}")

        return None

    def get_airport(self, icao: str) -> Optional[Dict[str, Any]]:
        icao = icao.strip().upper()
        if icao in self._airports:
            return self._airports[icao]
        return self.fetch_dynamic_airport(icao)

    def get_runways(self, icao: str) -> List[Dict[str, Any]]:
        apt = self.get_airport(icao)
        if apt:
            return apt.get("runways", [])
        return []

    def get_coordinates(self, icao: str) -> Optional[Tuple[float, float]]:
        apt = self.get_airport(icao)
        if apt and "lat" in apt and "lon" in apt:
            try:
                return (float(apt["lat"]), float(apt["lon"]))
            except Exception:
                pass
        return None

    def get_elevation(self, icao: str) -> float:
        apt = self.get_airport(icao)
        if apt and "elevation_ft" in apt:
            try:
                return float(apt["elevation_ft"])
            except Exception:
                pass
        return 0.0

airport_db = AirportDatabase()

class CrosswindCalculator:
    @staticmethod
    def calculate_components(
        runway_heading: Union[float, int, str],
        wind_dir: Optional[Union[float, int, str]],
        wind_speed: Union[float, int, str],
        wind_gust: Optional[Union[float, int, str]] = None
    ) -> Dict[str, Any]:
        """
        Calculates headwind/tailwind and crosswind components for a specific runway.
        Safely coerces strings and handles VRB winds.
        """
        try:
            rwy_hdg = float(runway_heading)
        except (ValueError, TypeError):
            rwy_hdg = 0.0

        try:
            w_spd = float(wind_speed) if wind_speed is not None else 0.0
        except (ValueError, TypeError):
            w_spd = 0.0

        w_gust = None
        if wind_gust is not None:
            try:
                w_gust = float(wind_gust)
            except (ValueError, TypeError):
                w_gust = None

        # If wind direction is None, 'VRB', or non-numeric
        w_dir = None
        if wind_dir is not None:
            try:
                w_dir = float(wind_dir)
            except (ValueError, TypeError):
                w_dir = None

        if w_dir is None or w_spd == 0:
            return {
                "headwind": 0.0,
                "tailwind": 0.0,
                "crosswind": 0.0,
                "crosswind_gust": 0.0,
                "crosswind_side": "Variable" if wind_dir == "VRB" else "None",
                "angle_diff": 0.0,
                "is_favorable": True
            }

        # Angle between wind and runway heading in degrees
        angle_diff = (w_dir - rwy_hdg + 180) % 360 - 180
        rad = math.radians(angle_diff)

        # Headwind is positive along runway direction (cos)
        along_runway = w_spd * math.cos(rad)
        headwind = max(0.0, along_runway)
        tailwind = max(0.0, -along_runway)

        # Crosswind is perpendicular to runway (sin)
        cross_component = w_spd * math.sin(rad)
        crosswind_mag = abs(cross_component)
        crosswind_side = "Left" if cross_component < -0.5 else ("Right" if cross_component > 0.5 else "Direct")

        crosswind_gust_mag = 0.0
        if w_gust and w_gust > w_spd:
            crosswind_gust_mag = abs(w_gust * math.sin(rad))

        return {
            "headwind": round(headwind, 1),
            "tailwind": round(tailwind, 1),
            "crosswind": round(crosswind_mag, 1),
            "crosswind_gust": round(crosswind_gust_mag, 1) if crosswind_gust_mag > 0 else None,
            "crosswind_side": crosswind_side,
            "angle_diff": round(abs(angle_diff), 1)
        }

    @classmethod
    def evaluate_airport_runways(
        cls,
        icao: str,
        wind_dir: Optional[Union[float, int, str]],
        wind_speed: Union[float, int, str],
        wind_gust: Optional[Union[float, int, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Evaluates all runways for an airport and ranks them from most favorable to least favorable.
        """
        runways = airport_db.get_runways(icao)
        if not runways:
            return []

        results = []
        for rwy in runways:
            rwy_id = rwy.get("id", "??")
            heading = rwy.get("heading", 0.0)
            length = rwy.get("length_ft", 0)
            comps = cls.calculate_components(heading, wind_dir, wind_speed, wind_gust)
            results.append({
                "runway_id": rwy_id,
                "heading": float(heading) if isinstance(heading, (int, float)) else 0.0,
                "length_ft": length,
                **comps
            })

        # Sort: Highest headwind first, lowest crosswind second
        results.sort(key=lambda x: (-x["headwind"] + x["tailwind"] * 2, x["crosswind"]))
        return results

import json
import math
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

AIRPORTS_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "airports.json"

class AirportDatabase:
    def __init__(self, file_path: Path = AIRPORTS_DATA_PATH):
        self.file_path = file_path
        self._airports: Dict[str, Any] = {}
        self.load()

    def load(self):
        if self.file_path.exists():
            with open(self.file_path, "r", encoding="utf-8-sig") as f:
                self._airports = json.load(f)

    def get_airport(self, icao: str) -> Optional[Dict[str, Any]]:
        return self._airports.get(icao.strip().upper())

    def get_runways(self, icao: str) -> List[Dict[str, Any]]:
        apt = self.get_airport(icao)
        if apt:
            return apt.get("runways", [])
        return []

    def get_coordinates(self, icao: str) -> Optional[Tuple[float, float]]:
        apt = self.get_airport(icao)
        if apt and "lat" in apt and "lon" in apt:
            return (float(apt["lat"]), float(apt["lon"]))
        return None

    def get_elevation(self, icao: str) -> float:
        apt = self.get_airport(icao)
        if apt and "elevation_ft" in apt:
            return float(apt["elevation_ft"])
        return 0.0

airport_db = AirportDatabase()

class CrosswindCalculator:
    @staticmethod
    def calculate_components(
        runway_heading: float,
        wind_dir: Optional[float],
        wind_speed: float,
        wind_gust: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculates headwind/tailwind and crosswind components for a specific runway.
        """
        if wind_dir is None or wind_speed == 0:
            return {
                "headwind": 0.0,
                "tailwind": 0.0,
                "crosswind": 0.0,
                "crosswind_gust": 0.0,
                "crosswind_side": "None",
                "is_favorable": True
            }

        # Angle between wind and runway heading in degrees
        angle_diff = (wind_dir - runway_heading + 180) % 360 - 180
        rad = math.radians(angle_diff)

        # Headwind is positive along runway direction (cos)
        along_runway = wind_speed * math.cos(rad)
        headwind = max(0.0, along_runway)
        tailwind = max(0.0, -along_runway)

        # Crosswind is perpendicular to runway (sin)
        cross_component = wind_speed * math.sin(rad)
        crosswind_mag = abs(cross_component)
        crosswind_side = "Left" if cross_component < -0.5 else ("Right" if cross_component > 0.5 else "Direct")

        crosswind_gust_mag = 0.0
        if wind_gust and wind_gust > wind_speed:
            crosswind_gust_mag = abs(wind_gust * math.sin(rad))

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
        wind_dir: Optional[float],
        wind_speed: float,
        wind_gust: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Evaluates all runways for an airport and ranks them from most favorable to least favorable.
        """
        runways = airport_db.get_runways(icao)
        if not runways:
            return []

        results = []
        for rwy in runways:
            rwy_id = rwy["id"]
            heading = float(rwy["heading"])
            length = rwy.get("length_ft", 0)
            comps = cls.calculate_components(heading, wind_dir, wind_speed, wind_gust)
            results.append({
                "runway_id": rwy_id,
                "heading": heading,
                "length_ft": length,
                **comps
            })

        # Sort: Highest headwind first, lowest crosswind second
        results.sort(key=lambda x: (-x["headwind"] + x["tailwind"] * 2, x["crosswind"]))
        return results

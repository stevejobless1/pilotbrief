import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import matplotlib.path as mpath
from services.weather.crosswind import airport_db

logger = logging.getLogger(__name__)

class SigmetMonitor:
    @staticmethod
    def point_in_polygon(lon: float, lat: float, poly_pts: List[List[float]]) -> bool:
        """
        Checks if a longitude/latitude coordinate falls inside a polygon.
        """
        try:
            path = mpath.Path(poly_pts)
            return path.contains_point((lon, lat))
        except Exception as e:
            logger.error(f"Error checking point in polygon: {e}")
            return False

    @classmethod
    def min_distance_to_polygon_nm(cls, lon: float, lat: float, poly_pts: List[List[float]]) -> float:
        """
        Calculates the minimum distance from a coordinate to a polygon in Nautical Miles.
        Returns 0.0 if the point is strictly inside.
        """
        if cls.point_in_polygon(lon, lat, poly_pts):
            return 0.0

        min_dist = 999999.0
        for p in poly_pts:
            p_lon, p_lat = p[0], p[1]
            dlat = (p_lat - lat) * 60.0
            dlon = (p_lon - lon) * 60.0 * math.cos(math.radians((lat + p_lat) / 2.0))
            dist = math.sqrt(dlat * dlat + dlon * dlon)
            if dist < min_dist:
                min_dist = dist
        return round(min_dist, 1)

    @classmethod
    def evaluate_airport_convective_hazards(
        cls,
        icao: str,
        sigmets: List[Dict[str, Any]],
        proximity_nm: float = 25.0
    ) -> List[Dict[str, Any]]:
        """
        Evaluates active NOAA SIGMETs for Convective/Thunderstorm hazards
        directly over or within `proximity_nm` of the given airport.
        """
        coord = airport_db.get_coordinates(icao)
        if not coord:
            return []

        lat, lon = coord
        matching_hazards = []

        for s in sigmets:
            props = s.get("properties", {})
            geom = s.get("geometry", {})
            hazard = str(props.get("hazard", "")).upper()
            sig_type = str(props.get("airSigmetType", "")).upper()
            alpha_char = str(props.get("alphaChar", "")).upper()
            raw_text = props.get("rawAirSigmet") or props.get("rawText", "")
            sig_id = str(props.get("airSigmetId") or props.get("seriesId") or props.get("receiptTime") or raw_text[:30]).strip()

            # Identify if this is a Convective SIGMET or severe thunderstorm hazard
            is_convective = (
                "CONVECTIVE" in hazard or
                "TS" in hazard or
                "THUNDERSTORM" in hazard or
                "C" in alpha_char or
                "CONVECTIVE" in raw_text.upper() or
                "LINE TS" in raw_text.upper() or
                "SEV TS" in raw_text.upper()
            )

            if not is_convective:
                continue

            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])

            if gtype == "Polygon" and coords:
                poly_pts = coords[0]
                dist_nm = cls.min_distance_to_polygon_nm(lon, lat, poly_pts)

                if dist_nm <= proximity_nm:
                    is_overhead = (dist_nm == 0.0)
                    top_alt = props.get("altitudeHi1") or props.get("top")
                    val_from = props.get("validTimeFrom")
                    val_to = props.get("validTimeTo")

                    matching_hazards.append({
                        "sigmet_id": sig_id or f"CONV-{int(lat*100)}-{int(lon*100)}",
                        "icao": icao.upper(),
                        "hazard": "CONVECTIVE SIGMET",
                        "is_overhead": is_overhead,
                        "distance_nm": dist_nm,
                        "top_altitude_ft": top_alt,
                        "top_fl": f"FL{int(top_alt/100)}" if top_alt else "Unknown",
                        "valid_from": val_from,
                        "valid_to": val_to,
                        "raw_text": raw_text,
                        "feature": s
                    })

        return matching_hazards

sigmet_monitor = SigmetMonitor()

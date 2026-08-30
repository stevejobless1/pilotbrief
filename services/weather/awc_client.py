import aiohttp
import asyncio
import logging
import math
from typing import Dict, Any, List, Optional, Tuple
from config.settings import settings
from services.weather.crosswind import airport_db

logger = logging.getLogger(__name__)

class AWCClient:
    def __init__(self, base_url: str = settings.AWC_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._headers = {"User-Agent": "PilotBrief-DiscordBot/1.0 (Aviation Tool)"}

    async def get_metar(self, icao: str) -> Optional[Dict[str, Any]]:
        """Fetch METAR for an airport ICAO code in JSON format."""
        icao = icao.strip().upper()
        url = f"{self.base_url}/metar?ids={icao}&format=json"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            return data[0]
                    else:
                        logger.warning(f"Failed to fetch METAR for {icao}: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching METAR for {icao}: {e}")
        return None

    async def get_taf(self, icao: str) -> Optional[Dict[str, Any]]:
        """Fetch direct TAF for an airport ICAO code."""
        icao = icao.strip().upper()
        url = f"{self.base_url}/taf?ids={icao}&format=json"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            return data[0]
        except Exception as e:
            logger.error(f"Error fetching TAF for {icao}: {e}")
        return None

    async def get_best_taf(self, icao: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[float]]:
        """
        Fetches TAF for airport. If the airport does not publish a TAF,
        dynamically searches for the closest reporting TAF station within 50nm using NOAA bbox.
        Returns: (taf_data, station_used, distance_nm)
        """
        direct_taf = await self.get_taf(icao)
        if direct_taf:
            return direct_taf, icao.upper(), 0.0

        # Retrieve coordinates
        coord = airport_db.get_coordinates(icao)
        if not coord:
            metar = await self.get_metar(icao)
            if metar and "lat" in metar and "lon" in metar:
                coord = (float(metar["lat"]), float(metar["lon"]))

        if not coord:
            return None, None, None

        lat0, lon0 = coord
        pad = 0.85
        min_lat = lat0 - pad
        min_lon = lon0 - pad
        max_lat = lat0 + pad
        max_lon = lon0 + pad
        
        url = f"{self.base_url}/taf?bbox={min_lat:.2f},{min_lon:.2f},{max_lat:.2f},{max_lon:.2f}&format=json"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        tafs = await resp.json()
                        if tafs and isinstance(tafs, list):
                            candidates = []
                            for t in tafs:
                                t_icao = t.get("icaoId", "").upper()
                                t_lat = float(t.get("lat", 0))
                                t_lon = float(t.get("lon", 0))
                                dlat = (t_lat - lat0) * 60.0
                                dlon = (t_lon - lon0) * 60.0 * math.cos(math.radians((lat0 + t_lat) / 2.0))
                                dist = math.sqrt(dlat * dlat + dlon * dlon)
                                candidates.append((dist, t_icao, t))

                            candidates.sort(key=lambda x: x[0])
                            if candidates:
                                best_dist, best_icao, best_data = candidates[0]
                                logger.info(f"Using nearby TAF {best_icao} ({best_dist:.1f}nm) for {icao}")
                                return best_data, best_icao, round(best_dist, 1)
        except Exception as e:
            logger.error(f"Error querying regional TAFs for {icao}: {e}")

        return None, None, None

    async def get_sigmets(self) -> List[Dict[str, Any]]:
        """Fetch active SIGMETs & AIRMETs in GeoJSON format."""
        url = f"{self.base_url}/airsigmet?format=geojson"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and "features" in data:
                            return data["features"]
        except Exception as e:
            logger.error(f"Error fetching SIGMETs: {e}")
        return []

awc_client = AWCClient()

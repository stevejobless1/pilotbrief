import aiohttp
import asyncio
import logging
import math
from typing import Dict, Any, List, Optional, Tuple
from config.settings import settings
from services.weather.crosswind import airport_db

logger = logging.getLogger(__name__)

# List of major reporting TAF stations used for fallback search
COMMON_TAF_STATIONS = [
    "KSFO", "KSJC", "KOAK", "KCCR", "KSMF", "KMRY", "KSTS", "KAPC", "KSCK", "KFAT",
    "KLAX", "KSNA", "KVNY", "KBUR", "KLGB", "KONT", "KSAN", "KCRQ", "KMYF", "KPSP",
    "KSEA", "KBFI", "KPAE", "KPDX", "KORD", "KMDW", "KJFK", "KLGA", "KEWR", "KBOS",
    "KIAD", "KDCA", "KBWI", "KATL", "KMIA", "KMCO", "KTPA", "KDFW", "KDAL", "KAUS",
    "KSAT", "KIAH", "KHOU", "KDEN", "KPHX", "KLAS", "KSLC", "KMCI", "KMSP", "KDTW"
]

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
        searches for the closest reporting TAF station within 45nm.
        Returns: (taf_data, station_used, distance_nm)
        """
        direct_taf = await self.get_taf(icao)
        if direct_taf:
            return direct_taf, icao.upper(), 0.0

        # Search nearest TAF station
        origin_coord = airport_db.get_coordinates(icao)
        if not origin_coord:
            return None, None, None

        lat0, lon0 = origin_coord
        candidates = []
        for cand in COMMON_TAF_STATIONS:
            if cand == icao.upper():
                continue
            cand_coord = airport_db.get_coordinates(cand)
            if cand_coord:
                lat1, lon1 = cand_coord
                # Calculate distance in NM
                dlat = (lat1 - lat0) * 60.0
                dlon = (lon1 - lon0) * 60.0 * math.cos(math.radians((lat0 + lat1) / 2.0))
                dist = math.sqrt(dlat * dlat + dlon * dlon)
                if dist <= 45.0:
                    candidates.append((dist, cand))

        candidates.sort()
        for dist, cand_icao in candidates[:3]:
            cand_taf = await self.get_taf(cand_icao)
            if cand_taf:
                logger.info(f"Using nearby TAF {cand_icao} ({dist:.1f}nm) for {icao}")
                return cand_taf, cand_icao, round(dist, 1)

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

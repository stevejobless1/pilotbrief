import aiohttp
import asyncio
import logging
from typing import Dict, Any, List, Optional
from config.settings import settings

logger = logging.getLogger(__name__)

class AWCClient:
    def __init__(self, base_url: str = settings.AWC_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._headers = {"User-Agent": "PilotBrief-DiscordBot/1.0 (Aviation Student Tool)"}

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
        """Fetch TAF for an airport ICAO code in JSON format."""
        icao = icao.strip().upper()
        url = f"{self.base_url}/taf?ids={icao}&format=json"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            return data[0]
                    else:
                        logger.warning(f"Failed to fetch TAF for {icao}: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching TAF for {icao}: {e}")
        return None

    async def get_sigmets(self) -> List[Dict[str, Any]]:
        """Fetch active SIGMETs in GeoJSON format."""
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

    async def get_pireps(self, lat: float, lon: float, distance_nm: int = 60) -> List[Dict[str, Any]]:
        """Fetch recent PIREPs within distance around coordinate."""
        url = f"{self.base_url}/pirep?format=geojson&bbox={lon-1},{lat-1},{lon+1},{lat+1}"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and "features" in data:
                            return data["features"]
        except Exception as e:
            logger.error(f"Error fetching PIREPs: {e}")
        return []

awc_client = AWCClient()

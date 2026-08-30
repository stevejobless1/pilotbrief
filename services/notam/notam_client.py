import aiohttp
import logging
from typing import Dict, Any, List, Optional
import re

logger = logging.getLogger(__name__)

class NOTAMClient:
    """
    Fetches and filters NOTAMs for general aviation and student pilots,
    prioritizing safety-critical items (runway closures, TFRs, NAVAID outages)
    over low-priority noise (distant unlit crane notices).
    """
    def __init__(self):
        self._headers = {"User-Agent": "PilotBrief-DiscordBot/1.0"}

    async def get_notams_for_station(self, icao: str) -> List[Dict[str, Any]]:
        """
        Fetches NOTAMs for an ICAO station using the FAA/AWC NOTAM feeds.
        """
        icao = icao.strip().upper()
        # Remove 'K' for 3-letter domestic identifiers if querying specific FAA endpoints, but standard ICAO is accepted
        url = f"https://aviationweather.gov/api/data/notam?ids={icao}&format=json"
        
        notams_list = []
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list):
                            notams_list = data
        except Exception as e:
            logger.error(f"Error fetching NOTAMs for {icao}: {e}")

        # Classify and filter notable NOTAMs
        return self._classify_notams(notams_list)

    def _classify_notams(self, raw_notams: List[Any]) -> List[Dict[str, Any]]:
        classified = []
        for item in raw_notams:
            text = ""
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("rawText") or item.get("text") or str(item)

            if not text:
                continue

            text_upper = text.upper()
            
            # Severity / Importance assessment
            priority = "NORMAL"
            category = "GENERAL"

            if any(k in text_upper for k in ["RWY CLSD", "RUNWAY CLOSED", "RWY CLOSED", "CLSD"]):
                priority = "CRITICAL"
                category = "RUNWAY CLOSURE"
            elif any(k in text_upper for k in ["TFR", "TEMPORARY FLIGHT RESTRICTION", "RESTRICTED"]):
                priority = "CRITICAL"
                category = "AIRSPACE / TFR"
            elif any(k in text_upper for k in ["ILS OTS", "PAPI OTS", "VASI OTS", "VOR OTS", "UNSERVICEABLE", "OUT OF SERVICE"]):
                priority = "HIGH"
                category = "NAVAID / LIGHTING"
            elif any(k in text_upper for k in ["TWY CLSD", "TAXIWAY CLOSED"]):
                priority = "MEDIUM"
                category = "TAXIWAY"
            elif any(k in text_upper for k in ["OBST", "CRANE", "TOWER", "UNLIT"]):
                priority = "LOW"
                category = "OBSTACLE"

            classified.append({
                "text": text,
                "priority": priority,
                "category": category
            })

        # Sort: CRITICAL -> HIGH -> MEDIUM -> NORMAL -> LOW
        priority_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "NORMAL": 3, "LOW": 4}
        classified.sort(key=lambda x: priority_rank.get(x["priority"], 3))
        return classified

notam_client = NOTAMClient()

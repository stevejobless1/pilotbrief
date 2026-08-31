import re
from typing import Dict, Any, Optional, Tuple, List

# Common flight keywords to detect aviation events
FLIGHT_KEYWORDS = [
    "flight", "lesson", "solo", "dual", "xc", "cross country",
    "c172", "c-172", "skyhawk", "pa28", "pa-28", "archer", "cherokee",
    "da40", "da20", "cessna", "piper", "cirrus", "sr20", "sr22",
    "stage check", "checkride", "mock oral", "ground + flight", "pattern work"
]

# Regex for ICAO codes (e.g. KPAO, KSFO, 0O2, C83, or 4-letter standard)
ICAO_PATTERN = re.compile(r'\b([Kk][A-Za-z0-9]{3}|[0-9][A-Za-z0-9]{2}|[A-Za-z][0-9]{2})\b')

class FlightEventExtractor:
    @staticmethod
    def is_flight_event(summary: str, description: str = "") -> bool:
        """Determines if a calendar event is an aviation flight event."""
        text = f"{summary} {description}".lower()
        return any(kw in text for kw in FLIGHT_KEYWORDS)

    @classmethod
    def extract_airports(cls, summary: str, description: str = "", default_home: str = "KRYN") -> Tuple[str, Optional[str]]:
        """
        Extracts departure and destination ICAO codes from event title/notes.
        Examples:
          "Flight Lesson KPAO to KSTS" -> ("KPAO", "KSTS")
          "Solo Pattern Practice @ KSQL" -> ("KSQL", None)
          "Dual XC KPAO - KMRY - KPAO" -> ("KPAO", "KMRY")
          "Flight Lesson with Bob" -> ("KPAO", None) [uses default_home]
        """
        combined = f"{summary} {description}"
        matches = [m.upper() for m in ICAO_PATTERN.findall(combined)]
        
        # Filter out common false positives like "WITH", "FROM", "TIME", "SOLO", "DUAL"
        false_positives = {"WITH", "FROM", "TIME", "SOLO", "DUAL", "LESS", "HOME", "DATE", "HOUR", "POST"}
        valid_matches = [m for m in matches if m not in false_positives]

        # Look for explicit "A to B" or "A -> B" or "A - B" patterns
        route_match = re.search(r'([Kk][A-Za-z0-9]{3})\s*(?:to|->|-|>)\s*([Kk][A-Za-z0-9]{3})', combined, re.IGNORECASE)
        if route_match:
            dep = route_match.group(1).upper()
            dest = route_match.group(2).upper()
            return dep, (dest if dest != dep else None)

        if len(valid_matches) >= 2:
            dep = valid_matches[0]
            dest = valid_matches[1]
            return dep, (dest if dest != dep else None)
        elif len(valid_matches) == 1:
            return valid_matches[0], None
        else:
            return default_home.upper(), None

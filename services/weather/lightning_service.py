import asyncio
import aiohttp
import json
import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from collections import deque

logger = logging.getLogger("PilotBrief.Lightning")


def decompress_lzw(b: str) -> str:
    """
    Decompresses Blitzortung LZW-encoded string.
    """
    if not b:
        return ""
    dictionary = {}
    f = list(b)
    g = f[0]
    h = [g]
    j = 256
    for a in range(1, len(f)):
        c = ord(f[a])
        if c < 256:
            d = f[a]
        elif c in dictionary:
            d = dictionary[c]
        else:
            d = g + g[0]
        h.append(d)
        dictionary[j] = g + d[0]
        j += 1
        g = d
    return "".join(h)


class LightningStrike:
    __slots__ = ("time_ms", "lat", "lon", "alt", "pol", "mds")

    def __init__(self, time_ms: int, lat: float, lon: float, alt: int = 0, pol: int = 0, mds: int = 0):
        self.time_ms = time_ms
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.pol = pol
        self.mds = mds

    def to_dict(self, now_ms: Optional[int] = None) -> Dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        age_sec = max(0, int((now_ms - self.time_ms) / 1000))
        return {
            "time_ms": self.time_ms,
            "lat": self.lat,
            "lon": self.lon,
            "alt": self.alt,
            "pol": self.pol,
            "mds": self.mds,
            "age_sec": age_sec
        }


class LightningService:
    def __init__(self, max_history_hours: float = 3.0):
        self.max_history_ms = int(max_history_hours * 3600 * 1000)
        # Store strikes in a time-ordered deque
        self._strikes: deque = deque()
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._ws_hosts = [
            "wss://ws7.blitzortung.org",
            "wss://ws8.blitzortung.org",
            "wss://ws1.blitzortung.org:8070"
        ]
        self._strike_count_last_minute = 0
        self._last_minute_ts = time.time()

    def add_strike(self, time_ms: int, lat: float, lon: float, alt: int = 0, pol: int = 0, mds: int = 0):
        strike = LightningStrike(time_ms, lat, lon, alt, pol, mds)
        self._strikes.append(strike)
        self._strike_count_last_minute += 1

        # Prune old strikes older than max_history_ms
        cutoff = int(time.time() * 1000) - self.max_history_ms
        while self._strikes and self._strikes[0].time_ms < cutoff:
            self._strikes.popleft()

    def get_strikes(
        self,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        max_results: int = 4000
    ) -> List[Dict[str, Any]]:
        """
        Retrieves strikes within given time bounds [since_ms, until_ms] and bbox [min_lat, min_lon, max_lat, max_lon].
        """
        now_ms = int(time.time() * 1000)
        if since_ms is None:
            # Default to last 3 hours
            since_ms = now_ms - self.max_history_ms
        if until_ms is None:
            until_ms = now_ms

        results = []
        # Filter strikes
        for s in reversed(self._strikes):
            if s.time_ms < since_ms or s.time_ms > until_ms:
                continue

            if bbox is not None:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= s.lat <= max_lat and min_lon <= s.lon <= max_lon):
                    continue

            results.append(s.to_dict(now_ms=now_ms))
            if len(results) >= max_results:
                break

        return results

    def get_stats(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._last_minute_ts >= 60:
            self._strike_count_last_minute = 0
            self._last_minute_ts = now

        return {
            "total_buffered": len(self._strikes),
            "rate_per_min": self._strike_count_last_minute,
            "connected": self._is_running
        }

    async def _worker_loop(self):
        host_idx = 0
        while self._is_running:
            host = self._ws_hosts[host_idx % len(self._ws_hosts)]
            host_idx += 1
            try:
                logger.info(f"Connecting to live lightning stream: {host}")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(host, timeout=aiohttp.ClientTimeout(total=8)) as ws:
                        await ws.send_json({"a": 111})
                        logger.info(f"Subscribed to lightning feed at {host}")

                        while self._is_running:
                            msg = await asyncio.wait_for(ws.receive(), timeout=30)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                raw_str = msg.data
                                try:
                                    decompressed = decompress_lzw(raw_str)
                                    data = json.loads(decompressed)
                                    
                                    # Parse strike timestamp
                                    # Blitzortung provides nanoseconds or timestamp
                                    t_val = data.get("time")
                                    if t_val:
                                        t_ms = int(t_val / 1_000_000) if t_val > 10_000_000_000_000 else int(t_val)
                                    else:
                                        t_ms = int(time.time() * 1000)

                                    lat = float(data.get("lat", 0))
                                    lon = float(data.get("lon", 0))
                                    alt = int(data.get("alt", 0))
                                    pol = int(data.get("pol", 0))
                                    mds = int(data.get("mds", 0))

                                    if lat != 0 and lon != 0:
                                        self.add_strike(t_ms, lat, lon, alt, pol, mds)
                                except Exception:
                                    continue
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Lightning stream reconnecting ({e})...")
                await asyncio.sleep(4)

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._worker_loop())
            logger.info("LightningService background ingest worker started.")

    def stop(self):
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("LightningService stopped.")


lightning_service = LightningService(max_history_hours=3.0)

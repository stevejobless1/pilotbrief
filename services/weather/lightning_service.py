import asyncio
import aiohttp
import json
import logging
import time
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from collections import deque

logger = logging.getLogger("PilotBrief.Lightning")

HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "lightning_history.json"


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
        self.time_ms = int(time_ms)
        self.lat = round(float(lat), 4)
        self.lon = round(float(lon), 4)
        self.alt = int(alt)
        self.pol = int(pol)
        self.mds = int(mds)

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
    def __init__(self, max_history_hours: float = 3.0, load_persisted: bool = False):
        self.max_history_ms = int(max_history_hours * 3600 * 1000)
        self._strikes: deque = deque()
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._save_task: Optional[asyncio.Task] = None
        self._ws_hosts = [
            "wss://ws7.blitzortung.org",
            "wss://ws8.blitzortung.org",
            "wss://ws1.blitzortung.org",
            "wss://ws2.blitzortung.org"
        ]
        self._active_host = None
        self._total_ingested = 0
        self._strike_count_last_minute = 0
        self._last_minute_ts = time.time()
        self._last_strike_ts = 0
        if load_persisted:
            self._load_persisted_history()

    def clear(self):
        """Clears all buffered strikes."""
        self._strikes.clear()
        self._total_ingested = 0
        self._strike_count_last_minute = 0

    def _load_persisted_history(self):
        """Loads strike history from disk cache to survive restarts."""
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cutoff = int(time.time() * 1000) - self.max_history_ms
                loaded = 0
                for item in data:
                    t_ms = item.get("time_ms", 0)
                    if t_ms >= cutoff:
                        self._strikes.append(LightningStrike(
                            t_ms,
                            item.get("lat", 0),
                            item.get("lon", 0),
                            item.get("alt", 0),
                            item.get("pol", 0),
                            item.get("mds", 0)
                        ))
                        loaded += 1
                logger.info(f"Loaded {loaded} historical lightning strikes from persistent cache.")
        except Exception as e:
            logger.warning(f"Could not load lightning cache: {e}")

    def save_history(self):
        """Saves active strikes to disk cache."""
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            cutoff = int(time.time() * 1000) - self.max_history_ms
            to_save = [s.to_dict() for s in self._strikes if s.time_ms >= cutoff]
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(to_save, f)
        except Exception as e:
            logger.warning(f"Could not save lightning cache: {e}")

    def add_strike(self, time_ms: int, lat: float, lon: float, alt: int = 0, pol: int = 0, mds: int = 0):
        strike = LightningStrike(time_ms, lat, lon, alt, pol, mds)
        self._strikes.append(strike)
        self._total_ingested += 1
        self._strike_count_last_minute += 1
        self._last_strike_ts = time_ms

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
            since_ms = now_ms - self.max_history_ms
        if until_ms is None:
            until_ms = now_ms

        results = []
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
            "total_ingested": self._total_ingested,
            "rate_per_min": self._strike_count_last_minute,
            "connected": self._is_running and (self._active_host is not None),
            "active_host": self._active_host,
            "last_strike_ts": self._last_strike_ts,
            "history_window_hours": self.max_history_ms / (3600 * 1000)
        }

    async def _periodic_save_loop(self):
        while self._is_running:
            await asyncio.sleep(120)
            self.save_history()

    async def _worker_loop(self):
        host_idx = 0
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        while self._is_running:
            host = self._ws_hosts[host_idx % len(self._ws_hosts)]
            host_idx += 1
            self._active_host = None
            try:
                logger.info(f"Connecting to live lightning stream: {host}")
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(host, timeout=aiohttp.ClientTimeout(total=8)) as ws:
                        await ws.send_json({"a": 111})
                        self._active_host = host
                        logger.info(f"Subscribed to lightning feed at {host}")

                        while self._is_running:
                            msg = await asyncio.wait_for(ws.receive(), timeout=25)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                raw_str = msg.data
                                try:
                                    decompressed = decompress_lzw(raw_str)
                                    data = json.loads(decompressed)

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
                logger.warning(f"Lightning stream reconnecting from {host} ({e})...")
                self._active_host = None
                await asyncio.sleep(3)

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._worker_loop())
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("LightningService background ingest worker started.")

    def stop(self):
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        self.save_history()
        logger.info("LightningService stopped.")


lightning_service = LightningService(max_history_hours=3.0, load_persisted=True)

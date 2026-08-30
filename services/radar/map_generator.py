import io
import math
import logging
import asyncio
import aiohttp
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon, Circle
import numpy as np
from PIL import Image

from services.weather.crosswind import airport_db

logger = logging.getLogger(__name__)

FAA_SECTIONAL_TILE_URL = "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}"
ESRI_DARK_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"

class RadarMapGenerator:
    def __init__(self):
        self._headers = {"User-Agent": "PilotBrief-MapGenerator/1.0 (Aviation Tool)"}

    @staticmethod
    def _deg2num(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
        lat_rad = math.radians(lat_deg)
        n = 2.0 ** zoom
        xtile = int((lon_deg + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return (xtile, ytile)

    @staticmethod
    def _num2deg(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
        n = 2.0 ** zoom
        lon_deg = xtile / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
        lat_deg = math.degrees(lat_rad)
        return (lat_deg, lon_deg)

    async def _fetch_single_tile(self, session: aiohttp.ClientSession, url: str) -> Optional[Image.Image]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            pass
        return None

    async def _fetch_stitched_basemap(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        tile_type: str = "sectional",  # 'sectional' or 'dark'
        zoom: int = 8
    ) -> Optional[Tuple[Image.Image, Tuple[float, float, float, float]]]:
        """
        Stitches map tiles across bounding box.
        """
        x0, y0 = self._deg2num(max_lat, min_lon, zoom)
        x1, y1 = self._deg2num(min_lat, max_lon, zoom)

        x_start, x_end = min(x0, x1), max(x0, x1)
        y_start, y_end = min(y0, y1), max(y0, y1)

        cols = x_end - x_start + 1
        rows = y_end - y_start + 1

        if cols > 14 or rows > 14:
            zoom = max(6, zoom - 1)
            return await self._fetch_stitched_basemap(min_lon, min_lat, max_lon, max_lat, tile_type, zoom)

        bg_color = (220, 220, 220, 255) if tile_type == "sectional" else (20, 24, 30, 255)
        stitched = Image.new("RGBA", (cols * 256, rows * 256), color=bg_color)

        url_template = FAA_SECTIONAL_TILE_URL if tile_type == "sectional" else ESRI_DARK_TILE_URL

        async with aiohttp.ClientSession(headers=self._headers) as session:
            tasks = []
            positions = []
            for r, y in enumerate(range(y_start, y_end + 1)):
                for c, x in enumerate(range(x_start, x_end + 1)):
                    url = url_template.format(z=zoom, y=y, x=x)
                    tasks.append(self._fetch_single_tile(session, url))
                    positions.append((c * 256, r * 256))

            tile_images = await asyncio.gather(*tasks, return_exceptions=True)

            successful_tiles = 0
            for pos, img in zip(positions, tile_images):
                if isinstance(img, Image.Image):
                    stitched.paste(img, pos)
                    successful_tiles += 1

            # If FAA sectional returns empty (e.g. outside US), fallback to ESRI Dark
            if tile_type == "sectional" and successful_tiles < (cols * rows * 0.3):
                return await self._fetch_stitched_basemap(min_lon, min_lat, max_lon, max_lat, "dark", zoom)

        nw_lat, nw_lon = self._num2deg(x_start, y_start, zoom)
        se_lat, se_lon = self._num2deg(x_end + 1, y_end + 1, zoom)

        return stitched, (nw_lon, se_lon, se_lat, nw_lat)

    async def _fetch_nexrad_layer(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        width: int = 1000,
        height: int = 800
    ) -> Optional[Image.Image]:
        """
        Fetches current composite NEXRAD base reflectivity from IEM WMS.
        """
        wms_url = (
            f"https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi?"
            f"SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=nexrad-n0q-m05m&"
            f"FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:4326&"
            f"BBOX={min_lon},{min_lat},{max_lon},{max_lat}&WIDTH={width}&HEIGHT={height}"
        )
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(wms_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        except Exception as e:
            logger.warning(f"Could not fetch NEXRAD overlay: {e}")
        return None

    async def _fetch_regional_metars(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> List[Dict[str, Any]]:
        """
        Fetches all current METAR reporting stations in the bounding box.
        """
        url = f"https://aviationweather.gov/api/data/metar?bbox={min_lat:.2f},{min_lon:.2f},{max_lat:.2f},{max_lon:.2f}&format=json"
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list):
                            return data
        except Exception as e:
            logger.warning(f"Error fetching regional METARs: {e}")
        return []

    async def generate_sectional_overview_map(
        self,
        dep_icao: str,
        dest_icao: Optional[str] = None,
        dep_fltcat: str = "VFR",
        dest_fltcat: Optional[str] = None,
        sigmets: Optional[List[Dict[str, Any]]] = None,
        radius_nm: float = 95.0
    ) -> Optional[bytes]:
        """
        Generates a 100 NM ForeFlight-Style VFR Sectional Chart Map with:
        - Official FAA VFR Sectional Chart tiles
        - NEXRAD precipitation radar overlay
        - ForeFlight color-coded METAR station dots (VFR/MVFR/IFR/LIFR)
        - 25, 50, 75, 100 NM range rings
        - Flight course route line
        - Active SIGMET hazard polygons
        """
        dep_coord = airport_db.get_coordinates(dep_icao)
        if not dep_coord:
            return None

        dep_lat, dep_lon = dep_coord
        dest_lat, dest_lon = (None, None)
        if dest_icao:
            dest_coord = airport_db.get_coordinates(dest_icao)
            if dest_coord:
                dest_lat, dest_lon = dest_coord

        deg_pad = radius_nm / 60.0
        if dest_lat and dest_lon:
            min_lat = min(dep_lat, dest_lat) - deg_pad * 0.7
            max_lat = max(dep_lat, dest_lat) + deg_pad * 0.7
            min_lon = min(dep_lon, dest_lon) - deg_pad * 0.9
            max_lon = max(dep_lon, dest_lon) + deg_pad * 0.9
        else:
            min_lat = dep_lat - deg_pad
            max_lat = dep_lat + deg_pad
            min_lon = dep_lon - deg_pad * 1.3
            max_lon = dep_lon + deg_pad * 1.3

        # Parallel fetch basemap, radar, regional METARs
        basemap_res, radar_img, regional_metars = await asyncio.gather(
            self._fetch_stitched_basemap(min_lon, min_lat, max_lon, max_lat, tile_type="sectional", zoom=8),
            self._fetch_nexrad_layer(min_lon, min_lat, max_lon, max_lat, width=1000, height=800),
            self._fetch_regional_metars(min_lon, min_lat, max_lon, max_lat),
            return_exceptions=True
        )

        fig, ax = plt.subplots(figsize=(10, 8), dpi=140)
        fig.patch.set_facecolor("#10151C")
        ax.set_facecolor("#DCDDE1")
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)

        # 1. Sectional Basemap
        if isinstance(basemap_res, tuple) and basemap_res[0]:
            base_img, (bm_w, bm_e, bm_s, bm_n) = basemap_res
            ax.imshow(base_img, extent=[bm_w, bm_e, bm_s, bm_n], origin="upper", zorder=1)

        # 2. NEXRAD Precipitation Layer (semi-transparent so sectional remains readable)
        if isinstance(radar_img, Image.Image):
            ax.imshow(radar_img, extent=[min_lon, max_lon, min_lat, max_lat], origin="upper", alpha=0.55, zorder=2)

        # 3. Gridlines
        ax.grid(True, color="#2F3542", linestyle=":", linewidth=0.5, alpha=0.4, zorder=3)
        ax.tick_params(colors="#CAD3C8", labelsize=8)

        # 4. Range Rings
        nm_to_deg = 1.0 / 60.0
        for ring_nm in [25, 50, 75]:
            circ = Circle(
                (dep_lon, dep_lat),
                radius=ring_nm * nm_to_deg,
                fill=False,
                edgecolor="#0984E3",
                linestyle="--",
                linewidth=1.2,
                alpha=0.75,
                zorder=4
            )
            ax.add_patch(circ)
            ax.text(
                dep_lon, dep_lat + ring_nm * nm_to_deg,
                f" {ring_nm} NM ",
                color="white",
                fontsize=7,
                fontweight="bold",
                ha="center",
                va="center",
                bbox=dict(boxstyle="round,pad=0.15", facecolor="#0984E3", edgecolor="none", alpha=0.85),
                zorder=5
            )

        # 5. SIGMET & AIRMET Polygons
        if sigmets and isinstance(sigmets, list):
            for s in sigmets:
                geom = s.get("geometry", {})
                coords = geom.get("coordinates", [])
                props = s.get("properties", {})
                hazard = str(props.get("hazard", "")).upper()
                top_alt = props.get("altitudeHi1") or props.get("top")

                if "CONVECTIVE" in hazard or "TS" in hazard:
                    color = "#FF3838"
                    lbl = "⚡ CONVECTIVE SIGMET" + (f" (FL{int(top_alt/100)})" if top_alt else "")
                elif "TURB" in hazard:
                    color = "#FF9F1A"
                    lbl = "💨 TURBULENCE"
                elif "ICE" in hazard or "ICING" in hazard:
                    color = "#00D2D3"
                    lbl = "❄️ ICING"
                else:
                    color = "#FA8231"
                    lbl = f"⚠️ {hazard or 'SIGMET'}"

                if geom.get("type") == "Polygon" and coords:
                    poly_pts = coords[0]
                    p_lons = [p[0] for p in poly_pts]
                    p_lats = [p[1] for p in poly_pts]
                    if (max(p_lons) >= min_lon and min(p_lons) <= max_lon and
                        max(p_lats) >= min_lat and min(p_lats) <= max_lat):
                        patch = Polygon(poly_pts, closed=True, facecolor=color, edgecolor=color, alpha=0.30, linewidth=2.0, zorder=6)
                        ax.add_patch(patch)
                        c_lon = sum(p_lons) / len(p_lons)
                        c_lat = sum(p_lats) / len(p_lats)
                        if min_lon <= c_lon <= max_lon and min_lat <= c_lat <= max_lat:
                            ax.text(c_lon, c_lat, lbl, color="white", fontsize=8, fontweight="bold", ha="center", va="center", bbox=dict(boxstyle="round,pad=0.2", facecolor=color, alpha=0.8, edgecolor="none"), zorder=7)

        # 6. ForeFlight METAR Station Dots
        COLOR_MAP = {
            "VFR": "#00B894",   # Emerald Green
            "MVFR": "#0984E3",  # Blue
            "IFR": "#D63031",   # Red
            "LIFR": "#6C5CE7"   # Purple
        }

        plotted_metars = 0
        if isinstance(regional_metars, list):
            for m in regional_metars:
                try:
                    m_lat = float(m.get("lat", 0))
                    m_lon = float(m.get("lon", 0))
                    m_icao = m.get("icaoId", "").upper()
                    cat = str(m.get("fltcat") or m.get("fltCat", "VFR")).upper()

                    if not (min_lon <= m_lon <= max_lon and min_lat <= m_lat <= max_lat):
                        continue
                    if m_icao in [dep_icao, dest_icao]:
                        continue

                    c = COLOR_MAP.get(cat, "#00B894")
                    ax.scatter([m_lon], [m_lat], color=c, s=110, edgecolors="white", linewidth=1.5, zorder=8)
                    ax.text(
                        m_lon, m_lat - 0.045,
                        m_icao,
                        color="white",
                        fontsize=7,
                        fontweight="bold",
                        ha="center",
                        va="top",
                        bbox=dict(boxstyle="round,pad=0.12", facecolor="#2D3436", alpha=0.85, edgecolor=c, linewidth=0.8),
                        zorder=9
                    )
                    plotted_metars += 1
                except Exception:
                    pass

        # 7. Route Vector (if destination exists)
        if dest_lat and dest_lon:
            ax.plot([dep_lon, dest_lon], [dep_lat, dest_lat], color="#D63031", linestyle="--", linewidth=3.0, zorder=10)
            d_lat = (dest_lat - dep_lat) * 60.0
            d_lon = (dest_lon - dep_lon) * 60.0 * math.cos(math.radians((dep_lat + dest_lat) / 2.0))
            dist_nm = math.sqrt(d_lat * d_lat + d_lon * d_lon)
            course_deg = (math.degrees(math.atan2(d_lon, d_lat)) + 360) % 360

            mid_lon = (dep_lon + dest_lon) / 2.0
            mid_lat = (dep_lat + dest_lat) / 2.0
            ax.text(
                mid_lon, mid_lat + 0.05,
                f"{int(round(dist_nm))} NM | {int(round(course_deg)):03d}° M",
                color="white",
                fontsize=8,
                fontweight="bold",
                ha="center",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#D63031", alpha=0.9, edgecolor="white", linewidth=1),
                zorder=11
            )

            # Destination Pin
            dest_color = COLOR_MAP.get(dest_fltcat or "VFR", "#00B894")
            ax.scatter([dest_lon], [dest_lat], color=dest_color, s=240, edgecolors="white", linewidth=2.5, zorder=12)
            ax.text(
                dest_lon, dest_lat + 0.08,
                f"DEST: {dest_icao}\n{dest_fltcat or 'VFR'}",
                color="white",
                fontsize=8.5,
                fontweight="bold",
                ha="center",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#2D3436", edgecolor=dest_color, linewidth=1.5),
                zorder=13
            )

        # 8. Departure Pin
        dep_color = COLOR_MAP.get(dep_fltcat, "#00B894")
        ax.scatter([dep_lon], [dep_lat], color="#FFD32A", s=300, edgecolors="#D63031", linewidth=2.5, zorder=12)
        ax.text(
            dep_lon, dep_lat + 0.08,
            f"DEP: {dep_icao}\n{dep_fltcat}",
            color="black",
            fontsize=9,
            fontweight="bold",
            ha="center",
            bbox=dict(boxstyle="round,pad=0.28", facecolor="#FFD32A", edgecolor="#D63031", linewidth=1.8),
            zorder=13
        )

        # 9. Header and Legend
        title_txt = f"ForeFlight VFR Sectional & Regional Weather ✈️ {dep_icao}" + (f" ➔ {dest_icao}" if dest_icao else " (100 NM Radius)")
        ax.set_title(title_txt, color="#F5F6FA", fontsize=11.5, fontweight="bold", pad=12)
        ax.set_xlabel(f"Generated at {datetime.now(timezone.utc).strftime('%H:%MZ')} • FAA VFR Sectional & NOAA AWC METARs ({plotted_metars} Stations)", color="#A4B0BE", fontsize=8)

        legend_patches = [
            mpatches.Patch(color="#00B894", label="VFR (Vis >5, Ceil >3000)"),
            mpatches.Patch(color="#0984E3", label="MVFR (Vis 3-5, Ceil 1000-3000)"),
            mpatches.Patch(color="#D63031", label="IFR (Vis 1-<3, Ceil 500-<1000)"),
            mpatches.Patch(color="#6C5CE7", label="LIFR (Vis <1, Ceil <500)"),
            mpatches.Patch(color="#FF3838", label="Convective SIGMET")
        ]
        ax.legend(
            handles=legend_patches,
            loc="lower right",
            facecolor="#2D3436",
            edgecolor="#636E72",
            fontsize=7.5,
            labelcolor="#F5F6FA"
        )

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    async def generate_briefing_map(
        self,
        dep_icao: str,
        dest_icao: Optional[str] = None,
        dep_fltcat: str = "VFR",
        dest_fltcat: Optional[str] = None,
        sigmets: Optional[List[Dict[str, Any]]] = None,
        radius_nm: float = 55.0
    ) -> Optional[bytes]:
        """
        Renders a focused Tactical Dark Radar & Airspace Overview map.
        """
        dep_coord = airport_db.get_coordinates(dep_icao)
        if not dep_coord:
            return None

        dep_lat, dep_lon = dep_coord
        dest_lat, dest_lon = (None, None)
        if dest_icao:
            dest_coord = airport_db.get_coordinates(dest_icao)
            if dest_coord:
                dest_lat, dest_lon = dest_coord

        deg_pad = radius_nm / 60.0
        if dest_lat and dest_lon:
            min_lat = min(dep_lat, dest_lat) - deg_pad
            max_lat = max(dep_lat, dest_lat) + deg_pad
            min_lon = min(dep_lon, dest_lon) - deg_pad * 1.3
            max_lon = max(dep_lon, dest_lon) + deg_pad * 1.3
        else:
            min_lat = dep_lat - deg_pad
            max_lat = dep_lat + deg_pad
            min_lon = dep_lon - deg_pad * 1.3
            max_lon = dep_lon + deg_pad * 1.3

        basemap_res, radar_img = await asyncio.gather(
            self._fetch_stitched_basemap(min_lon, min_lat, max_lon, max_lat, tile_type="dark", zoom=9),
            self._fetch_nexrad_layer(min_lon, min_lat, max_lon, max_lat, width=900, height=700),
            return_exceptions=True
        )

        fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
        fig.patch.set_facecolor("#0F1318")
        ax.set_facecolor("#151A21")
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)

        if isinstance(basemap_res, tuple) and basemap_res[0]:
            base_img, (bm_w, bm_e, bm_s, bm_n) = basemap_res
            ax.imshow(base_img, extent=[bm_w, bm_e, bm_s, bm_n], origin="upper", zorder=1)

        if isinstance(radar_img, Image.Image):
            ax.imshow(radar_img, extent=[min_lon, max_lon, min_lat, max_lat], origin="upper", alpha=0.75, zorder=2)

        ax.grid(True, color="#2C3A47", linestyle="--", linewidth=0.6, alpha=0.5, zorder=3)
        ax.tick_params(colors="#8395A7", labelsize=8)

        nm_to_deg = 1.0 / 60.0
        for ring_nm in [25, 50]:
            circ = Circle((dep_lon, dep_lat), radius=ring_nm * nm_to_deg, fill=False, edgecolor="#48DBFB", linestyle=":", linewidth=1.2, alpha=0.65, zorder=4)
            ax.add_patch(circ)
            ax.text(dep_lon, dep_lat + ring_nm * nm_to_deg, f"{ring_nm} NM", color="#00D2D3", fontsize=7, fontweight="bold", ha="center", va="bottom", zorder=4)

        if sigmets and isinstance(sigmets, list):
            for s in sigmets:
                geom = s.get("geometry", {})
                coords = geom.get("coordinates", [])
                props = s.get("properties", {})
                hazard = str(props.get("hazard", "")).upper()
                top_alt = props.get("altitudeHi1") or props.get("top")

                if "CONVECTIVE" in hazard or "TS" in hazard:
                    color = "#FF3838"
                    hatch = "//"
                    lbl = "⚡ CONVECTIVE SIGMET" + (f" (FL{int(top_alt/100)})" if top_alt else "")
                elif "TURB" in hazard:
                    color = "#FF9F1A"
                    hatch = "\\\\"
                    lbl = "💨 TURBULENCE"
                elif "ICE" in hazard or "ICING" in hazard:
                    color = "#00D2D3"
                    hatch = ".."
                    lbl = "❄️ ICING"
                elif "IFR" in hazard or "MTN" in hazard:
                    color = "#A55EEA"
                    hatch = ""
                    lbl = "☁️ IFR / MTN OBSCN"
                else:
                    color = "#FA8231"
                    hatch = ""
                    lbl = f"⚠️ {hazard or 'SIGMET'}"

                if geom.get("type") == "Polygon" and coords:
                    poly_pts = coords[0]
                    p_lons = [p[0] for p in poly_pts]
                    p_lats = [p[1] for p in poly_pts]
                    if (max(p_lons) >= min_lon and min(p_lons) <= max_lon and max(p_lats) >= min_lat and min(p_lats) <= max_lat):
                        patch = Polygon(poly_pts, closed=True, facecolor=color, edgecolor=color, alpha=0.35, hatch=hatch, linewidth=2.0, zorder=5)
                        ax.add_patch(patch)
                        c_lon = sum(p_lons) / len(p_lons)
                        c_lat = sum(p_lats) / len(p_lats)
                        if min_lon <= c_lon <= max_lon and min_lat <= c_lat <= max_lat:
                            ax.text(c_lon, c_lat, lbl, color="white", fontsize=8, fontweight="bold", ha="center", va="center", bbox=dict(boxstyle="round,pad=0.2", facecolor=color, alpha=0.7, edgecolor="none"), zorder=6)

        if dest_lat and dest_lon:
            ax.plot([dep_lon, dest_lon], [dep_lat, dest_lat], color="#FF9FF3", linestyle="--", linewidth=2.5, zorder=7)
            d_lat = (dest_lat - dep_lat) * 60.0
            d_lon = (dest_lon - dep_lon) * 60.0 * math.cos(math.radians((dep_lat + dest_lat) / 2.0))
            dist_nm = math.sqrt(d_lat * d_lat + d_lon * d_lon)
            course_deg = (math.degrees(math.atan2(d_lon, d_lat)) + 360) % 360

            mid_lon = (dep_lon + dest_lon) / 2.0
            mid_lat = (dep_lat + dest_lat) / 2.0
            ax.text(mid_lon, mid_lat + 0.05, f"{int(round(dist_nm))} NM | {int(round(course_deg)):03d}° M", color="#FF9FF3", fontsize=8, fontweight="bold", ha="center", bbox=dict(boxstyle="round,pad=0.25", facecolor="#1B1464", alpha=0.85, edgecolor="#FF9FF3", linewidth=1), zorder=8)

            dest_color = "#2ECC71" if dest_fltcat == "VFR" else ("#3498DB" if dest_fltcat == "MVFR" else "#E74C3C")
            ax.scatter([dest_lon], [dest_lat], color=dest_color, s=180, edgecolors="white", linewidth=2.0, zorder=9)
            ax.text(dest_lon + 0.04, dest_lat, f"{dest_icao} [DEST]\n{dest_fltcat or 'VFR'}", color="white", fontsize=9, fontweight="bold", bbox=dict(boxstyle="round,pad=0.2", facecolor="#1E272E", edgecolor=dest_color, linewidth=1.5), zorder=10)

        dep_color = "#2ECC71" if dep_fltcat == "VFR" else ("#3498DB" if dep_fltcat == "MVFR" else "#E74C3C")
        ax.scatter([dep_lon], [dep_lat], color=dep_color, s=220, edgecolors="white", linewidth=2.5, zorder=9)
        ax.text(dep_lon + 0.04, dep_lat, f"{dep_icao} [DEP]\n{dep_fltcat}", color="white", fontsize=9, fontweight="bold", bbox=dict(boxstyle="round,pad=0.2", facecolor="#1E272E", edgecolor=dep_color, linewidth=1.5), zorder=10)

        title_txt = f"PilotBrief Tactical Radar Overview ✈️ {dep_icao}" + (f" ➔ {dest_icao}" if dest_icao else "")
        ax.set_title(title_txt, color="#F5F6FA", fontsize=12, fontweight="bold", pad=12)
        ax.set_xlabel(f"Generated at {datetime.now(timezone.utc).strftime('%H:%MZ')} • NOAA AWC & IEM NEXRAD", color="#8395A7", fontsize=8)

        legend_patches = [
            mpatches.Patch(color="#2ECC71", label="VFR (Vis >5, Ceil >3000)"),
            mpatches.Patch(color="#3498DB", label="MVFR (Marginal VFR)"),
            mpatches.Patch(color="#E74C3C", label="IFR / Convective SIGMET"),
            mpatches.Patch(color="#FF9F1A", label="Turbulence SIGMET"),
            mpatches.Patch(color="#00D2D3", label="NEXRAD Precipitation")
        ]
        ax.legend(handles=legend_patches, loc="lower right", facecolor="#1E272E", edgecolor="#485460", fontsize=7, labelcolor="#F5F6FA")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

radar_map_generator = RadarMapGenerator()

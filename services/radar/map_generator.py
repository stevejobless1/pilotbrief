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

class RadarMapGenerator:
    def __init__(self):
        self._headers = {"User-Agent": "PilotBrief-MapGenerator/1.0"}

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

    async def _fetch_basemap_tiles(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        zoom: int = 9
    ) -> Optional[Tuple[Image.Image, Tuple[float, float, float, float]]]:
        """
        Stitches ESRI World Dark Gray basemap tiles across the bounding box.
        Returns: (stitched_pil_image, (actual_min_lon, actual_max_lon, actual_min_lat, actual_max_lat))
        """
        x0, y0 = self._deg2num(max_lat, min_lon, zoom)
        x1, y1 = self._deg2num(min_lat, max_lon, zoom)

        x_start, x_end = min(x0, x1), max(x0, x1)
        y_start, y_end = min(y0, y1), max(y0, y1)

        cols = x_end - x_start + 1
        rows = y_end - y_start + 1

        if cols > 12 or rows > 12:
            zoom = max(6, zoom - 1)
            return await self._fetch_basemap_tiles(min_lon, min_lat, max_lon, max_lat, zoom)

        stitched = Image.new("RGBA", (cols * 256, rows * 256), color=(20, 24, 30, 255))

        async with aiohttp.ClientSession(headers=self._headers) as session:
            tasks = []
            positions = []
            for r, y in enumerate(range(y_start, y_end + 1)):
                for c, x in enumerate(range(x_start, x_end + 1)):
                    # ESRI World Dark Gray Base: tile/{z}/{y}/{x}
                    url = f"https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{zoom}/{y}/{x}"
                    tasks.append(self._fetch_single_tile(session, url))
                    positions.append((c * 256, r * 256))

            tile_images = await asyncio.gather(*tasks, return_exceptions=True)

            for pos, img in zip(positions, tile_images):
                if isinstance(img, Image.Image):
                    stitched.paste(img, pos)

        # Actual geographic bounds of stitched image
        nw_lat, nw_lon = self._num2deg(x_start, y_start, zoom)
        se_lat, se_lon = self._num2deg(x_end + 1, y_end + 1, zoom)

        return stitched, (nw_lon, se_lon, se_lat, nw_lat)

    async def _fetch_single_tile(self, session: aiohttp.ClientSession, url: str) -> Optional[Image.Image]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            pass
        return None

    async def _fetch_nexrad_layer(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        width: int = 900,
        height: int = 700
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
        Renders a composite aviation radar and airspace map with:
        - ESRI World Dark Gray basemap
        - Real-time NEXRAD radar overlay
        - SIGMET & AIRMET colored hazard polygons
        - Departure and destination markers & runway headings
        - Flight route line with distance & course
        - 25nm / 50nm range rings
        """
        dep_coord = airport_db.get_coordinates(dep_icao)
        if not dep_coord:
            logger.warning(f"No coordinates found for {dep_icao}")
            return None

        dep_lat, dep_lon = dep_coord
        dest_lat, dest_lon = (None, None)
        if dest_icao:
            dest_coord = airport_db.get_coordinates(dest_icao)
            if dest_coord:
                dest_lat, dest_lon = dest_coord

        # Calculate bounding box
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

        # Fetch basemap and radar asynchronously in parallel
        basemap_res, radar_img = await asyncio.gather(
            self._fetch_basemap_tiles(min_lon, min_lat, max_lon, max_lat, zoom=9),
            self._fetch_nexrad_layer(min_lon, min_lat, max_lon, max_lat, width=900, height=700),
            return_exceptions=True
        )

        fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
        fig.patch.set_facecolor("#0F1318")
        ax.set_facecolor("#151A21")

        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)

        # 1. Render Basemap
        if isinstance(basemap_res, tuple) and basemap_res[0]:
            base_img, (bm_w, bm_e, bm_s, bm_n) = basemap_res
            ax.imshow(base_img, extent=[bm_w, bm_e, bm_s, bm_n], origin="upper", zorder=1)

        # 2. Render NEXRAD precipitation overlay
        if isinstance(radar_img, Image.Image):
            ax.imshow(radar_img, extent=[min_lon, max_lon, min_lat, max_lat], origin="upper", alpha=0.75, zorder=2)

        # 3. Grid lines
        ax.grid(True, color="#2C3A47", linestyle="--", linewidth=0.6, alpha=0.5, zorder=3)
        ax.tick_params(colors="#8395A7", labelsize=8)

        # 4. Range Rings around Departure (25 NM, 50 NM)
        nm_to_deg = 1.0 / 60.0
        for ring_nm in [25, 50]:
            circ = Circle(
                (dep_lon, dep_lat),
                radius=ring_nm * nm_to_deg,
                fill=False,
                edgecolor="#48DBFB",
                linestyle=":",
                linewidth=1.2,
                alpha=0.65,
                zorder=4
            )
            ax.add_patch(circ)
            ax.text(
                dep_lon, dep_lat + ring_nm * nm_to_deg,
                f"{ring_nm} NM",
                color="#00D2D3",
                fontsize=7,
                fontweight="bold",
                ha="center",
                va="bottom",
                zorder=4
            )

        # 5. Plot Active SIGMET & AIRMET Polygons
        sigmet_drawn = 0
        if sigmets:
            for s in sigmets:
                geom = s.get("geometry", {})
                gtype = geom.get("type", "")
                coords = geom.get("coordinates", [])
                props = s.get("properties", {})
                hazard = str(props.get("hazard", "")).upper()
                sig_type = str(props.get("airSigmetType", "SIGMET")).upper()
                top_alt = props.get("altitudeHi1") or props.get("top")

                # Style & Color code
                if "CONVECTIVE" in hazard or "TS" in hazard or "C" in str(props.get("alphaChar", "")):
                    color = "#FF3838"  # Vivid Red
                    hatch = "//"
                    lbl = f"⚡ CONVECTIVE SIGMET" + (f" (FL{int(top_alt/100)})" if top_alt else "")
                elif "TURB" in hazard:
                    color = "#FF9F1A"  # Vivid Orange
                    hatch = "\\\\"
                    lbl = f"💨 TURBULENCE" + (f" (FL{int(top_alt/100)})" if top_alt else "")
                elif "ICE" in hazard or "ICING" in hazard:
                    color = "#00D2D3"  # Cyan / Blue
                    hatch = ".."
                    lbl = f"❄️ ICING" + (f" (FL{int(top_alt/100)})" if top_alt else "")
                elif "IFR" in hazard or "MTN" in hazard:
                    color = "#A55EEA"  # Violet
                    hatch = ""
                    lbl = "☁️ IFR / MTN OBSCN"
                else:
                    color = "#FA8231"
                    hatch = ""
                    lbl = f"⚠️ {hazard or 'SIGMET'}"

                if gtype == "Polygon" and coords:
                    poly_pts = coords[0]
                    # Check if polygon is somewhat within map bounds
                    p_lons = [p[0] for p in poly_pts]
                    p_lats = [p[1] for p in poly_pts]
                    if (max(p_lons) >= min_lon and min(p_lons) <= max_lon and
                        max(p_lats) >= min_lat and min(p_lats) <= max_lat):
                        patch = Polygon(
                            poly_pts,
                            closed=True,
                            facecolor=color,
                            edgecolor=color,
                            alpha=0.35,
                            hatch=hatch,
                            linewidth=2.0,
                            zorder=5
                        )
                        ax.add_patch(patch)
                        # Add centered label
                        c_lon = sum(p_lons) / len(p_lons)
                        c_lat = sum(p_lats) / len(p_lats)
                        if min_lon <= c_lon <= max_lon and min_lat <= c_lat <= max_lat:
                            ax.text(
                                c_lon, c_lat, lbl,
                                color="white",
                                fontsize=8,
                                fontweight="bold",
                                ha="center",
                                va="center",
                                bbox=dict(boxstyle="round,pad=0.2", facecolor=color, alpha=0.7, edgecolor="none"),
                                zorder=6
                            )
                        sigmet_drawn += 1

        # 6. Flight Route Vector & Course Annotation
        if dest_lat and dest_lon:
            ax.plot([dep_lon, dest_lon], [dep_lat, dest_lat], color="#FF9FF3", linestyle="--", linewidth=2.5, zorder=7)
            
            # Calculate distance and course
            d_lat = (dest_lat - dep_lat) * 60.0
            d_lon = (dest_lon - dep_lon) * 60.0 * math.cos(math.radians((dep_lat + dest_lat) / 2.0))
            dist_nm = math.sqrt(d_lat * d_lat + d_lon * d_lon)
            course_deg = (math.degrees(math.atan2(d_lon, d_lat)) + 360) % 360

            mid_lon = (dep_lon + dest_lon) / 2.0
            mid_lat = (dep_lat + dest_lat) / 2.0
            ax.text(
                mid_lon, mid_lat + 0.05,
                f"{int(round(dist_nm))} NM | {int(round(course_deg)):03d}° M",
                color="#FF9FF3",
                fontsize=8,
                fontweight="bold",
                ha="center",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#1B1464", alpha=0.85, edgecolor="#FF9FF3", linewidth=1),
                zorder=8
            )

            # Destination Airport Pin
            dest_color = "#2ECC71" if dest_fltcat == "VFR" else ("#3498DB" if dest_fltcat == "MVFR" else "#E74C3C")
            ax.scatter([dest_lon], [dest_lat], color=dest_color, s=180, edgecolors="white", linewidth=2.0, zorder=9)
            ax.text(
                dest_lon + 0.04, dest_lat,
                f"{dest_icao} [DEST]\n{dest_fltcat or 'VFR'}",
                color="white",
                fontsize=9,
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#1E272E", edgecolor=dest_color, linewidth=1.5),
                zorder=10
            )

        # 7. Departure Airport Pin
        dep_color = "#2ECC71" if dep_fltcat == "VFR" else ("#3498DB" if dep_fltcat == "MVFR" else "#E74C3C")
        ax.scatter([dep_lon], [dep_lat], color=dep_color, s=220, edgecolors="white", linewidth=2.5, zorder=9)
        ax.text(
            dep_lon + 0.04, dep_lat,
            f"{dep_icao} [DEP]\n{dep_fltcat}",
            color="white",
            fontsize=9,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#1E272E", edgecolor=dep_color, linewidth=1.5),
            zorder=10
        )

        # 8. Titles & Legends
        title_txt = f"PilotBrief Radar & Airspace Overview ✈️ {dep_icao}" + (f" ➔ {dest_icao}" if dest_icao else "")
        ax.set_title(title_txt, color="#F5F6FA", fontsize=12, fontweight="bold", pad=12)
        ax.set_xlabel(f"Generated at {datetime.now(timezone.utc).strftime('%H:%MZ')} • NOAA AWC & IEM NEXRAD", color="#8395A7", fontsize=8)

        legend_patches = [
            mpatches.Patch(color="#2ECC71", label="VFR (Vis >5, Ceil >3000)"),
            mpatches.Patch(color="#3498DB", label="MVFR (Marginal VFR)"),
            mpatches.Patch(color="#E74C3C", label="IFR / Convective SIGMET"),
            mpatches.Patch(color="#FF9F1A", label="Turbulence SIGMET"),
            mpatches.Patch(color="#00D2D3", label="NEXRAD Precipitation")
        ]
        ax.legend(
            handles=legend_patches,
            loc="lower right",
            facecolor="#1E272E",
            edgecolor="#485460",
            fontsize=7,
            labelcolor="#F5F6FA"
        )

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

radar_map_generator = RadarMapGenerator()

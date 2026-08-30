import io
import math
import logging
import asyncio
import aiohttp
from typing import Dict, Any, List, Optional, Tuple
import matplotlib
matplotlib.use("Agg")  # Headless backend for Docker & servers
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
    def _latlon_to_mercator(lat: float, lon: float) -> Tuple[float, float]:
        """Convert WGS84 lat/lon to Web Mercator (EPSG:3857) meters."""
        r_major = 6378137.0
        x = r_major * math.radians(lon)
        scale = x / lon if lon != 0 else 1.0
        lat_rad = math.radians(lat)
        y = 3189068.5 * math.log((1.0 + math.sin(lat_rad)) / (1.0 - math.sin(lat_rad)))
        return x, y

    async def _fetch_nexrad_layer(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        width: int = 800,
        height: int = 600
    ) -> Optional[Image.Image]:
        """
        Fetches current composite NEXRAD base reflectivity from IEM WMS in EPSG:4326.
        """
        wms_url = (
            f"https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi?"
            f"SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=nexrad-n0q-m05m&"
            f"FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:4326&"
            f"BBOX={min_lon},{min_lat},{max_lon},{max_lat}&WIDTH={width}&HEIGHT={height}"
        )
        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(wms_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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
        radius_nm: float = 65.0
    ) -> Optional[bytes]:
        """
        Renders a radar and airspace map centered on departure/route.
        Returns PNG bytes for Discord attachment.
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

        # Calculate bounding box in degrees (1 deg lat ~= 60 nm)
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

        # Fetch radar overlay asynchronously
        radar_img = await self._fetch_nexrad_layer(min_lon, min_lat, max_lon, max_lat, width=800, height=600)

        # Matplotlib plot in dark theme
        fig, ax = plt.subplots(figsize=(8, 6), dpi=120)
        fig.patch.set_facecolor("#1e2124")
        ax.set_facecolor("#121417")

        # Set map extent
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)

        # Draw grid lines
        ax.grid(True, color="#2c3e50", linestyle="--", linewidth=0.6, alpha=0.6)
        ax.tick_params(colors="#95a5a6", labelsize=8)

        # Plot NEXRAD radar image if available
        if radar_img:
            ax.imshow(radar_img, extent=[min_lon, max_lon, min_lat, max_lat], origin="upper", alpha=0.75, zorder=2)

        # Draw Range Rings around departure (25nm, 50nm)
        nm_to_deg = 1.0 / 60.0
        for ring_nm in [25, 50]:
            if ring_nm <= radius_nm * 1.2:
                circ = Circle(
                    (dep_lon, dep_lat),
                    radius=ring_nm * nm_to_deg,
                    fill=False,
                    edgecolor="#34495e",
                    linestyle=":",
                    linewidth=1.0,
                    alpha=0.8,
                    zorder=1
                )
                ax.add_patch(circ)
                ax.text(
                    dep_lon, dep_lat + ring_nm * nm_to_deg,
                    f"{ring_nm} NM",
                    color="#7f8c8d",
                    fontsize=7,
                    ha="center",
                    va="bottom",
                    zorder=1
                )

        # Draw SIGMET polygons if any intersect the view
        if sigmets:
            for s in sigmets:
                geom = s.get("geometry", {})
                gtype = geom.get("type", "")
                coords = geom.get("coordinates", [])
                props = s.get("properties", {})
                hazard = props.get("hazard", "SIGMET")
                
                color = "#e74c3c" if "CONVECTIVE" in hazard.upper() or "TS" in hazard.upper() else (
                    "#e67e22" if "TURB" in hazard.upper() else "#3498db"
                )

                if gtype == "Polygon" and coords:
                    poly_pts = coords[0]
                    patch = Polygon(poly_pts, closed=True, facecolor=color, edgecolor=color, alpha=0.25, linewidth=1.5, zorder=3)
                    ax.add_patch(patch)

        # Draw Flight Route Vector if destination exists
        if dest_lat and dest_lon:
            ax.plot([dep_lon, dest_lon], [dep_lat, dest_lat], color="#00d2d3", linestyle="--", linewidth=2.0, zorder=4, label="Route")
            # Destination marker
            dest_color = "#2ecc71" if dest_fltcat == "VFR" else ("#3498db" if dest_fltcat == "MVFR" else "#e74c3c")
            ax.scatter([dest_lon], [dest_lat], color=dest_color, s=120, edgecolors="white", linewidth=1.5, zorder=5)
            ax.text(dest_lon + 0.05, dest_lat, f"{dest_icao}\n({dest_fltcat or 'DEST'})", color="white", fontsize=9, fontweight="bold", zorder=6)

        # Departure marker
        dep_color = "#2ecc71" if dep_fltcat == "VFR" else ("#3498db" if dep_fltcat == "MVFR" else "#e74c3c")
        ax.scatter([dep_lon], [dep_lat], color=dep_color, s=150, edgecolors="white", linewidth=2.0, zorder=5)
        ax.text(dep_lon + 0.05, dep_lat, f"{dep_icao} (DEP)\n{dep_fltcat}", color="white", fontsize=9, fontweight="bold", zorder=6)

        # Title and styling
        title_txt = f"PilotBrief Radar & Airspace Overview - {dep_icao}" + (f" ➔ {dest_icao}" if dest_icao else "")
        ax.set_title(title_txt, color="white", fontsize=11, fontweight="bold", pad=10)
        ax.set_xlabel("Longitude", color="#95a5a6", fontsize=8)
        ax.set_ylabel("Latitude", color="#95a5a6", fontsize=8)

        # Legend patches
        legend_patches = [
            mpatches.Patch(color="#2ecc71", label="VFR"),
            mpatches.Patch(color="#3498db", label="MVFR / Icing"),
            mpatches.Patch(color="#e74c3c", label="IFR / Convective SIGMET"),
            mpatches.Patch(color="#00d2d3", label="NEXRAD Precipitation")
        ]
        ax.legend(handles=legend_patches, loc="lower right", facecolor="#1e2124", edgecolor="#34495e", fontsize=7, labelcolor="white")

        plt.tight_layout()

        # Save to memory buffer
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

radar_map_generator = RadarMapGenerator()

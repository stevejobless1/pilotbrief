import re
import math
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

class METARDecoder:
    CATEGORY_COLORS = {
        "VFR": 0x2ECC71,   # Green
        "MVFR": 0x3498DB,  # Blue / Marginal
        "IFR": 0xE74C3C,   # Red
        "LIFR": 0x9B59B6   # Magenta / Purple
    }

    CATEGORY_EMOJIS = {
        "VFR": "🟢",
        "MVFR": "🔵",
        "IFR": "🔴",
        "LIFR": "🟣"
    }

    @staticmethod
    def parse_altimeter_inhg(raw_ob: str, raw_altim_val: Optional[float]) -> float:
        """
        Extracts altimeter in inches of mercury (inHg, e.g. 29.92).
        Prioritizes raw METAR string 'A2992' -> 29.92, or converts hPa/mb to inHg.
        """
        if raw_ob:
            # Look for standard US A2992 or A3012 format
            match = re.search(r'\bA(2[7-9]\d{2}|3[0-1]\d{2})\b', raw_ob)
            if match:
                digits = match.group(1)
                return float(f"{digits[:2]}.{digits[2:]}")
            
            # Look for QNH format Q1013
            q_match = re.search(r'\bQ(\d{4})\b', raw_ob)
            if q_match:
                hpa = float(q_match.group(1))
                return round(hpa * 0.029529983, 2)

        if raw_altim_val is not None:
            # If greater than 100, it is in hPa (e.g. 1013.6 mb)
            if raw_altim_val > 100:
                return round(raw_altim_val * 0.029529983, 2)
            else:
                return round(raw_altim_val, 2)

        return 29.92

    @staticmethod
    def determine_flight_category(visibility_sm: Optional[float], ceiling_ft: Optional[int]) -> str:
        """
        Determines FAA flight category (VFR, MVFR, IFR, LIFR).
        """
        vis = visibility_sm if visibility_sm is not None else 10.0
        ceil = ceiling_ft if ceiling_ft is not None else 99999

        if ceil < 500 or vis < 1.0:
            return "LIFR"
        elif ceil < 1000 or vis < 3.0:
            return "IFR"
        elif ceil <= 3000 or vis <= 5.0:
            return "MVFR"
        else:
            return "VFR"

    @staticmethod
    def calculate_density_altitude(elevation_ft: float, temp_c: float, altim_inhg: float) -> Tuple[int, int]:
        """
        Calculates Pressure Altitude and Density Altitude.
        Returns: (pressure_altitude, density_altitude) in feet.
        """
        # PA = elevation + (29.92 - altimeter) * 1000
        pa = elevation_ft + (29.92 - altim_inhg) * 1000.0
        
        # Standard temperature at pressure altitude: 15C at sea level, lapses 2C per 1000ft
        t_std = 15.0 - 2.0 * (pa / 1000.0)
        
        # DA = PA + 120 * (OAT - Standard Temp)
        da = pa + 120.0 * (temp_c - t_std)
        return int(round(pa)), int(round(da))

    @staticmethod
    def assess_carb_icing_risk(temp_c: float, dewpoint_c: float) -> str:
        """
        Estimates FAA Carburetor Icing risk based on temperature and dewpoint spread.
        """
        rh_spread = temp_c - dewpoint_c
        if -10 <= temp_c <= 25 and rh_spread <= 3:
            return "⚠️ HIGH RISK (Glide & Cruise Power)"
        elif -15 <= temp_c <= 30 and rh_spread <= 7:
            return "⚠️ MODERATE RISK (Glide Power)"
        else:
            return "✅ LOW RISK"

    @classmethod
    def decode_metar(cls, data: Dict[str, Any], elevation_ft: float = 0.0) -> Dict[str, Any]:
        """
        Parses AWC METAR JSON structure into decoded student-friendly format.
        """
        raw_text = data.get("rawOb") or data.get("rawText", "")
        station = data.get("icaoId") or data.get("id", "UNKNOWN")
        obs_time = data.get("obsTime") or data.get("reportTime", "")

        # Wind
        wdir = data.get("wdir")
        wspd = data.get("wspd", 0)
        wgst = data.get("wgst")
        is_variable = (wdir == "VRB" or wdir is None)
        wind_str = "Calm" if (wspd == 0 or wspd is None) else (
            f"Variable at {wspd}kt" if is_variable else f"{wdir:03d}° at {wspd}kt" + (f" G{wgst}kt" if wgst else "")
        )

        # Visibility
        vis_val = data.get("visib")
        if isinstance(vis_val, str) and vis_val.endswith("+"):
            vis_miles = float(vis_val[:-1])
        elif vis_val is not None:
            try:
                vis_miles = float(vis_val)
            except ValueError:
                vis_miles = 10.0
        else:
            vis_miles = 10.0
        vis_str = f"{vis_miles} SM" if vis_miles < 10 else "10+ SM"

        # Clouds & Ceiling
        clouds = data.get("clouds", [])
        ceiling_ft = None
        cloud_layers = []
        for layer in clouds:
            cover = layer.get("cover", "")
            base = layer.get("base")
            if base is not None:
                cloud_layers.append(f"{cover} {base}ft AGL")
                if cover in ["BKN", "OVC", "VV"] and ceiling_ft is None:
                    ceiling_ft = int(base)
            else:
                cloud_layers.append(cover)

        clouds_str = ", ".join(cloud_layers) if cloud_layers else "Clear / SKC"

        # Temperature & Dewpoint
        temp_c = data.get("temp")
        dew_c = data.get("dewp")
        temp_str = f"{temp_c:.1f}°C" if temp_c is not None else "N/A"
        dew_str = f"{dew_c:.1f}°C" if dew_c is not None else "N/A"
        spread_str = f"{(temp_c - dew_c):.1f}°C" if (temp_c is not None and dew_c is not None) else "N/A"

        # Altimeter: parse exact inHg format (e.g. 29.93 inHg)
        altim_inhg = cls.parse_altimeter_inhg(raw_text, data.get("altim"))
        altim_hpa = int(round(altim_inhg / 0.029529983))

        # Flight category
        category = data.get("fltcat") or data.get("fltCat")
        if not category:
            category = cls.determine_flight_category(vis_miles, ceiling_ft)

        # Pressure and Density Altitude
        if temp_c is not None:
            pa, da = cls.calculate_density_altitude(elevation_ft, temp_c, altim_inhg)
        else:
            pa, da = int(elevation_ft), int(elevation_ft)

        # Carb icing assessment
        carb_risk = cls.assess_carb_icing_risk(temp_c, dew_c) if (temp_c is not None and dew_c is not None) else "N/A"

        # Weather phenomena (rain, mist, fog, etc.)
        wx_str = data.get("wxString", "")

        return {
            "station": station,
            "raw": raw_text,
            "obs_time": obs_time,
            "category": category,
            "category_color": cls.CATEGORY_COLORS.get(category, 0x95A5A6),
            "category_emoji": cls.CATEGORY_EMOJIS.get(category, "⚪"),
            "wind_dir": wdir,
            "wind_speed": wspd or 0,
            "wind_gust": wgst,
            "wind_str": wind_str,
            "visibility_sm": vis_miles,
            "visibility_str": vis_str,
            "ceiling_ft": ceiling_ft,
            "clouds_str": clouds_str,
            "temp_c": temp_c,
            "dewpoint_c": dew_c,
            "temp_str": temp_str,
            "dew_str": dew_str,
            "temp_dew_spread": spread_str,
            "altimeter_inhg": altim_inhg,
            "altimeter_hpa": altim_hpa,
            "altimeter_str": f"{altim_inhg:.2f} inHg ({altim_hpa} hPa)",
            "pressure_altitude": pa,
            "density_altitude": da,
            "carb_icing_risk": carb_risk,
            "wx_string": wx_str
        }

    @classmethod
    def decode_taf(cls, data: Dict[str, Any], origin_station: str = "") -> Dict[str, Any]:
        """
        Parses TAF data for timeline trends and decoded forecast blocks.
        """
        raw_text = data.get("rawTAF") or data.get("rawText", "")
        station = data.get("icaoId") or data.get("id", "UNKNOWN")
        station_name = data.get("name", station)
        forecasts = data.get("fcsts", [])
        
        parsed_forecasts = []
        for fc in forecasts:
            time_from_raw = fc.get("timeFrom")
            time_to_raw = fc.get("timeTo")

            # Convert epoch or ISO timestamps
            from_str = ""
            to_str = ""
            if isinstance(time_from_raw, (int, float)):
                dt_from = datetime.fromtimestamp(time_from_raw, tz=timezone.utc)
                from_str = dt_from.strftime("%H:%MZ")
            elif time_from_raw:
                from_str = str(time_from_raw)[11:16] + "Z"

            if isinstance(time_to_raw, (int, float)):
                dt_to = datetime.fromtimestamp(time_to_raw, tz=timezone.utc)
                to_str = dt_to.strftime("%H:%MZ")
            elif time_to_raw:
                to_str = str(time_to_raw)[11:16] + "Z"

            time_window = f"{from_str} - {to_str}" if (from_str and to_str) else "Period"

            fc_type = fc.get("fcstChange") or "INITIAL"
            wdir = fc.get("wdir")
            wspd = fc.get("wspd", 0)
            wgst = fc.get("wgst")
            vis = fc.get("visib", "6+")

            # Winds string
            if wspd == 0 or wspd is None:
                wind_desc = "Calm"
            elif wdir is None or wdir == "VRB":
                wind_desc = f"VRB at {wspd}kt" + (f" G{wgst}kt" if wgst else "")
            else:
                wind_desc = f"{int(wdir):03d}° at {wspd}kt" + (f" G{wgst}kt" if wgst else "")

            # Clouds & flight category calculation for forecast block
            clouds_list = []
            block_ceil = None
            for c in fc.get("clouds", []):
                cov = c.get("cover", "")
                base = c.get("base")
                if base is not None:
                    clouds_list.append(f"{cov} {base}ft")
                    if cov in ["BKN", "OVC", "VV"] and block_ceil is None:
                        block_ceil = int(base)
                else:
                    clouds_list.append(cov)

            clouds_desc = ", ".join(clouds_list) if clouds_list else "SKC"
            
            # Numeric vis
            try:
                num_vis = float(str(vis).replace("+", ""))
            except ValueError:
                num_vis = 10.0

            block_cat = cls.determine_flight_category(num_vis, block_ceil)
            block_emoji = cls.CATEGORY_EMOJIS.get(block_cat, "🟢")

            parsed_forecasts.append({
                "time_window": time_window,
                "type": fc_type,
                "wind": wind_desc,
                "vis": f"{vis} SM",
                "clouds": clouds_desc,
                "category": block_cat,
                "category_emoji": block_emoji
            })

        return {
            "station": station,
            "station_name": station_name,
            "is_nearby_fallback": (origin_station != "" and station.upper() != origin_station.upper()),
            "origin_station": origin_station,
            "raw": raw_text,
            "forecasts": parsed_forecasts
        }

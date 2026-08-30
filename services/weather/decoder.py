from typing import Dict, Any, List, Optional, Tuple
import math

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
    def determine_flight_category(visibility_sm: Optional[float], ceiling_ft: Optional[int]) -> str:
        """
        Determines FAA flight category (VFR, MVFR, IFR, LIFR).
        """
        vis = visibility_sm if visibility_sm is not None else 10.0
        # If ceiling is None, sky is clear or only scattered/few clouds -> infinite ceiling
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

        # Altimeter
        altim = data.get("altim", 29.92)
        if altim is None:
            altim = 29.92

        # Flight category
        category = data.get("fltcat")
        if not category:
            category = cls.determine_flight_category(vis_miles, ceiling_ft)

        # Pressure and Density Altitude
        if temp_c is not None:
            pa, da = cls.calculate_density_altitude(elevation_ft, temp_c, altim)
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
            "altimeter": altim,
            "pressure_altitude": pa,
            "density_altitude": da,
            "carb_icing_risk": carb_risk,
            "wx_string": wx_str
        }

    @classmethod
    def decode_taf(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses TAF data for timeline trends.
        """
        raw_text = data.get("rawTAF") or data.get("rawText", "")
        station = data.get("icaoId") or data.get("id", "UNKNOWN")
        forecasts = data.get("fcsts", [])
        
        parsed_forecasts = []
        for fc in forecasts:
            time_from = fc.get("timeFrom")
            time_to = fc.get("timeTo")
            fc_type = fc.get("fcstChange", "FM")
            wdir = fc.get("wdir")
            wspd = fc.get("wspd", 0)
            vis = fc.get("visib", 10.0)
            clouds = [f"{c.get('cover')} {c.get('base', '')}ft" for c in fc.get("clouds", [])]
            parsed_forecasts.append({
                "time_from": time_from,
                "time_to": time_to,
                "type": fc_type,
                "wind": f"{wdir}° at {wspd}kt" if wdir is not None else "VRB",
                "vis": f"{vis} SM",
                "clouds": ", ".join(clouds) if clouds else "SKC"
            })

        return {
            "station": station,
            "raw": raw_text,
            "forecasts": parsed_forecasts
        }

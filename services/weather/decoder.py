import re
import math
import logging
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

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
    def parse_altimeter_inhg(raw_ob: str, raw_altim_val: Optional[Union[float, int, str]]) -> float:
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
            try:
                val = float(raw_altim_val)
                # If greater than 100, it is in hPa (e.g. 1013.6 mb)
                if val > 100:
                    return round(val * 0.029529983, 2)
                else:
                    return round(val, 2)
            except (ValueError, TypeError):
                pass

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
        pa = elevation_ft + (29.92 - altim_inhg) * 1000.0
        t_std = 15.0 - 2.0 * (pa / 1000.0)
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
        raw_wdir = data.get("wdir")
        raw_wspd = data.get("wspd", 0)
        raw_wgst = data.get("wgst")

        try:
            wspd = float(raw_wspd) if raw_wspd is not None else 0.0
        except (ValueError, TypeError):
            wspd = 0.0

        try:
            wgst = float(raw_wgst) if raw_wgst is not None else None
        except (ValueError, TypeError):
            wgst = None

        if raw_wdir is None or str(raw_wdir).strip().upper() == "VRB":
            wdir = None
            is_variable = True
        else:
            try:
                wdir = float(raw_wdir)
                is_variable = False
            except (ValueError, TypeError):
                wdir = None
                is_variable = True

        if wspd == 0:
            wind_str = "Calm"
        elif is_variable:
            wind_str = f"Variable at {int(wspd)}kt" + (f" G{int(wgst)}kt" if wgst else "")
        else:
            wind_str = f"{int(wdir):03d}° at {int(wspd)}kt" + (f" G{int(wgst)}kt" if wgst else "")

        # Visibility
        vis_val = data.get("visib")
        if isinstance(vis_val, str) and vis_val.endswith("+"):
            try:
                vis_miles = float(vis_val[:-1])
            except ValueError:
                vis_miles = 10.0
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
                    try:
                        ceiling_ft = int(base)
                    except (ValueError, TypeError):
                        pass
            else:
                cloud_layers.append(cover)

        clouds_str = ", ".join(cloud_layers) if cloud_layers else "Clear / SKC"

        # Temperature & Dewpoint
        try:
            temp_c = float(data["temp"]) if data.get("temp") is not None else None
        except (ValueError, TypeError):
            temp_c = None

        try:
            dew_c = float(data["dewp"]) if data.get("dewp") is not None else None
        except (ValueError, TypeError):
            dew_c = None

        temp_str = f"{temp_c:.1f}°C" if temp_c is not None else "N/A"
        dew_str = f"{dew_c:.1f}°C" if dew_c is not None else "N/A"
        spread_str = f"{(temp_c - dew_c):.1f}°C" if (temp_c is not None and dew_c is not None) else "N/A"

        # Altimeter
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

        carb_risk = cls.assess_carb_icing_risk(temp_c, dew_c) if (temp_c is not None and dew_c is not None) else "N/A"
        wx_str = data.get("wxString", "")

        return {
            "station": station,
            "raw": raw_text,
            "obs_time": obs_time,
            "wind_dir": wdir,
            "wind_speed": wspd,
            "wind_gust": wgst,
            "wind_str": wind_str,
            "visibility_sm": vis_miles,
            "visibility_str": vis_str,
            "clouds": cloud_layers,
            "clouds_str": clouds_str,
            "ceiling_ft": ceiling_ft,
            "temp_c": temp_c,
            "dew_c": dew_c,
            "temp_str": temp_str,
            "dew_str": dew_str,
            "temp_dew_spread": spread_str,
            "altimeter_inhg": altim_inhg,
            "altimeter_hpa": altim_hpa,
            "altimeter_str": f"{altim_inhg:.2f} inHg ({altim_hpa} hPa)",
            "pressure_altitude": pa,
            "density_altitude": da,
            "carb_icing_risk": carb_risk,
            "weather": wx_str,
            "category": category,
            "category_color": cls.CATEGORY_COLORS.get(category, 0x95A5A6),
            "category_emoji": cls.CATEGORY_EMOJIS.get(category, "⚪")
        }

    @classmethod
    def decode_taf(cls, data: Dict[str, Any], origin_station: Optional[str] = None) -> Dict[str, Any]:
        """
        Parses AWC TAF JSON structure into structured forecast periods.
        """
        raw_taf = data.get("rawTAF") or data.get("rawText", "")
        station = data.get("icaoId") or data.get("id", "UNKNOWN")
        issue_time = data.get("issueTime", "")
        valid_from = data.get("validTimeFrom")
        valid_to = data.get("validTimeTo")
        forecast_list = data.get("forecast", [])

        decoded_forecasts = []
        for fc in forecast_list:
            fc_type = fc.get("fcstChange", "INITIAL") or "INITIAL"
            from_time = fc.get("timeFrom")
            to_time = fc.get("timeTo")
            
            # Format time window
            t_win_str = ""
            if from_time and to_time:
                try:
                    dt_from = datetime.fromtimestamp(from_time, tz=timezone.utc)
                    dt_to = datetime.fromtimestamp(to_time, tz=timezone.utc)
                    t_win_str = f"{dt_from.strftime('%H:%MZ')} - {dt_to.strftime('%H:%MZ')}"
                except Exception:
                    t_win_str = f"{from_time} - {to_time}"
            elif from_time:
                try:
                    dt_from = datetime.fromtimestamp(from_time, tz=timezone.utc)
                    t_win_str = f"From {dt_from.strftime('%H:%MZ')}"
                except Exception:
                    t_win_str = f"From {from_time}"

            # Wind
            fc_wdir = fc.get("wdir")
            fc_wspd = fc.get("wspd", 0)
            fc_wgst = fc.get("wgst")
            if fc_wdir == "VRB" or fc_wdir is None:
                fc_wind_str = f"VRB at {fc_wspd}kt" + (f" G{fc_wgst}kt" if fc_wgst else "")
            else:
                try:
                    fc_wind_str = f"{int(fc_wdir):03d}° at {int(fc_wspd)}kt" + (f" G{int(fc_wgst)}kt" if fc_wgst else "")
                except Exception:
                    fc_wind_str = f"{fc_wdir}° at {fc_wspd}kt"

            # Visibility
            fc_vis = fc.get("visib")
            if fc_vis is not None:
                try:
                    fc_vis_f = float(fc_vis)
                    fc_vis_str = f"{fc_vis_f} SM" if fc_vis_f < 6 else "6+ SM"
                except ValueError:
                    fc_vis_str = f"{fc_vis} SM"
            else:
                fc_vis_str = "6+ SM"

            # Clouds & Ceiling
            fc_clouds = fc.get("clouds", [])
            fc_ceiling = None
            fc_cloud_layers = []
            for c in fc_clouds:
                c_cov = c.get("cover", "")
                c_base = c.get("base")
                if c_base is not None:
                    fc_cloud_layers.append(f"{c_cov} {c_base}ft")
                    if c_cov in ["BKN", "OVC", "VV"] and fc_ceiling is None:
                        try:
                            fc_ceiling = int(c_base)
                        except (ValueError, TypeError):
                            pass
                else:
                    fc_cloud_layers.append(c_cov)

            fc_clouds_str = ", ".join(fc_cloud_layers) if fc_cloud_layers else "Sky Clear (SKC)"

            # Flight Category
            fc_cat = fc.get("fltcat")
            if not fc_cat:
                vis_num = 6.0
                if fc_vis is not None:
                    try:
                        vis_num = float(fc_vis)
                    except ValueError:
                        vis_num = 6.0
                fc_cat = cls.determine_flight_category(vis_num, fc_ceiling)

            decoded_forecasts.append({
                "type": fc_type,
                "time_window": t_win_str,
                "wind": fc_wind_str,
                "vis": fc_vis_str,
                "clouds": fc_clouds_str,
                "category": fc_cat,
                "category_emoji": cls.CATEGORY_EMOJIS.get(fc_cat, "⚪"),
                "raw": fc.get("rawFcst", "")
            })

        is_fallback = bool(origin_station and origin_station.upper() != station.upper())

        return {
            "station": station,
            "origin_station": origin_station or station,
            "is_nearby_fallback": is_fallback,
            "raw": raw_taf,
            "issue_time": issue_time,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "forecasts": decoded_forecasts
        }

import re
import logging
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class METARDecoder:
    # Flight Category Color Palette
    CATEGORY_COLORS = {
        "VFR": 0x00B894,   # Vivid Green
        "MVFR": 0x0984E3,  # Blue
        "IFR": 0xD63031,   # Red
        "LIFR": 0x6C5CE7   # Purple
    }

    CATEGORY_EMOJIS = {
        "VFR": "🟢",
        "MVFR": "🔵",
        "IFR": "🔴",
        "LIFR": "🟣"
    }

    @staticmethod
    def parse_altimeter_inhg(raw_metar: str, altim_field: Optional[Any] = None) -> float:
        """
        Parses altimeter setting strictly prioritizing US FAA inHg format (e.g. A2992 -> 29.92).
        Falls back to converting millibars / hPa if only metric is present.
        """
        if raw_metar:
            match = re.search(r"\bA(\d{2})(\d{2})\b", raw_metar)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
            
            # QNH format (e.g. Q1013 -> 29.92 inHg)
            qnh_match = re.search(r"\bQ(\d{4})\b", raw_metar)
            if qnh_match:
                qnh_hpa = float(qnh_match.group(1))
                return round(qnh_hpa * 0.029529983, 2)

        if altim_field is not None:
            try:
                val = float(altim_field)
                if val > 800:
                    return round(val * 0.029529983, 2)
                elif val > 20:
                    return round(val, 2)
            except (ValueError, TypeError):
                pass

        return 29.92

    @staticmethod
    def calculate_density_altitude(
        elevation_ft: float,
        temp_c: float,
        altimeter_inhg: float
    ) -> Tuple[int, int]:
        """
        Calculates Pressure Altitude and Density Altitude.
        PA = Elevation + (29.92 - Altimeter) * 1000
        Standard Temp (ISA) = 15 - (2 * (PA / 1000))
        DA = PA + 120 * (OAT - ISA_Temp)
        """
        pressure_altitude = elevation_ft + (29.92 - altimeter_inhg) * 1000.0
        isa_temp = 15.0 - (1.98 * (pressure_altitude / 1000.0))
        density_altitude = pressure_altitude + (118.8 * (temp_c - isa_temp))
        return int(round(pressure_altitude)), int(round(density_altitude))

    @staticmethod
    def determine_flight_category(visibility_sm: Optional[float], ceiling_ft: Optional[int]) -> str:
        """
        Determines FAA Flight Category:
        - LIFR: Ceiling < 500 ft and/or Visibility < 1 SM
        - IFR:  Ceiling 500 to < 1000 ft and/or Visibility 1 to < 3 SM
        - MVFR: Ceiling 1000 to 3000 ft and/or Visibility 3 to 5 SM
        - VFR:  Ceiling > 3000 ft and Visibility > 5 SM
        """
        vis = visibility_sm if visibility_sm is not None else 10.0
        ceil = ceiling_ft if ceiling_ft is not None else 10000

        if ceil < 500 or vis < 1.0:
            return "LIFR"
        elif ceil < 1000 or vis < 3.0:
            return "IFR"
        elif ceil <= 3000 or vis <= 5.0:
            return "MVFR"
        else:
            return "VFR"

    @staticmethod
    def assess_carb_icing_risk(temp_c: float, dew_c: float) -> str:
        """
        Assesses carburetor icing risk based on standard FAA / Transport Canada charts.
        """
        rel_hum = 100.0 - 5.0 * (temp_c - dew_c)
        if -10 <= temp_c <= 25 and rel_hum >= 80:
            return "🔴 **SERIOUS RISK** at glide & cruise power"
        elif -15 <= temp_c <= 30 and rel_hum >= 60:
            return "🟡 **MODERATE RISK** at cruise (Serious at glide)"
        elif -20 <= temp_c <= 35 and rel_hum >= 40:
            return "ℹ️ Low risk (Light at glide power)"
        return "🟢 Risk Unlikely"

    @classmethod
    def decode_metar(cls, data: Dict[str, Any], elevation_ft: float = 0.0) -> Dict[str, Any]:
        """
        Decodes NOAA AWC JSON METAR format into a structured aviation payload.
        """
        raw_text = data.get("rawOb") or data.get("rawText", "")
        station = data.get("icaoId") or data.get("id", "UNKNOWN")
        obs_time = data.get("obsTime")

        # Parse Winds
        raw_wdir = data.get("wdir")
        wspd = data.get("wspd", 0)
        wgst = data.get("wgst")

        try:
            wspd = float(wspd) if wspd is not None else 0.0
        except (ValueError, TypeError):
            wspd = 0.0

        try:
            wgst = float(wgst) if wgst is not None else None
        except (ValueError, TypeError):
            wgst = None

        if raw_wdir == "VRB" or raw_wdir is None:
            wdir = None
            wind_str = f"VRB at {int(wspd)}kt" + (f" G{int(wgst)}kt" if wgst else "")
        else:
            try:
                wdir = float(raw_wdir)
                wind_str = f"{int(wdir):03d}° at {int(wspd)}kt" + (f" G{int(wgst)}kt" if wgst else "")
            except (ValueError, TypeError):
                wdir = None
                wind_str = f"{raw_wdir} at {int(wspd)}kt" + (f" G{int(wgst)}kt" if wgst else "")

        # Visibility
        vis_raw = data.get("visib")
        if vis_raw is not None:
            try:
                vis_miles = float(vis_raw)
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
        
        # NOAA AWC uses 'fcsts' or 'forecast' or 'forecasts'
        forecast_list = data.get("fcsts") or data.get("forecast") or data.get("forecasts") or []

        decoded_forecasts = []
        for fc in forecast_list:
            fc_change = fc.get("fcstChange")
            prob = fc.get("probability")
            
            if fc_change:
                fc_type = str(fc_change).upper()
                if prob:
                    fc_type = f"PROB{prob} {fc_type}"
            elif prob:
                fc_type = f"PROB{prob}"
            else:
                fc_type = "INITIAL"

            from_time = fc.get("timeFrom")
            to_time = fc.get("timeTo")
            
            # Format time window
            t_win_str = ""
            if from_time and to_time:
                try:
                    dt_from = datetime.fromtimestamp(from_time, tz=timezone.utc)
                    dt_to = datetime.fromtimestamp(to_time, tz=timezone.utc)
                    t_win_str = f"{dt_from.strftime('%d%H:%M')}Z - {dt_to.strftime('%d%H:%M')}Z"
                except Exception:
                    t_win_str = f"{from_time} - {to_time}"
            elif from_time:
                try:
                    dt_from = datetime.fromtimestamp(from_time, tz=timezone.utc)
                    t_win_str = f"From {dt_from.strftime('%d%H:%M')}Z"
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
                    fc_vis_f = float(str(fc_vis).replace("+", "").replace("P", ""))
                    fc_vis_str = f"{fc_vis_f} SM" if fc_vis_f < 6 else "6+ SM"
                except ValueError:
                    fc_vis_str = f"{fc_vis} SM"
            else:
                fc_vis_str = "6+ SM"

            # Weather Phenomena (e.g. VCSH, VCTS, TSRA)
            wx_str = fc.get("wxString")

            # Clouds & Ceiling
            fc_clouds = fc.get("clouds", [])
            fc_ceiling = None
            fc_cloud_layers = []
            for c in fc_clouds:
                c_cov = c.get("cover", "")
                c_base = c.get("base")
                c_type = c.get("type") or ""
                if c_base is not None:
                    type_suffix = f" ({c_type})" if c_type else ""
                    fc_cloud_layers.append(f"{c_cov} {c_base}ft{type_suffix}")
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
                        vis_num = float(str(fc_vis).replace("+", "").replace("P", ""))
                    except ValueError:
                        vis_num = 6.0
                fc_cat = cls.determine_flight_category(vis_num, fc_ceiling)

            decoded_forecasts.append({
                "type": fc_type,
                "time_window": t_win_str,
                "wind": fc_wind_str,
                "vis": fc_vis_str,
                "weather": wx_str,
                "clouds": fc_clouds_str,
                "ceiling_ft": fc_ceiling,
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

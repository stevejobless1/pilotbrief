from typing import Dict, Any, List, Optional
from database.models import PersonalMinima

class MinimaChecker:
    @staticmethod
    def evaluate(
        metar_decoded: Dict[str, Any],
        runway_evals: List[Dict[str, Any]],
        minima: Optional[PersonalMinima] = None
    ) -> Dict[str, Any]:
        """
        Evaluates current conditions against student personal minimums.
        Returns a dict with overall pass/fail, list of warnings, and status badge.
        """
        # Default student personal minimums if none explicitly set
        max_wind = minima.max_surface_wind_kt if minima else 15
        max_cross = minima.max_crosswind_kt if minima else 10
        max_gust = minima.max_gust_factor_kt if minima else 7
        min_ceil = minima.min_ceiling_ft if minima else 2500
        min_vis = minima.min_visibility_sm if minima else 6

        warnings = []
        violations = []

        # 1. Surface wind speed check
        wspd = metar_decoded.get("wind_speed", 0)
        if wspd > max_wind:
            violations.append(f"Surface wind ({wspd}kt) exceeds student limit ({max_wind}kt)")

        # 2. Wind gust check
        wgst = metar_decoded.get("wind_gust")
        if wgst:
            gust_factor = wgst - wspd
            if gust_factor > max_gust:
                warnings.append(f"Wind gust factor (+{gust_factor}kt) exceeds comfort limit (+{max_gust}kt)")
            if wgst > max_wind:
                violations.append(f"Wind gusts ({wgst}kt) exceed max wind limit ({max_wind}kt)")

        # 3. Visibility check
        vis = metar_decoded.get("visibility_sm", 10.0)
        if vis < min_vis:
            violations.append(f"Visibility ({vis} SM) is below student minimum ({min_vis} SM)")

        # 4. Ceiling check
        ceil = metar_decoded.get("ceiling_ft")
        if ceil is not None and ceil < min_ceil:
            violations.append(f"Ceiling ({ceil}ft AGL) is below student minimum ({min_ceil}ft AGL)")

        # 5. Runway Crosswind check
        if runway_evals:
            best_rwy = runway_evals[0]
            xw = best_rwy.get("crosswind", 0.0)
            xwg = best_rwy.get("crosswind_gust")
            effective_xw = xwg if xwg is not None else xw

            if effective_xw > max_cross:
                violations.append(
                    f"Best Runway {best_rwy['runway_id']} crosswind ({effective_xw}kt) exceeds student limit ({max_cross}kt)"
                )

        is_go = len(violations) == 0

        return {
            "is_go": is_go,
            "decision": "✅ GO (Conditions within student minimums)" if is_go and not warnings else (
                "🟡 CAUTION (Within minimums, but notable factors)" if is_go else "🛑 NO-GO / REVIEW (Exceeds personal minimums)"
            ),
            "violations": violations,
            "warnings": warnings,
            "limits": {
                "max_wind": max_wind,
                "max_crosswind": max_cross,
                "max_gust": max_gust,
                "min_ceiling": min_ceil,
                "min_visibility": min_vis
            }
        }

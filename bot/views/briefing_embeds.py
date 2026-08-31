import io
import discord
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

class BriefingEmbedBuilder:
    @classmethod
    def build_briefing_embed(
        cls,
        event_title: str,
        departure_icao: str,
        destination_icao: Optional[str],
        start_time_utc: datetime,
        metar_dep: Optional[Dict[str, Any]],
        metar_dest: Optional[Dict[str, Any]],
        taf_dep: Optional[Dict[str, Any]],
        runway_evals: List[Dict[str, Any]],
        minima_eval: Dict[str, Any],
        notams: List[Dict[str, Any]],
        sigmets: List[Dict[str, Any]],
        milestone_label: str = "FLIGHT BRIEFING"
    ) -> discord.Embed:
        """
        Constructs a comprehensive, rich Discord Embed for student pilots.
        """
        dep_cat = metar_dep.get("category", "VFR") if metar_dep else "VFR"
        dep_color = metar_dep.get("category_color", 0x2ECC71) if metar_dep else 0x2ECC71
        dep_emoji = metar_dep.get("category_emoji", "🟢") if metar_dep else "🟢"

        route_str = f"{departure_icao}" + (f" ➔ {destination_icao}" if destination_icao else " (Local Flight)")
        
        embed = discord.Embed(
            title=f"✈️ {milestone_label} | {route_str}",
            description=(
                f"**Flight Lesson:** {event_title}\n"
                f"**Departure Time:** <t:{int(start_time_utc.timestamp())}:F> (<t:{int(start_time_utc.timestamp())}:R>)\n"
                f"**Go / No-Go Assessment:** {minima_eval.get('decision', 'N/A')}"
            ),
            color=dep_color,
            timestamp=datetime.utcnow()
        )

        # 1. Student Personal Minimums Callouts
        violations = minima_eval.get("violations", [])
        warnings = minima_eval.get("warnings", [])
        if violations:
            embed.add_field(
                name="🛑 PERSONAL MINIMUMS EXCEEDED",
                value="\n".join([f"• ⚠️ **{v}**" for v in violations]),
                inline=False
            )
        elif warnings:
            embed.add_field(
                name="🟡 CAUTIONARY FACTORS",
                value="\n".join([f"• ℹ️ {w}" for w in warnings]),
                inline=False
            )

        # 2. Departure METAR & Decoded Conditions
        if metar_dep:
            pa = metar_dep.get("pressure_altitude", 0)
            da = metar_dep.get("density_altitude", 0)
            altim_str = f"{metar_dep.get('altimeter_inhg', 29.92):.2f} inHg ({metar_dep.get('altimeter_hpa', 1013)} hPa)"
            
            dep_val = (
                f"**Flight Category:** {dep_emoji} **{metar_dep['category']}**\n"
                f"**Surface Winds:** `{metar_dep['wind_str']}`\n"
                f"**Visibility:** `{metar_dep['visibility_str']}` | **Clouds/Ceiling:** `{metar_dep['clouds_str']}`\n"
                f"**Temp / Dewpoint:** `{metar_dep['temp_str']} / {metar_dep['dew_str']}` (Spread: `{metar_dep['temp_dew_spread']}`)\n"
                f"**Altimeter:** `{altim_str}`\n"
                f"**Density Altitude:** `{da:,} ft` (Pressure Alt: `{pa:,} ft`)\n"
                f"**Carb Icing Risk:** {metar_dep['carb_icing_risk']}\n"
                f"```{metar_dep['raw']}```"
            )
            embed.add_field(name=f"📍 Departure Weather ({departure_icao})", value=dep_val, inline=False)
        else:
            embed.add_field(name=f"📍 Departure Weather ({departure_icao})", value="*METAR currently unavailable.*", inline=False)

        # 3. Runway Crosswind Component Analysis
        if runway_evals:
            rwy_lines = []
            for i, r in enumerate(runway_evals):
                pref_tag = "⭐ **FAVORABLE** " if i == 0 else ""
                hw_tw = f"{r['headwind']}kt Headwind" if r['headwind'] > 0 else (f"{r['tailwind']}kt Tailwind" if r['tailwind'] > 0 else "0kt Direct X-Wind")
                xw_str = f"{r['crosswind']}kt {r['crosswind_side']} X-Wind"
                if r.get("crosswind_gust"):
                    xw_str += f" (Gusts to {r['crosswind_gust']}kt)"
                rwy_lines.append(f"{pref_tag}**Rwy {r['runway_id']}** ({int(r['heading']):03d}°): {hw_tw} • {xw_str}")
            embed.add_field(name="🛫 Runway & Crosswind Analysis", value="\n".join(rwy_lines), inline=False)

        # 4. Destination METAR (if cross-country)
        if destination_icao and metar_dest:
            dest_emoji = metar_dest.get("category_emoji", "🟢")
            dest_altim = f"{metar_dest.get('altimeter_inhg', 29.92):.2f} inHg"
            dest_val = (
                f"**Flight Category:** {dest_emoji} **{metar_dest['category']}** | **Winds:** `{metar_dest['wind_str']}`\n"
                f"**Visibility:** `{metar_dest['visibility_str']}` | **Clouds:** `{metar_dest['clouds_str']}`\n"
                f"**Altimeter:** `{dest_altim}` | **Density Alt:** `{metar_dest.get('density_altitude', 0):,} ft`\n"
                f"```{metar_dest['raw']}```"
            )
            embed.add_field(name=f"🏁 Destination Weather ({destination_icao})", value=dest_val, inline=False)

        # 5. Terminal Aerodrome Forecast (TAF)
        if taf_dep:
            station_lbl = taf_dep.get("station", departure_icao)
            is_fallback = taf_dep.get("is_nearby_fallback", False)
            taf_header = f"🔮 Terminal Aerodrome Forecast ({station_lbl}" + (f" - Nearby station for {departure_icao})" if is_fallback else ")")
            
            taf_body = []
            if taf_dep.get("forecasts"):
                for fc in taf_dep["forecasts"][:4]:
                    fc_type = f"**{fc['type']}**" if fc['type'] != "INITIAL" else "**INITIAL**"
                    taf_body.append(
                        f"• {fc['category_emoji']} `{fc['time_window']}` {fc_type}: Wind `{fc['wind']}`, Vis `{fc['vis']}`, Clouds: `{fc['clouds']}`"
                    )
            
            raw_taf_snippet = f"```{taf_dep.get('raw', '')}```"
            embed.add_field(
                name=taf_header,
                value=("\n".join(taf_body) if taf_body else "No forecast periods available") + f"\n{raw_taf_snippet}",
                inline=False
            )
        else:
            embed.add_field(name=f"🔮 Terminal Aerodrome Forecast ({departure_icao})", value="*No TAF issued for this station or nearby reporting stations.*", inline=False)

        # 6. Active SIGMETs & AIRMETs in Region
        if sigmets:
            sig_lines = []
            for s in sigmets[:4]:
                props = s.get("properties", {})
                hazard = props.get("hazard", "Hazard")
                top = props.get("altitudeHi1") or props.get("top")
                top_str = f" (Tops: FL{int(top/100)})" if top else ""
                sig_lines.append(f"• ⚠️ **{hazard}**{top_str}")
            if sig_lines:
                embed.add_field(name="⛈️ Active SIGMETs / AIRMETs in Region", value="\n".join(sig_lines), inline=False)

        # 7. Notable NOTAM Highlights
        if notams:
            notam_highlights = [n for n in notams if n["priority"] in ["CRITICAL", "HIGH"]]
            if notam_highlights:
                n_lines = [f"• [{n['category']}] {n['text'][:130]}..." for n in notam_highlights[:3]]
                embed.add_field(name="📢 Notable NOTAMs", value="\n".join(n_lines), inline=False)

        embed.set_footer(text="PilotBrief • Student Pilot Briefing System • Verify official briefing via 1-800-WX-BRIEF / Leidos")
        return embed

    @classmethod
    def build_convective_alert_embed(
        cls,
        icao: str,
        hazard_info: Dict[str, Any]
    ) -> discord.Embed:
        """
        Constructs an urgent, high-priority Convective SIGMET alert embed.
        """
        is_overhead = hazard_info.get("is_overhead", True)
        dist_nm = hazard_info.get("distance_nm", 0.0)
        pos_str = "🚨 **DIRECTLY OVER AIRPORT**" if is_overhead else f"⚠️ **{dist_nm:.1f} NM from Airport Boundary**"
        tops = hazard_info.get("top_fl", "FL450+")
        raw_text = hazard_info.get("raw_text", "N/A")

        embed = discord.Embed(
            title=f"⚡ URGENT: CONVECTIVE SIGMET OVER {icao}",
            description=(
                f"A **Convective SIGMET** has been issued by NOAA AWC affecting **{icao}**.\n\n"
                f"**Position:** {pos_str}\n"
                f"**Storm Tops:** `{tops}`\n"
                f"**Status:** 🛑 **NO-GO FOR VFR STUDENT FLIGHTS**\n"
            ),
            color=0xD63031,  # Emergency Red
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="🌪️ Associated Severe Hazards",
            value=(
                "• **Severe / Extreme Turbulence**\n"
                "• **Microbursts & Low-Level Wind Shear**\n"
                "• **Hail & Torrential Precipitation**\n"
                "• **Severe Icing & Lightning**"
            ),
            inline=False
        )

        if raw_text:
            embed.add_field(
                name="📋 Official NOAA SIGMET Bulletin",
                value=f"```{raw_text[:800]}```",
                inline=False
            )

        embed.set_footer(text="PilotBrief Real-Time Airspace Monitor • Check radar & official briefing prior to flight")
        return embed

class BriefingView(discord.ui.View):
    def __init__(
        self,
        raw_metar: str,
        raw_taf: str,
        tactical_map_bytes: Optional[bytes] = None,
        sectional_map_bytes: Optional[bytes] = None
    ):
        super().__init__(timeout=3600)
        self.raw_metar = raw_metar
        self.raw_taf = raw_taf
        self.tactical_map_bytes = tactical_map_bytes
        self.sectional_map_bytes = sectional_map_bytes

    @discord.ui.button(label="100 NM Sectional View", style=discord.ButtonStyle.primary, emoji="🗺️")
    async def show_sectional(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.sectional_map_bytes:
            file = discord.File(io.BytesIO(self.sectional_map_bytes), filename="sectional_100nm.png")
            emb = discord.Embed(title="🗺️ ForeFlight VFR Sectional & Regional Weather (100 NM)", color=0x0984E3)
            emb.set_image(url="attachment://sectional_100nm.png")
            await interaction.response.send_message(embed=emb, file=file, ephemeral=True)
        else:
            await interaction.response.send_message("Sectional map view unavailable.", ephemeral=True)

    @discord.ui.button(label="Tactical Radar View", style=discord.ButtonStyle.secondary, emoji="📡")
    async def show_tactical(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.tactical_map_bytes:
            file = discord.File(io.BytesIO(self.tactical_map_bytes), filename="tactical_radar.png")
            emb = discord.Embed(title="📡 PilotBrief Tactical Radar & Airspace View", color=0x2ECC71)
            emb.set_image(url="attachment://tactical_radar.png")
            await interaction.response.send_message(embed=emb, file=file, ephemeral=True)
        else:
            await interaction.response.send_message("Tactical radar view unavailable.", ephemeral=True)

    @discord.ui.button(label="Raw METAR / TAF", style=discord.ButtonStyle.secondary, emoji="📋")
    async def show_raw(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = f"**Raw METAR:**\n```{self.raw_metar or 'N/A'}```\n**Raw TAF:**\n```{self.raw_taf or 'N/A'}```"
        await interaction.response.send_message(content, ephemeral=True)

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
        Constructs a compact, high-density, professional Discord Embed for student pilots.
        Compresses layout to eliminate clutter while retaining 100% of weather and flight data.
        """
        dep_cat = metar_dep.get("category", "VFR") if metar_dep else "VFR"
        dep_color = metar_dep.get("category_color", 0x2ECC71) if metar_dep else 0x2ECC71
        dep_emoji = metar_dep.get("category_emoji", "🟢") if metar_dep else "🟢"

        route_str = f"{departure_icao}" + (f" ➔ {destination_icao}" if destination_icao else " (Local)")
        
        # 1. Compact Header Description
        ts = int(start_time_utc.timestamp())
        decision = minima_eval.get("decision", "N/A")
        desc_lines = [
            f"📋 **{event_title}** • 🛫 <t:{ts}:t> (<t:{ts}:R>)",
            f"🎯 **Status:** {decision}"
        ]

        embed = discord.Embed(
            title=f"✈️ {milestone_label} • {route_str}",
            description="\n".join(desc_lines),
            color=dep_color,
            timestamp=datetime.utcnow()
        )

        # 2. Student Personal Minimums Callouts (if any violations or warnings)
        violations = minima_eval.get("violations", [])
        warnings = minima_eval.get("warnings", [])
        if violations:
            embed.add_field(
                name="🛑 Personal Minimums Exceeded",
                value="\n".join([f"• ⚠️ **{v}**" for v in violations]),
                inline=False
            )
        elif warnings:
            embed.add_field(
                name="🟡 Cautionary Factors",
                value="\n".join([f"• ℹ️ {w}" for w in warnings]),
                inline=False
            )

        # 3. Departure Weather (Compact 4-line high-density block)
        if metar_dep:
            pa = metar_dep.get("pressure_altitude", 0)
            da = metar_dep.get("density_altitude", 0)
            altim_str = f"{metar_dep.get('altimeter_inhg', 29.92):.2f}\""
            hpa = metar_dep.get("altimeter_hpa", 1013)
            
            dep_lines = [
                f"• **Wind:** `{metar_dep['wind_str']}`",
                f"• **Vis & Sky:** `{metar_dep['visibility_str']}` • `{metar_dep['clouds_str']}`",
                f"• **Temp / Dew:** `{metar_dep['temp_str']} / {metar_dep['dew_str']}` (Spread: `{metar_dep['temp_dew_spread']}`) • **Carb Risk:** {metar_dep['carb_icing_risk']}",
                f"• **Altimeter:** `{altim_str}` ({hpa} hPa) • **DA:** `{da:,} ft` (PA: `{pa:,} ft`)"
            ]
            embed.add_field(
                name=f"📍 Departure ({departure_icao}) — {dep_emoji} **{metar_dep['category']}**",
                value="\n".join(dep_lines),
                inline=False
            )
        else:
            embed.add_field(
                name=f"📍 Departure ({departure_icao})",
                value="*METAR currently unavailable.*",
                inline=False
            )

        # 4. Runway Crosswind Component Analysis
        if runway_evals:
            rwy_lines = []
            for i, r in enumerate(runway_evals[:4]):
                pref = "⭐ " if i == 0 else "• "
                hw_tw = f"{r['headwind']}kt HW" if r['headwind'] > 0 else (f"{r['tailwind']}kt TW" if r['tailwind'] > 0 else "0kt Calm")
                side = "R" if r['crosswind_side'] == "Right" else ("L" if r['crosswind_side'] == "Left" else r['crosswind_side'])
                xw_str = f"{r['crosswind']}kt {side}-XW" if r['crosswind_side'] in ["Right", "Left"] else f"{r['crosswind']}kt XW"
                if r.get("crosswind_gust"):
                    xw_str += f" (G{r['crosswind_gust']}kt)"
                hdg = f"{int(r['heading']):03d}°" if 'heading' in r else ""
                rwy_lines.append(f"{pref}**Rwy {r['runway_id']}** ({hdg}): {hw_tw} • {xw_str}")
            embed.add_field(
                name=f"🛫 Runway & Wind Analysis ({departure_icao})",
                value="\n".join(rwy_lines),
                inline=False
            )

        # 5. Destination Weather (if cross-country)
        if destination_icao and metar_dest:
            dest_emoji = metar_dest.get("category_emoji", "🟢")
            dest_altim = f"{metar_dest.get('altimeter_inhg', 29.92):.2f}\""
            dest_lines = [
                f"• **Wind:** `{metar_dest['wind_str']}` • **Vis:** `{metar_dest['visibility_str']}`",
                f"• **Clouds / Sky:** `{metar_dest['clouds_str']}`",
                f"• **Altimeter:** `{dest_altim}` • **DA:** `{metar_dest.get('density_altitude', 0):,} ft`"
            ]
            embed.add_field(
                name=f"🏁 Destination ({destination_icao}) — {dest_emoji} **{metar_dest['category']}**",
                value="\n".join(dest_lines),
                inline=False
            )

        # 6. Terminal Aerodrome Forecast (TAF)
        if taf_dep:
            station_lbl = taf_dep.get("station", departure_icao)
            is_fallback = taf_dep.get("is_nearby_fallback", False)
            header_extra = f" (Nearby for {departure_icao})" if is_fallback else ""
            taf_header = f"🔮 Forecast / TAF ({station_lbl}{header_extra})"
            
            taf_body = []
            if taf_dep.get("forecasts"):
                for fc in taf_dep["forecasts"][:4]:
                    fc_type = fc['type']
                    wx_str = f" • {fc['weather']}" if fc.get('weather') else ""
                    taf_body.append(
                        f"• {fc['category_emoji']} `{fc['time_window']}` **{fc_type}**: `{fc['wind']}` • Vis `{fc['vis']}`{wx_str} • `{fc['clouds']}`"
                    )
            embed.add_field(
                name=taf_header,
                value="\n".join(taf_body) if taf_body else "No forecast periods available.",
                inline=False
            )
        else:
            embed.add_field(
                name=f"🔮 Forecast / TAF ({departure_icao})",
                value="*No TAF issued for this station.*",
                inline=False
            )

        # 7. Active SIGMETs & AIRMETs in Region
        if sigmets:
            sig_lines = []
            for s in sigmets[:3]:
                props = s.get("properties", {})
                hazard = props.get("hazard", "Hazard")
                top = props.get("altitudeHi1") or props.get("top")
                top_str = f" (Tops: FL{int(top/100)})" if top else ""
                sig_lines.append(f"• ⚠️ **{hazard}**{top_str}")
            if sig_lines:
                embed.add_field(name="⛈️ Active SIGMETs / AIRMETs", value="\n".join(sig_lines), inline=False)

        # 8. Notable NOTAM Highlights
        if notams:
            notam_highlights = [n for n in notams if n.get("priority") in ["CRITICAL", "HIGH"]]
            if notam_highlights:
                n_lines = [
                    f"• **[{n['category']}]** {n['text'][:110]}..." if len(n['text']) > 110 else f"• **[{n['category']}]** {n['text']}"
                    for n in notam_highlights[:3]
                ]
                embed.add_field(name="📢 Key NOTAM Highlights", value="\n".join(n_lines), inline=False)

        embed.set_footer(text="PilotBrief • Student Pilot Briefing • Tap 'Raw METAR / TAF' below for raw bulletins")
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
        sectional_map_bytes: Optional[bytes] = None,
        raw_dest_metar: Optional[str] = None
    ):
        super().__init__(timeout=3600)
        self.raw_metar = raw_metar
        self.raw_taf = raw_taf
        self.tactical_map_bytes = tactical_map_bytes
        self.sectional_map_bytes = sectional_map_bytes
        self.raw_dest_metar = raw_dest_metar

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
        parts = []
        if self.raw_metar:
            parts.append(f"**Departure METAR:**\n```{self.raw_metar}```")
        if self.raw_dest_metar:
            parts.append(f"**Destination METAR:**\n```{self.raw_dest_metar}```")
        if self.raw_taf:
            parts.append(f"**Terminal Forecast (TAF):**\n```{self.raw_taf}```")
        if not parts:
            parts.append("*No raw weather bulletins available.*")
        await interaction.response.send_message("\n\n".join(parts), ephemeral=True)

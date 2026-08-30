import discord
from typing import Dict, Any, List, Optional
from datetime import datetime

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
        Constructs a Discord Embed containing aviation weather briefing.
        """
        # Determine embed color based on departure flight category
        dep_cat = metar_dep.get("category", "VFR") if metar_dep else "VFR"
        dep_color = metar_dep.get("category_color", 0x2ECC71) if metar_dep else 0x2ECC71
        dep_emoji = metar_dep.get("category_emoji", "🟢") if metar_dep else "🟢"

        route_str = f"{departure_icao}" + (f" ➔ {destination_icao}" if destination_icao else " (Local Flight)")
        
        embed = discord.Embed(
            title=f"✈️ {milestone_label} | {route_str}",
            description=f"**Flight Event:** {event_title}\n**Departure Time:** <t:{int(start_time_utc.timestamp())}:F> (<t:{int(start_time_utc.timestamp())}:R>)\n**Assessment:** {minima_eval.get('decision', 'N/A')}",
            color=dep_color,
            timestamp=datetime.utcnow()
        )

        # 1. Student Minimums & Decision Alerts
        violations = minima_eval.get("violations", [])
        warnings = minima_eval.get("warnings", [])
        if violations:
            embed.add_field(
                name="🛑 PERSONAL MINIMUMS EXCEEDED",
                value="\n".join([f"• ⚠️ {v}" for v in violations]),
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
            dep_val = (
                f"**Category:** {dep_emoji} **{metar_dep['category']}**\n"
                f"**Winds:** `{metar_dep['wind_str']}`\n"
                f"**Visibility:** `{metar_dep['visibility_str']}` | **Ceiling:** `{metar_dep['clouds_str']}`\n"
                f"**Temp/Dew:** `{metar_dep['temp_str']} / {metar_dep['dew_str']}` (Spread: `{metar_dep['temp_dew_spread']}`)\n"
                f"**Altimeter:** `{metar_dep['altimeter']:.2f} inHg` | **Density Alt:** `{metar_dep['density_altitude']} ft`\n"
                f"**Carb Icing:** {metar_dep['carb_icing_risk']}\n"
                f"```{metar_dep['raw']}```"
            )
            embed.add_field(name=f"📍 Departure Weather ({departure_icao})", value=dep_val, inline=False)
        else:
            embed.add_field(name=f"📍 Departure Weather ({departure_icao})", value="*METAR currently unavailable.*", inline=False)

        # 3. Runway Crosswind Breakdown
        if runway_evals:
            rwy_lines = []
            for i, r in enumerate(runway_evals[:3]):  # Top 3 most favorable runways
                pref_tag = "⭐ **FAVORABLE** " if i == 0 else ""
                hw_tw = f"{r['headwind']}kt Headwind" if r['headwind'] > 0 else (f"{r['tailwind']}kt Tailwind" if r['tailwind'] > 0 else "Calm")
                xw_str = f"{r['crosswind']}kt {r['crosswind_side']} X-Wind" + (f" (Gusts {r['crosswind_gust']}kt)" if r.get("crosswind_gust") else "")
                rwy_lines.append(f"{pref_tag}**Rwy {r['runway_id']}** ({int(r['heading']):03d}°): {hw_tw} | {xw_str}")
            embed.add_field(name="🛫 Runway & Crosswind Analysis", value="\n".join(rwy_lines), inline=False)

        # 4. Destination METAR (if cross country)
        if destination_icao and metar_dest:
            dest_emoji = metar_dest.get("category_emoji", "🟢")
            dest_val = (
                f"**Category:** {dest_emoji} **{metar_dest['category']}** | **Winds:** `{metar_dest['wind_str']}`\n"
                f"**Visibility:** `{metar_dest['visibility_str']}` | **Clouds:** `{metar_dest['clouds_str']}`\n"
                f"```{metar_dest['raw']}```"
            )
            embed.add_field(name=f"🏁 Destination Weather ({destination_icao})", value=dest_val, inline=False)

        # 5. TAF Forecast
        if taf_dep and taf_dep.get("forecasts"):
            taf_lines = []
            for fc in taf_dep["forecasts"][:3]:
                taf_lines.append(f"• `{fc['type']}`: Wind {fc['wind']}, Vis {fc['vis']}, Clouds: {fc['clouds']}")
            embed.add_field(name=f"🔮 TAF Forecast Trend ({departure_icao})", value="\n".join(taf_lines) or "No active changes", inline=False)

        # 6. Active SIGMETs / Convective Warnings
        if sigmets:
            sig_lines = []
            for s in sigmets[:3]:
                props = s.get("properties", {})
                hazard = props.get("hazard", "Hazard")
                top = props.get("top", "N/A")
                sig_lines.append(f"• ⚠️ **{hazard}** (Tops: {top}ft)")
            embed.add_field(name="⛈️ Active SIGMETs in Region", value="\n".join(sig_lines), inline=False)

        # 7. Notable NOTAM Highlights
        if notams:
            notam_highlights = [n for n in notams if n["priority"] in ["CRITICAL", "HIGH"]]
            if notam_highlights:
                n_lines = [f"• [{n['category']}] {n['text'][:120]}..." for n in notam_highlights[:3]]
                embed.add_field(name="📢 Notable NOTAMs", value="\n".join(n_lines), inline=False)

        embed.set_footer(text="PilotBrief • Safety First • Verify official briefing via 1-800-WX-BRIEF / Leidos")
        return embed

class BriefingView(discord.ui.View):
    def __init__(self, raw_metar: str, raw_taf: str):
        super().__init__(timeout=3600)
        self.raw_metar = raw_metar
        self.raw_taf = raw_taf

    @discord.ui.button(label="Raw METAR / TAF", style=discord.ButtonStyle.secondary, emoji="📋")
    async def show_raw(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = f"**Raw METAR:**\n```{self.raw_metar or 'N/A'}```\n**Raw TAF:**\n```{self.raw_taf or 'N/A'}```"
        await interaction.response.send_message(content, ephemeral=True)

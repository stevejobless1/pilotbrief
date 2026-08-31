import io
import logging
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from sqlalchemy import select, and_

from database.session import AsyncSessionLocal
from database.models import UserSettings, FlightEvent, PersonalMinima
from services.weather.awc_client import awc_client
from services.weather.decoder import METARDecoder
from services.weather.crosswind import CrosswindCalculator, airport_db
from services.weather.minima_checker import MinimaChecker
from services.weather.sigmet_monitor import sigmet_monitor
from services.notam.notam_client import notam_client
from services.radar.map_generator import radar_map_generator
from services.calendar.ical_service import calendar_service
from bot.views.briefing_embeds import BriefingEmbedBuilder, BriefingView

logger = logging.getLogger(__name__)

class FlightCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    flight_group = app_commands.Group(name="flight", description="Aviation weather & flight lesson commands")

    @flight_group.command(name="brief", description="Get an on-demand weather briefing & ForeFlight sectional map for any airport(s)")
    @app_commands.describe(
        departure="Departure Airport ICAO (e.g. KPAO, KSFO, KRYN)",
        destination="Optional Destination Airport ICAO (e.g. KTUS, KSTS)"
    )
    async def brief(self, interaction: discord.Interaction, departure: str, destination: str = None):
        await interaction.response.defer(thinking=True)
        try:
            dep_icao = departure.strip().upper()
            dest_icao = destination.strip().upper() if destination else None

            # 1. Fetch Departure METAR & Best TAF
            dep_elev = airport_db.get_elevation(dep_icao)
            raw_metar_dep = await awc_client.get_metar(dep_icao)
            decoded_metar_dep = METARDecoder.decode_metar(raw_metar_dep, dep_elev) if raw_metar_dep else None

            raw_taf_dep, taf_station, taf_dist = await awc_client.get_best_taf(dep_icao)
            decoded_taf_dep = METARDecoder.decode_taf(raw_taf_dep, origin_station=dep_icao) if raw_taf_dep else None

            # 2. Fetch Destination METAR
            decoded_metar_dest = None
            if dest_icao:
                dest_elev = airport_db.get_elevation(dest_icao)
                raw_metar_dest = await awc_client.get_metar(dest_icao)
                if raw_metar_dest:
                    decoded_metar_dest = METARDecoder.decode_metar(raw_metar_dest, dest_elev)

            # 3. Runway Crosswinds
            runway_evals = []
            if decoded_metar_dep:
                runway_evals = CrosswindCalculator.evaluate_airport_runways(
                    dep_icao,
                    decoded_metar_dep.get("wind_dir"),
                    decoded_metar_dep.get("wind_speed", 0),
                    decoded_metar_dep.get("wind_gust")
                )

            # 4. Personal Minima
            async with AsyncSessionLocal() as session:
                minima = await session.get(PersonalMinima, interaction.user.id)

            minima_eval = MinimaChecker.evaluate(decoded_metar_dep or {}, runway_evals, minima)

            # 5. SIGMETs & NOTAMs
            sigmets = await awc_client.get_sigmets()
            notams = await notam_client.get_notams_for_station(dep_icao)

            # 6. Generate Maps (100 NM ForeFlight Sectional & Tactical Radar)
            dep_cat = decoded_metar_dep.get("category", "VFR") if decoded_metar_dep else "VFR"
            dest_cat = decoded_metar_dest.get("category", "VFR") if decoded_metar_dest else None
            
            sectional_map_bytes = None
            tactical_map_bytes = None
            try:
                sectional_map_bytes, tactical_map_bytes = await asyncio.gather(
                    radar_map_generator.generate_sectional_overview_map(
                        dep_icao=dep_icao,
                        dest_icao=dest_icao,
                        dep_fltcat=dep_cat,
                        dest_fltcat=dest_cat,
                        sigmets=sigmets,
                        radius_nm=95.0
                    ),
                    radar_map_generator.generate_briefing_map(
                        dep_icao=dep_icao,
                        dest_icao=dest_icao,
                        dep_fltcat=dep_cat,
                        dest_fltcat=dest_cat,
                        sigmets=sigmets,
                        radius_nm=50.0
                    ),
                    return_exceptions=True
                )
                if not isinstance(sectional_map_bytes, bytes):
                    sectional_map_bytes = None
                if not isinstance(tactical_map_bytes, bytes):
                    tactical_map_bytes = None
            except Exception as me:
                logger.error(f"Error rendering maps for {dep_icao}: {me}")

            embed = BriefingEmbedBuilder.build_briefing_embed(
                event_title="On-Demand Weather Briefing",
                departure_icao=dep_icao,
                destination_icao=dest_icao,
                start_time_utc=datetime.utcnow(),
                metar_dep=decoded_metar_dep,
                metar_dest=decoded_metar_dest,
                taf_dep=decoded_taf_dep,
                runway_evals=runway_evals,
                minima_eval=minima_eval,
                notams=notams,
                sigmets=sigmets,
                milestone_label="INSTANT WEATHER BRIEFING"
            )

            primary_bytes = sectional_map_bytes or tactical_map_bytes
            file = discord.File(io.BytesIO(primary_bytes), filename="sectional_overview.png") if primary_bytes else None
            if file:
                embed.set_image(url="attachment://sectional_overview.png")

            view = BriefingView(
                raw_metar=decoded_metar_dep.get("raw", "") if decoded_metar_dep else "",
                raw_taf=decoded_taf_dep.get("raw", "") if decoded_taf_dep else "",
                tactical_map_bytes=tactical_map_bytes,
                sectional_map_bytes=sectional_map_bytes,
                raw_dest_metar=decoded_metar_dest.get("raw", "") if decoded_metar_dest else None
            )

            if file:
                await interaction.followup.send(embed=embed, file=file, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Error handling /flight brief command: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ Error generating flight briefing: `{str(e)}`", ephemeral=True)

    @flight_group.command(name="sigmet", description="Check active Convective SIGMETs and thunderstorm hazards over any airport")
    @app_commands.describe(airport="Airport ICAO code (e.g. KPAO, KRYN, KSFO)")
    async def check_sigmet(self, interaction: discord.Interaction, airport: str = None):
        await interaction.response.defer(thinking=True)
        try:
            if not airport:
                async with AsyncSessionLocal() as session:
                    user = await session.get(UserSettings, interaction.user.id)
                    icao = user.home_icao.upper() if user and user.home_icao else "KPAO"
            else:
                icao = airport.strip().upper()

            sigmets = await awc_client.get_sigmets()
            hazards = sigmet_monitor.evaluate_airport_convective_hazards(icao, sigmets, proximity_nm=30.0)

            if not hazards:
                embed = discord.Embed(
                    title=f"✅ Airspace Clear: No Convective SIGMETs over {icao}",
                    description=f"There are currently **no active Convective SIGMETs or severe thunderstorm areas** within 30 NM of **{icao}**.",
                    color=0x2ECC71,
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="PilotBrief Airspace Monitor • Always verify radar & official briefing")
                await interaction.followup.send(embed=embed)
                return

            # If hazard found
            h = hazards[0]
            embed = BriefingEmbedBuilder.build_convective_alert_embed(icao, h)
            map_bytes = await radar_map_generator.generate_sectional_overview_map(
                dep_icao=icao,
                dep_fltcat="IFR",
                sigmets=sigmets,
                radius_nm=85.0
            )

            file = discord.File(io.BytesIO(map_bytes), filename="convective_sigmet.png") if map_bytes else None
            if file:
                embed.set_image(url="attachment://convective_sigmet.png")
                await interaction.followup.send(embed=embed, file=file)
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error checking SIGMETs: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ Error checking SIGMETs: `{e}`", ephemeral=True)

    @flight_group.command(name="next", description="Generate a briefing for your next upcoming scheduled flight lesson")
    async def next_flight(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            now = datetime.utcnow()

            async with AsyncSessionLocal() as session:
                stmt = select(FlightEvent).where(
                    and_(
                        FlightEvent.discord_user_id == interaction.user.id,
                        FlightEvent.start_time > now
                    )
                ).order_by(FlightEvent.start_time.asc()).limit(1)
                
                event = (await session.execute(stmt)).scalar_one_or_none()

            if not event:
                await interaction.followup.send(
                    "📅 No upcoming flight lessons found in your synced calendar. "
                    "Use `/flight sync` to sync your calendar or `/flight brief <ICAO>` for an instant briefing.",
                    ephemeral=True
                )
                return

            dep_icao = event.departure_icao
            dest_icao = event.destination_icao

            dep_elev = airport_db.get_elevation(dep_icao)
            raw_metar_dep = await awc_client.get_metar(dep_icao)
            decoded_metar_dep = METARDecoder.decode_metar(raw_metar_dep, dep_elev) if raw_metar_dep else None

            raw_taf_dep, taf_station, taf_dist = await awc_client.get_best_taf(dep_icao)
            decoded_taf_dep = METARDecoder.decode_taf(raw_taf_dep, origin_station=dep_icao) if raw_taf_dep else None

            decoded_metar_dest = None
            if dest_icao:
                dest_elev = airport_db.get_elevation(dest_icao)
                raw_metar_dest = await awc_client.get_metar(dest_icao)
                if raw_metar_dest:
                    decoded_metar_dest = METARDecoder.decode_metar(raw_metar_dest, dest_elev)

            runway_evals = []
            if decoded_metar_dep:
                runway_evals = CrosswindCalculator.evaluate_airport_runways(
                    dep_icao,
                    decoded_metar_dep.get("wind_dir"),
                    decoded_metar_dep.get("wind_speed", 0),
                    decoded_metar_dep.get("wind_gust")
                )

            async with AsyncSessionLocal() as session:
                minima = await session.get(PersonalMinima, interaction.user.id)

            minima_eval = MinimaChecker.evaluate(decoded_metar_dep or {}, runway_evals, minima)
            sigmets = await awc_client.get_sigmets()
            notams = await notam_client.get_notams_for_station(dep_icao)

            dep_cat = decoded_metar_dep.get("category", "VFR") if decoded_metar_dep else "VFR"
            dest_cat = decoded_metar_dest.get("category", "VFR") if decoded_metar_dest else None
            
            sectional_map_bytes = None
            tactical_map_bytes = None
            try:
                sectional_map_bytes, tactical_map_bytes = await asyncio.gather(
                    radar_map_generator.generate_sectional_overview_map(
                        dep_icao=dep_icao,
                        dest_icao=dest_icao,
                        dep_fltcat=dep_cat,
                        dest_fltcat=dest_cat,
                        sigmets=sigmets,
                        radius_nm=95.0
                    ),
                    radar_map_generator.generate_briefing_map(
                        dep_icao=dep_icao,
                        dest_icao=dest_icao,
                        dep_fltcat=dep_cat,
                        dest_fltcat=dest_cat,
                        sigmets=sigmets,
                        radius_nm=50.0
                    ),
                    return_exceptions=True
                )
                if not isinstance(sectional_map_bytes, bytes):
                    sectional_map_bytes = None
                if not isinstance(tactical_map_bytes, bytes):
                    tactical_map_bytes = None
            except Exception as me:
                logger.error(f"Error rendering maps for {dep_icao}: {me}")

            embed = BriefingEmbedBuilder.build_briefing_embed(
                event_title=event.summary,
                departure_icao=dep_icao,
                destination_icao=dest_icao,
                start_time_utc=event.start_time,
                metar_dep=decoded_metar_dep,
                metar_dest=decoded_metar_dest,
                taf_dep=decoded_taf_dep,
                runway_evals=runway_evals,
                minima_eval=minima_eval,
                notams=notams,
                sigmets=sigmets,
                milestone_label="NEXT SCHEDULED FLIGHT BRIEFING"
            )

            primary_bytes = sectional_map_bytes or tactical_map_bytes
            file = discord.File(io.BytesIO(primary_bytes), filename="sectional_overview.png") if primary_bytes else None
            if file:
                embed.set_image(url="attachment://sectional_overview.png")

            view = BriefingView(
                raw_metar=decoded_metar_dep.get("raw", "") if decoded_metar_dep else "",
                raw_taf=decoded_taf_dep.get("raw", "") if decoded_taf_dep else "",
                tactical_map_bytes=tactical_map_bytes,
                sectional_map_bytes=sectional_map_bytes,
                raw_dest_metar=decoded_metar_dest.get("raw", "") if decoded_metar_dest else None
            )

            if file:
                await interaction.followup.send(embed=embed, file=file, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Error handling /flight next command: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ Error retrieving next flight: `{str(e)}`", ephemeral=True)

    @flight_group.command(name="home", description="Set your default home departure airport")
    @app_commands.describe(icao="ICAO code for your primary airport (e.g. KPAO)")
    async def set_home(self, interaction: discord.Interaction, icao: str):
        try:
            icao = icao.strip().upper()
            async with AsyncSessionLocal() as session:
                user = await session.get(UserSettings, interaction.user.id)
                if not user:
                    user = UserSettings(discord_user_id=interaction.user.id, home_icao=icao)
                    session.add(user)
                else:
                    user.home_icao = icao
                await session.commit()

            await interaction.response.send_message(f"✅ Default home airport updated to **{icao}**.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error setting home airport: {e}")
            await interaction.response.send_message(f"⚠️ Failed to update home airport: `{e}`", ephemeral=True)

    @flight_group.command(name="sync", description="Manually sync upcoming flight events from your Google Calendar")
    async def sync_calendar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            count = await calendar_service.sync_user_calendar(interaction.user.id)
            await interaction.followup.send(f"🔄 Synced **{count}** upcoming flight event(s) from your calendar.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error syncing calendar: {e}")
            await interaction.followup.send(f"⚠️ Calendar sync failed: `{e}`", ephemeral=True)

    @flight_group.command(name="schedule", description="View all upcoming synced flight lessons")
    async def view_schedule(self, interaction: discord.Interaction):
        try:
            now = datetime.utcnow()
            async with AsyncSessionLocal() as session:
                stmt = select(FlightEvent).where(
                    and_(
                        FlightEvent.discord_user_id == interaction.user.id,
                        FlightEvent.start_time > now
                    )
                ).order_by(FlightEvent.start_time.asc()).limit(10)
                events = (await session.execute(stmt)).scalars().all()

            if not events:
                await interaction.response.send_message("📅 No upcoming flights found in schedule.", ephemeral=True)
                return

            lines = []
            for e in events:
                dest = f" ➔ {e.destination_icao}" if e.destination_icao else ""
                lines.append(f"• **{e.summary}** ({e.departure_icao}{dest})\n  <t:{int(e.start_time.timestamp())}:F> (<t:{int(e.start_time.timestamp())}:R>)")

            embed = discord.Embed(
                title="📅 Synced Upcoming Flight Lessons",
                description="\n\n".join(lines),
                color=0x3498DB
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error viewing schedule: {e}")
            await interaction.response.send_message(f"⚠️ Failed to retrieve schedule: `{e}`", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(FlightCommands(bot))

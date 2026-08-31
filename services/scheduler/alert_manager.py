import io
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Set
import discord
from sqlalchemy import select, and_

from database.session import AsyncSessionLocal
from database.models import UserSettings, FlightEvent, AlertLog, PersonalMinima, ConvectiveAlertLog
from services.weather.awc_client import awc_client
from services.weather.decoder import METARDecoder
from services.weather.crosswind import CrosswindCalculator, airport_db
from services.weather.minima_checker import MinimaChecker
from services.weather.sigmet_monitor import sigmet_monitor
from services.notam.notam_client import notam_client
from services.radar.map_generator import radar_map_generator
from services.calendar.ical_service import calendar_service
from bot.views.briefing_embeds import BriefingEmbedBuilder, BriefingView
from config.settings import settings, is_user_allowed

logger = logging.getLogger(__name__)

class AlertManager:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info("AlertManager scheduler loop started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _scheduler_loop(self):
        """Main periodic loop running every 60 seconds."""
        await self.bot.wait_until_ready()
        last_cal_sync = datetime.min

        while self._running:
            try:
                now = datetime.utcnow()

                # 1. Periodic calendar sync (every 5 mins)
                if (now - last_cal_sync).total_seconds() >= settings.CALENDAR_POLL_INTERVAL_SECONDS:
                    for uid in settings.ALLOWED_USER_IDS:
                        await calendar_service.sync_user_calendar(uid)
                    last_cal_sync = now

                # 2. Check and dispatch pending milestone countdown alerts (6h, 3h, 2h, 1h, 15m)
                await self._check_and_dispatch_alerts(now)

                # 3. Constantly monitor real-time Convective SIGMETs over departure/home airports
                await self._check_convective_sigmet_alerts(now)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in AlertManager loop: {e}", exc_info=True)

            await asyncio.sleep(60)

    async def _check_convective_sigmet_alerts(self, now: datetime):
        """
        Continuously monitors active Convective SIGMETs and instantly alerts
        the user if any severe convective polygon appears over their departure or home airport.
        """
        sigmets = await awc_client.get_sigmets()
        if not sigmets:
            return

        async with AsyncSessionLocal() as session:
            for uid in settings.ALLOWED_USER_IDS:
                user = await session.get(UserSettings, uid)
                if not user:
                    continue

                # Collect airports of active interest: home airport + scheduled flights in next 24h
                airports_to_monitor: Set[str] = set()
                if user.home_icao:
                    airports_to_monitor.add(user.home_icao.upper())

                stmt = select(FlightEvent).where(
                    and_(
                        FlightEvent.discord_user_id == uid,
                        FlightEvent.start_time > now,
                        FlightEvent.start_time <= now + timedelta(hours=24)
                    )
                )
                events = (await session.execute(stmt)).scalars().all()
                for ev in events:
                    if ev.departure_icao:
                        airports_to_monitor.add(ev.departure_icao.upper())
                    if ev.destination_icao:
                        airports_to_monitor.add(ev.destination_icao.upper())

                # Check each airport for convective hazards
                for icao in airports_to_monitor:
                    hazards = sigmet_monitor.evaluate_airport_convective_hazards(icao, sigmets, proximity_nm=20.0)
                    for h in hazards:
                        sig_id = h["sigmet_id"]

                        # Deduplicate: check if already alerted
                        log_stmt = select(ConvectiveAlertLog).where(
                            and_(
                                ConvectiveAlertLog.sigmet_id == sig_id,
                                ConvectiveAlertLog.discord_user_id == uid,
                                ConvectiveAlertLog.icao == icao
                            )
                        )
                        already_alerted = (await session.execute(log_stmt)).scalar_one_or_none()
                        if not already_alerted:
                            logger.warning(f"⚡ Convective SIGMET {sig_id} detected over {icao}! Dispatching urgent alert to user {uid}")
                            sent = await self.send_convective_sigmet_alert(uid, icao, h, sigmets)
                            if sent:
                                new_log = ConvectiveAlertLog(
                                    sigmet_id=sig_id,
                                    discord_user_id=uid,
                                    icao=icao,
                                    hazard=h.get("hazard", "CONVECTIVE"),
                                    sent_at=datetime.utcnow()
                                )
                                session.add(new_log)
                                await session.commit()

    async def send_convective_sigmet_alert(
        self,
        discord_user_id: int,
        icao: str,
        hazard_info: Dict[str, Any],
        sigmets: List[Dict[str, Any]]
    ) -> bool:
        if not is_user_allowed(discord_user_id):
            return False

        try:
            user_obj = await self.bot.fetch_user(discord_user_id)
            if not user_obj:
                return False

            # Generate ForeFlight Sectional Map highlighting the convective polygon
            map_bytes = None
            try:
                map_bytes = await radar_map_generator.generate_sectional_overview_map(
                    dep_icao=icao,
                    dep_fltcat="IFR",
                    sigmets=sigmets,
                    radius_nm=85.0
                )
            except Exception as e:
                logger.error(f"Error generating map for convective alert: {e}")

            embed = BriefingEmbedBuilder.build_convective_alert_embed(icao, hazard_info)
            file = discord.File(io.BytesIO(map_bytes), filename="convective_sigmet.png") if map_bytes else None
            if file:
                embed.set_image(url="attachment://convective_sigmet.png")

            if file:
                await user_obj.send(
                    content=f"🚨 **URGENT AVIATION WEATHER ALERT**: Active Convective SIGMET affecting **{icao}**!",
                    embed=embed,
                    file=file
                )
            else:
                await user_obj.send(
                    content=f"🚨 **URGENT AVIATION WEATHER ALERT**: Active Convective SIGMET affecting **{icao}**!",
                    embed=embed
                )
            return True
        except Exception as e:
            logger.error(f"Failed to deliver Convective SIGMET DM alert: {e}")
            return False

    async def _check_and_dispatch_alerts(self, now: datetime):
        async with AsyncSessionLocal() as session:
            stmt = select(FlightEvent).where(
                and_(
                    FlightEvent.start_time > now,
                    FlightEvent.start_time <= now + timedelta(hours=8)
                )
            )
            result = await session.execute(stmt)
            events = result.scalars().all()

            for event in events:
                user = await session.get(UserSettings, event.discord_user_id)
                if not user or not is_user_allowed(user.discord_user_id):
                    continue

                intervals = user.get_alert_intervals()
                minutes_until_start = (event.start_time - now).total_seconds() / 60.0

                for interval in sorted(intervals, reverse=True):
                    if minutes_until_start <= interval and minutes_until_start >= (interval - 10):
                        alert_stmt = select(AlertLog).where(
                            and_(
                                AlertLog.event_id == event.event_id,
                                AlertLog.interval_minutes == interval
                            )
                        )
                        existing_alert = (await session.execute(alert_stmt)).scalar_one_or_none()
                        if not existing_alert:
                            logger.info(f"Triggering {interval}m countdown briefing for event {event.summary}")
                            success = await self.send_flight_briefing(
                                discord_user_id=event.discord_user_id,
                                event_title=event.summary,
                                dep_icao=event.departure_icao,
                                dest_icao=event.destination_icao,
                                start_time=event.start_time,
                                interval_minutes=interval
                            )
                            if success:
                                log_entry = AlertLog(
                                    event_id=event.event_id,
                                    discord_user_id=event.discord_user_id,
                                    interval_minutes=interval,
                                    sent_at=datetime.utcnow(),
                                    status="SENT"
                                )
                                session.add(log_entry)
                                await session.commit()

    async def send_flight_briefing(
        self,
        discord_user_id: int,
        event_title: str,
        dep_icao: str,
        dest_icao: Optional[str],
        start_time: datetime,
        interval_minutes: Optional[int] = None
    ) -> bool:
        if not is_user_allowed(discord_user_id):
            return False

        user_obj = await self.bot.fetch_user(discord_user_id)
        if not user_obj:
            return False

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

        # 4. User Personal Minimums
        async with AsyncSessionLocal() as session:
            minima = await session.get(PersonalMinima, discord_user_id)
        
        minima_eval = MinimaChecker.evaluate(decoded_metar_dep or {}, runway_evals, minima)

        # 5. SIGMETs & NOTAMs
        sigmets = await awc_client.get_sigmets()
        notams = await notam_client.get_notams_for_station(dep_icao)

        # 6. Milestone Label
        if interval_minutes:
            if interval_minutes >= 60:
                hrs = interval_minutes // 60
                label = f"{hrs} HOUR{'S' if hrs > 1 else ''} OUT BRIEFING"
            else:
                label = f"{interval_minutes} MINUTES OUT RAMP CHECK"
        else:
            label = "ON-DEMAND PREFLIGHT BRIEFING"

        # 7. Generate Maps (100 NM Sectional & Tactical)
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
            logger.error(f"Error rendering maps for DM alert: {me}")

        # 8. Build Discord Embed
        embed = BriefingEmbedBuilder.build_briefing_embed(
            event_title=event_title,
            departure_icao=dep_icao,
            destination_icao=dest_icao,
            start_time_utc=start_time,
            metar_dep=decoded_metar_dep,
            metar_dest=decoded_metar_dest,
            taf_dep=decoded_taf_dep,
            runway_evals=runway_evals,
            minima_eval=minima_eval,
            notams=notams,
            sigmets=sigmets,
            milestone_label=label
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

        try:
            if file:
                await user_obj.send(embed=embed, file=file, view=view)
            else:
                await user_obj.send(embed=embed, view=view)
            logger.info(f"Sent DM briefing to user {discord_user_id} for {event_title}")
            return True
        except Exception as e:
            logger.error(f"Failed to deliver Discord DM to user {discord_user_id}: {e}")
            return False

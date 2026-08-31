import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import icalendar
import recurring_ical_events
from sqlalchemy import select, delete

from database.session import AsyncSessionLocal
from database.models import FlightEvent, UserSettings
from .event_extractor import FlightEventExtractor

logger = logging.getLogger(__name__)

class CalendarService:
    def __init__(self):
        self._headers = {"User-Agent": "PilotBrief-CalendarSyncer/1.0"}

    async def sync_user_calendar(self, user_id: int) -> int:
        """
        Fetches user's iCal URL, parses upcoming events for next 7 days,
        extracts flight lessons, and upserts them into the database.
        Returns count of synced upcoming flights.
        """
        async with AsyncSessionLocal() as session:
            user = await session.get(UserSettings, user_id)
            if not user or not user.ical_url:
                return 0

            ical_url = user.ical_url.strip()
            home_icao = user.home_icao or settings.HOME_ICAO

        # Fetch iCal feed
        ical_data = None
        try:
            async with aiohttp.ClientSession(headers=self._headers) as client:
                async with client.get(ical_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        ical_data = await resp.read()
                    else:
                        logger.error(f"Failed to fetch iCal for user {user_id}: HTTP {resp.status}")
                        return 0
        except Exception as e:
            logger.error(f"Error downloading iCal feed for user {user_id}: {e}")
            return 0

        try:
            cal = icalendar.Calendar.from_ical(ical_data)
        except Exception as e:
            logger.error(f"Failed to parse iCal data: {e}")
            return 0

        # Expand recurring events for window: now to +7 days
        now_utc = datetime.now(timezone.utc)
        window_end = now_utc + timedelta(days=7)

        try:
            events = recurring_ical_events.of(cal).between(now_utc - timedelta(hours=2), window_end)
        except Exception as e:
            logger.error(f"Error expanding recurring events: {e}")
            return 0

        synced_count = 0
        async with AsyncSessionLocal() as session:
            for component in events:
                summary = str(component.get("summary", ""))
                desc = str(component.get("description", ""))

                if not FlightEventExtractor.is_flight_event(summary, desc):
                    continue

                dtstart = component.get("dtstart").dt
                dtend = component.get("dtend").dt if component.get("dtend") else dtstart + timedelta(hours=2)

                # Ensure timezone awareness converted to naive UTC for DB indexing
                if hasattr(dtstart, "tzinfo") and dtstart.tzinfo is not None:
                    start_utc = dtstart.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    start_utc = dtstart

                if hasattr(dtend, "tzinfo") and dtend.tzinfo is not None:
                    end_utc = dtend.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    end_utc = dtend

                # Extract UID or generate deterministic ID
                uid = str(component.get("uid", f"{user_id}_{start_utc.isoformat()}_{summary[:20]}"))

                dep_icao, dest_icao = FlightEventExtractor.extract_airports(summary, desc, default_home=home_icao)

                # Upsert into database
                existing = await session.get(FlightEvent, uid)
                if existing:
                    existing.summary = summary
                    existing.description = desc
                    existing.departure_icao = dep_icao
                    existing.destination_icao = dest_icao
                    existing.start_time = start_utc
                    existing.end_time = end_utc
                    existing.last_updated = datetime.utcnow()
                else:
                    new_flight = FlightEvent(
                        event_id=uid,
                        discord_user_id=user_id,
                        summary=summary,
                        description=desc,
                        departure_icao=dep_icao,
                        destination_icao=dest_icao,
                        start_time=start_utc,
                        end_time=end_utc,
                        last_updated=datetime.utcnow()
                    )
                    session.add(new_flight)
                synced_count += 1

            # Update last synced time
            user_obj = await session.get(UserSettings, user_id)
            if user_obj:
                user_obj.last_synced_at = datetime.utcnow()

            await session.commit()

        logger.info(f"Successfully synced {synced_count} flight events for user {user_id}")
        return synced_count

calendar_service = CalendarService()

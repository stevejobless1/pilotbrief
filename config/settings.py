from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import List, Union, Any
import json

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Discord Bot Token
    DISCORD_BOT_TOKEN: str = Field(default="", description="Discord Bot Token")

    # Security Whitelist - Restricted strictly to authorized Discord User IDs
    # Default is the requested Discord ID: 454870771039469568
    ALLOWED_USER_IDS: List[int] = Field(default=[454870771039469568], description="Whitelisted Discord User IDs")

    # Default Home Airport (ICAO)
    HOME_ICAO: str = Field(default="KPAO", description="Default home airport ICAO")

    # Optional default iCal URL (can also be configured per user via slash command)
    ICAL_URL: str = Field(default="", description="Google Calendar Private iCal URL")

    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///data/pilotbrief.db", description="SQLAlchemy DB URL")

    # Countdown intervals in minutes: 6h (360), 3h (180), 2h (120), 1h (60), 15m (15)
    DEFAULT_ALERT_INTERVALS: List[int] = Field(default=[360, 180, 120, 60, 15], description="Milestone countdown minutes")

    # Calendar polling interval in seconds
    CALENDAR_POLL_INTERVAL_SECONDS: int = Field(default=300, description="Calendar poll interval in seconds")

    # NOAA AWC API Base URL
    AWC_BASE_URL: str = Field(default="https://aviationweather.gov/api/data", description="NOAA AWC API base")

    @field_validator("ALLOWED_USER_IDS", mode="before")
    @classmethod
    def parse_allowed_ids(cls, v: Union[str, int, List[Any]]) -> List[int]:
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    return [int(x) for x in json.loads(v)]
                except Exception:
                    pass
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        return [454870771039469568]

settings = Settings()

def is_user_allowed(user_id: int) -> bool:
    """Checks if a given Discord user ID is present in the security whitelist."""
    return user_id in settings.ALLOWED_USER_IDS

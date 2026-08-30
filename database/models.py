from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Text, select
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class UserSettings(Base):
    __tablename__ = "user_settings"

    discord_user_id = Column(BigInteger, primary_key=True)
    home_icao = Column(String(10), default="KPAO")
    ical_url = Column(Text, nullable=True)
    alert_intervals_csv = Column(String(100), default="360,180,120,60,15")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)

    minima = relationship("PersonalMinima", back_populates="user", uselist=False, cascade="all, delete-orphan")
    events = relationship("FlightEvent", back_populates="user", cascade="all, delete-orphan")

    def get_alert_intervals(self) -> List[int]:
        if not self.alert_intervals_csv:
            return [360, 180, 120, 60, 15]
        return [int(x.strip()) for x in self.alert_intervals_csv.split(",") if x.strip().isdigit()]

class PersonalMinima(Base):
    __tablename__ = "personal_minima"

    discord_user_id = Column(BigInteger, ForeignKey("user_settings.discord_user_id"), primary_key=True)
    max_surface_wind_kt = Column(Integer, default=20)
    max_crosswind_kt = Column(Integer, default=12)
    max_gust_factor_kt = Column(Integer, default=8)
    min_ceiling_ft = Column(Integer, default=2000)
    min_visibility_sm = Column(Integer, default=5)

    user = relationship("UserSettings", back_populates="minima")

class FlightEvent(Base):
    __tablename__ = "flight_events"

    event_id = Column(String(255), primary_key=True)
    discord_user_id = Column(BigInteger, ForeignKey("user_settings.discord_user_id"), index=True)
    summary = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    departure_icao = Column(String(10), nullable=False)
    destination_icao = Column(String(10), nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    last_updated = Column(DateTime, default=datetime.utcnow)

    user = relationship("UserSettings", back_populates="events")
    alerts = relationship("AlertLog", back_populates="event", cascade="all, delete-orphan")

class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(255), ForeignKey("flight_events.event_id"), index=True)
    discord_user_id = Column(BigInteger, index=True)
    interval_minutes = Column(Integer, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="SENT")

    event = relationship("FlightEvent", back_populates="alerts")

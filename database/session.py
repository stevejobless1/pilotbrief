import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from config.settings import settings
from .models import Base, UserSettings, PersonalMinima

# Ensure data directory exists if using sqlite
if "sqlite" in settings.DATABASE_URL:
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    db_dir = Path(db_path).parent
    if db_dir and not db_dir.exists():
        db_dir.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Auto-seed default user if allowed list exists
    async with AsyncSessionLocal() as session:
        for uid in settings.ALLOWED_USER_IDS:
            existing = await session.get(UserSettings, uid)
            if not existing:
                new_user = UserSettings(
                    discord_user_id=uid,
                    home_icao=settings.HOME_ICAO,
                    ical_url=settings.ICAL_URL if settings.ICAL_URL else None
                )
                new_minima = PersonalMinima(discord_user_id=uid)
                session.add(new_user)
                session.add(new_minima)
        await session.commit()

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

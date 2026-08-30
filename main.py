import asyncio
import logging
import signal
import sys
from config.settings import settings
from bot.client import PilotBriefBot
from services.scheduler.alert_manager import AlertManager

# Setup clean logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("PilotBrief")

def main():
    if not settings.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN is not set in environment or .env file!")
        sys.exit(1)

    logger.info("Starting PilotBrief Aviation Discord Bot...")
    logger.info(f"Authorized Whitelist: {settings.ALLOWED_USER_IDS}")

    bot = PilotBriefBot()
    alert_manager = AlertManager(bot)

    @bot.event
    async def on_ready():
        logger.info(f"Bot connected as {bot.user}")
        alert_manager.start()

    try:
        bot.run(settings.DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        alert_manager.stop()

if __name__ == "__main__":
    main()

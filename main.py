import asyncio
import logging
import signal
import sys
from config.settings import settings
from bot.client import PilotBriefBot
from services.scheduler.alert_manager import AlertManager
from services.web.server import run_web_server

# Setup clean logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("PilotBrief")

async def async_main():
    runner = None
    if settings.WEB_ENABLED:
        logger.info(f"Starting PilotBrief Web Dashboard on http://{settings.WEB_HOST}:{settings.WEB_PORT}...")
        runner = await run_web_server(host=settings.WEB_HOST, port=settings.WEB_PORT)

    if settings.DISCORD_BOT_TOKEN:
        logger.info("Starting PilotBrief Aviation Discord Bot...")
        logger.info(f"Authorized Whitelist: {settings.ALLOWED_USER_IDS}")
        bot = PilotBriefBot()
        alert_manager = AlertManager(bot)

        @bot.event
        async def on_ready():
            logger.info(f"Bot connected as {bot.user}")
            alert_manager.start()

        try:
            await bot.start(settings.DISCORD_BOT_TOKEN)
        finally:
            alert_manager.stop()
            if not bot.is_closed():
                await bot.close()
            if runner:
                await runner.cleanup()
    else:
        logger.warning("DISCORD_BOT_TOKEN not provided. Running in Web Map Standalone Mode.")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            if runner:
                await runner.cleanup()

def main():
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down PilotBrief...")

if __name__ == "__main__":
    main()

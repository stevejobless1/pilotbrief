import discord
from discord.ext import commands
import logging
from config.settings import settings, is_user_allowed
from database.session import init_db

logger = logging.getLogger(__name__)

class PilotBriefBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        # Initialize SQLite database
        await init_db()

        # Load extension cogs
        await self.load_extension("bot.cogs.flight_commands")
        await self.load_extension("bot.cogs.settings_commands")

        # Global interaction security check: Whitelist guard
        async def global_interaction_check(interaction: discord.Interaction) -> bool:
            if not is_user_allowed(interaction.user.id):
                logger.warning(f"Unauthorized access attempted by user {interaction.user.id} ({interaction.user.name})")
                await interaction.response.send_message(
                    "⛔ **Access Denied**: You are not authorized to use this private aviation bot.",
                    ephemeral=True
                )
                return False
            return True

        self.tree.interaction_check = global_interaction_check

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands.")
        except Exception as e:
            logger.error(f"Error syncing slash commands: {e}")

    async def on_ready(self):
        logger.info(f"PilotBrief Bot logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="METARs & Flight Lessons ✈️")
        )

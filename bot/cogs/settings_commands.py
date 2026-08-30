import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from database.session import AsyncSessionLocal
from database.models import UserSettings, PersonalMinima
from services.calendar.ical_service import calendar_service

class SettingsCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    settings_group = app_commands.Group(name="settings", description="Configure PilotBrief preferences and minimums")

    @settings_group.command(name="link-calendar", description="Link your private Google Calendar iCal feed URL")
    @app_commands.describe(ical_url="Secret private address in iCal format (.ics) from Google Calendar Settings")
    async def link_calendar(self, interaction: discord.Interaction, ical_url: str):
        await interaction.response.defer(ephemeral=True)
        url = ical_url.strip()

        async with AsyncSessionLocal() as session:
            user = await session.get(UserSettings, interaction.user.id)
            if not user:
                user = UserSettings(discord_user_id=interaction.user.id, ical_url=url)
                session.add(user)
            else:
                user.ical_url = url
            await session.commit()

        # Perform initial sync
        count = await calendar_service.sync_user_calendar(interaction.user.id)
        await interaction.followup.send(
            f"✅ **Google Calendar Linked Successfully!**\n"
            f"Synced **{count}** upcoming flight lesson(s). You will now receive automatic DMs at **6h, 3h, 2h, 1h, and 15m** before departure.",
            ephemeral=True
        )

    @settings_group.command(name="minima", description="Configure your student pilot personal minimums")
    @app_commands.describe(
        max_surface_wind="Max surface wind speed in knots (e.g. 15)",
        max_crosswind="Max crosswind component in knots (e.g. 10)",
        max_gust_factor="Max allowable gust spread in knots (e.g. 8)",
        min_ceiling="Min cloud ceiling in feet AGL (e.g. 2500)",
        min_visibility="Min visibility in statute miles (e.g. 6)"
    )
    async def set_minima(
        self,
        interaction: discord.Interaction,
        max_surface_wind: int = None,
        max_crosswind: int = None,
        max_gust_factor: int = None,
        min_ceiling: int = None,
        min_visibility: int = None
    ):
        async with AsyncSessionLocal() as session:
            minima = await session.get(PersonalMinima, interaction.user.id)
            if not minima:
                minima = PersonalMinima(discord_user_id=interaction.user.id)
                session.add(minima)

            if max_surface_wind is not None:
                minima.max_surface_wind_kt = max_surface_wind
            if max_crosswind is not None:
                minima.max_crosswind_kt = max_crosswind
            if max_gust_factor is not None:
                minima.max_gust_factor_kt = max_gust_factor
            if min_ceiling is not None:
                minima.min_ceiling_ft = min_ceiling
            if min_visibility is not None:
                minima.min_visibility_sm = min_visibility

            await session.commit()

        await interaction.response.send_message(
            f"✅ **Personal Minimums Updated:**\n"
            f"• Max Surface Wind: `{minima.max_surface_wind_kt} kt`\n"
            f"• Max Crosswind: `{minima.max_crosswind_kt} kt`\n"
            f"• Max Gust Factor: `+{minima.max_gust_factor_kt} kt`\n"
            f"• Min Ceiling: `{minima.min_ceiling_ft} ft AGL`\n"
            f"• Min Visibility: `{minima.min_visibility_sm} SM`",
            ephemeral=True
        )

    @settings_group.command(name="intervals", description="Customize countdown alert intervals in minutes")
    @app_commands.describe(intervals_csv="Comma-separated minutes before flight (default: 360,180,120,60,15)")
    async def set_intervals(self, interaction: discord.Interaction, intervals_csv: str):
        cleaned = ",".join([x.strip() for x in intervals_csv.split(",") if x.strip().isdigit()])
        if not cleaned:
            await interaction.response.send_message("❌ Invalid interval format. Use comma separated numbers like `360,180,120,60,15`.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            user = await session.get(UserSettings, interaction.user.id)
            if not user:
                user = UserSettings(discord_user_id=interaction.user.id, alert_intervals_csv=cleaned)
                session.add(user)
            else:
                user.alert_intervals_csv = cleaned
            await session.commit()

        await interaction.response.send_message(f"✅ Alert milestone intervals updated to: `{cleaned}` minutes before departure.", ephemeral=True)

    @settings_group.command(name="view", description="View current configuration, minimums, and sync status")
    async def view_settings(self, interaction: discord.Interaction):
        async with AsyncSessionLocal() as session:
            user = await session.get(UserSettings, interaction.user.id)
            minima = await session.get(PersonalMinima, interaction.user.id)

        home = user.home_icao if user else "KPAO"
        cal_status = "🔗 Linked" if (user and user.ical_url) else "❌ Not Linked (`/settings link-calendar`)"
        last_sync = f"<t:{int(user.last_synced_at.timestamp())}:R>" if (user and user.last_synced_at) else "Never"
        intervals = user.get_alert_intervals() if user else [360, 180, 120, 60, 15]

        embed = discord.Embed(
            title="⚙️ PilotBrief User Configuration",
            color=0x9B59B6
        )
        embed.add_field(name="🏠 Default Home Airport", value=f"`{home}`", inline=True)
        embed.add_field(name="📅 Google Calendar", value=f"{cal_status}\nLast Synced: {last_sync}", inline=True)
        embed.add_field(name="⏰ Alert Intervals", value=f"`{', '.join([str(x) + 'm' for x in intervals])}`", inline=False)

        if minima:
            min_str = (
                f"• Max Surface Wind: `{minima.max_surface_wind_kt} kt`\n"
                f"• Max Crosswind: `{minima.max_crosswind_kt} kt`\n"
                f"• Max Gust Factor: `+{minima.max_gust_factor_kt} kt`\n"
                f"• Min Ceiling: `{minima.min_ceiling_ft} ft AGL`\n"
                f"• Min Visibility: `{minima.min_visibility_sm} SM`"
            )
            embed.add_field(name="🛡️ Student Personal Minimums", value=min_str, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCommands(bot))

# skyblock_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import asyncio # Keep asyncio if needed for other functions


class SkyblockCog(commands.Cog, name="Skyblock Functions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog loaded and ready.")

async def setup(bot: commands.Bot):
    await bot.add_cog(SkyblockCog(bot))

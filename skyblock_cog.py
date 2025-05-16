# skyblock_cog.py

import discord
from discord import app_commands
from discord.ext import commands
# Removed os, time, json as they are now in forge_cog
import asyncio # Keep asyncio if needed for other functions

# Keep only necessary imports from skyblock.py if other commands use them
# from skyblock import ...

class SkyblockCog(commands.Cog, name="Skyblock Funktionen"):
    """
    Dieser Cog bündelt alle Skyblock-spezifischen Befehle und Funktionen.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog wurde geladen und ist bereit.")

async def setup(bot: commands.Bot):
    # Fügt eine Instanz des Cogs zum Bot hinzu
    await bot.add_cog(SkyblockCog(bot))

# Keep asyncio import if needed
# import asyncio
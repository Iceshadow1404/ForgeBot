# bot.py

import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents)

INITIAL_EXTENSIONS = [
    'skyblock_cog',      # Keep if other skyblock commands exist
    'forge_cog',         # Keep the forge cog
    'registration_cog'   # Add the new registration cog
]

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    print(f'Bot ID: {bot.user.id}')
    print(f'Discord.py Version: {discord.__version__}')
    print('--------------------------------------------------')
    try:
        await bot.change_presence(activity=discord.Game(name="Hypixel Skyblock | /help"))
        print("Bot presence set.")
    except Exception as e:
        print(f"Error setting bot presence: {e}")

async def setup_hook():
    print("Setup Hook running...")
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            print(f"Cog '{extension}' loaded successfully.")
        except commands.ExtensionNotFound:
            print(f"ERROR: Cog '{extension}' not found.")
        except commands.NoEntryPointError:
            print(f"ERROR: Cog '{extension}' has no 'setup' function.")
        except commands.ExtensionFailed as e:
            print(f"ERROR: Cog '{extension}' could not be loaded: {e}")
            print(f"Original error: {e.original}")

    try:
        # Sync commands globally (or to a specific guild for faster testing)
        synced = await bot.tree.sync() # Or bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        print(f"{len(synced)} global Slash Command(s) synced.")
        if not synced:
            print("No global commands found to sync or already current.")
    except Exception as e:
        print(f"Error syncing Slash Commands: {e}")

bot.setup_hook = setup_hook


def startbot():
    if TOKEN is None:
        print("ERROR: Discord Token not found. Ensure .env file is set up correctly and contains DISCORD_TOKEN.")
    else:
        try:
            print("Bot starting...")
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("ERROR: Invalid Discord Token. Please check your token in the .env file.")
        except Exception as e:
            print(f"An unexpected error occurred while starting the bot: {e}")
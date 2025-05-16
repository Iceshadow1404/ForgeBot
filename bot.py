# bot.py

import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents)

# Add 'forge_cog' to the list
INITIAL_EXTENSIONS = [
    'skyblock_cog', # Keep skyblock_cog if you have other skyblock commands there
    'forge_cog'     # Add the new forge_cog
]

@bot.event
async def on_ready():
    print(f'{bot.user.name} hat sich erfolgreich mit Discord verbunden!')
    print(f'Bot ID: {bot.user.id}')
    print(f'Discord.py Version: {discord.__version__}')
    print('--------------------------------------------------')
    try:
        await bot.change_presence(activity=discord.Game(name="Hypixel Skyblock | /hilfe"))
        print("Bot-Präsenz wurde gesetzt.")
    except Exception as e:
        print(f"Fehler beim Setzen der Bot-Präsenz: {e}")

async def setup_hook():
    print("Setup Hook wird ausgeführt...")
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            print(f"Cog '{extension}' erfolgreich geladen.")
        except commands.ExtensionNotFound:
            print(f"FEHLER: Cog '{extension}' nicht gefunden.")
        except commands.NoEntryPointError:
            print(f"FEHLER: Cog '{extension}' hat keine 'setup'-Funktion.")
        except commands.ExtensionFailed as e:
            print(f"FEHLER: Cog '{extension}' konnte nicht geladen werden: {e}")
            print(f"Originaler Fehler: {e.original}")

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} globale Slash Command(s) synchronisiert.")
        if not synced:
            print("Keine globalen Befehle zum Synchronisieren gefunden oder bereits aktuell.")
    except Exception as e:
        print(f"Fehler beim Synchronisieren der Slash Commands: {e}")

bot.setup_hook = setup_hook

# Keep your other commands here if you want
@bot.tree.command(name="test", description="Gibt einfach nur True als Antwort.")
async def test_slash_command(interaction: discord.Interaction):
    await interaction.response.send_message("True", ephemeral=True)
    print(f"/test Befehl von {interaction.user} ausgeführt.")

@bot.tree.command(name="hallo", description="Sagt Hallo zu einem Nutzer.")
@discord.app_commands.describe(nutzer="Der Nutzer, dem Hallo gesagt werden soll")
async def hallo_slash_command(interaction: discord.Interaction, nutzer: discord.Member):
    await interaction.response.send_message(f"Hallo {nutzer.mention}!")
    print(f"/hallo Befehl von {interaction.user} für {nutzer.name} ausgeführt.")


if __name__ == "__main__":
    if TOKEN is None:
        print("FEHLER: Discord Token nicht gefunden. Stelle sicher, dass die .env Datei korrekt eingerichtet ist und DISCORD_TOKEN enthält.")
    else:
        try:
            print("Bot wird gestartet...")
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("FEHLER: Ungültiger Discord Token. Bitte überprüfe deinen Token in der .env Datei.")
        except Exception as e:
            print(f"Ein unerwarteter Fehler ist beim Starten des Bots aufgetreten: {e}")
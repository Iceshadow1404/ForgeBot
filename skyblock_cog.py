# skyblock_cog.py

import discord
from discord import app_commands
from discord.ext import commands
import os # Import os to access environment variables
import asyncio # Keep asyncio for potential delays
import time

# Import functions from skyblock.py
from skyblock import get_uuid, format_uuid, get_player_profiles, find_profile_by_name

class SkyblockCog(commands.Cog, name="Skyblock Funktionen"):
    """
    Dieser Cog bündelt alle Skyblock-spezifischen Befehle und Funktionen.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Load the Hypixel API Key from environment variables
        self.hypixel_api_key = os.getenv("HYPIXEL_API_KEY")
        if not self.hypixel_api_key:
            print("WARNING: HYPIXEL_API_KEY not found in environment variables.")
            print("Skyblock API commands will not work.")

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog wurde geladen und ist bereit.")

    @app_commands.command(name="forge", description="Zeigt die aktuellen Items in der Skyblock Forge eines Spielers an.")
    @app_commands.describe(username="Der Minecraft-Name des Spielers.")
    @app_commands.describe(profile_name="Optional: Der Name des Skyblock-Profils (z.B. 'Apple'). Wenn nicht angegeben, wird das zuletzt gespielte Profil verwendet.")
    async def forge_command(self, interaction: discord.Interaction, username: str, profile_name: str = None):
        """
        Fetches and displays the items currently in the player's Skyblock forge.
        """
        if not self.hypixel_api_key:
            await interaction.response.send_message(
                "Der API-Schlüssel für Hypixel ist nicht konfiguriert. Bitte informiere den Bot-Betreiber.",
                ephemeral=True # Only visible to the user who used the command
            )
            return

        # Defer the response as API calls can take time
        await interaction.response.defer()

        # 1. Get the player's UUID
        uuid = get_uuid(username)
        if not uuid:
            await interaction.followup.send(f"Konnte den Minecraft-Spieler '{username}' nicht finden.")
            return

        uuid_dashed = format_uuid(uuid)

        # 2. Get Skyblock profiles
        profiles_data = get_player_profiles(self.hypixel_api_key, uuid_dashed)

        if not profiles_data or not profiles_data.get("success", False):
            await interaction.followup.send(f"Fehler beim Abrufen der Skyblock-Profile für '{username}'.")
            return

        profiles = profiles_data.get("profiles", [])
        if not profiles:
            await interaction.followup.send(f"Keine Skyblock-Profile für '{username}' gefunden.")
            return

        # 3. Find the target profile
        target_profile = None
        if profile_name:
            target_profile = find_profile_by_name(profiles_data, profile_name)
            if not target_profile:
                await interaction.followup.send(f"Profil '{profile_name}' für '{username}' nicht gefunden.")
                return
        else:
            # If no profile name is given, use the last played profile
            # The Hypixel API usually returns profiles with 'selected': true for the last played
            # Or you can sort by the 'last_save' timestamp if 'selected' isn't reliable
            for profile in profiles:
                 # Check for 'selected' key if available, otherwise use a heuristic like last_save
                 if profile.get("selected", False): # Prioritize 'selected'
                     target_profile = profile
                     break
            if not target_profile and profiles:
                 # Fallback: Use the profile with the latest last_save if 'selected' isn't found
                 target_profile = max(profiles, key=lambda p: p.get("members", {}).get(uuid, {}).get("last_save", 0))


            if not target_profile:
                 await interaction.followup.send(f"Konnte das zuletzt gespielte Profil für '{username}' nicht bestimmen.")
                 return

        profile_id = target_profile.get("profile_id")
        profile_cute_name = target_profile.get("cute_name", "Unbekanntes Profil")

        # 4. Extract Forge Data
        try:
            # Navigate through the nested structure
            member_data = target_profile.get("members", {}).get(uuid, {})
            forge_data = member_data.get("forge", {}).get("forge_processes", {}).get("forge_1", {})

            if not forge_data:
                await interaction.followup.send(f"Keine aktiven Gegenstände in der Forge auf Profil '{profile_cute_name}' von '{username}'.")
                return

            # 5. Format and display forge items
            forge_items = []
            for slot, item_data in forge_data.items():
                item_id = item_data.get("id", "Unbekannter Gegenstand")
                item_type = item_data.get("type", "Unbekannter Typ")
                start_time_ms = item_data.get("startTime")

                # Calculate remaining time if startTime is available
                remaining_time_str = "Zeit unbekannt"
                if start_time_ms:
                    # Hypixel API time is in milliseconds
                    start_time_sec = start_time_ms / 1000
                    current_time_sec = time.time()
                    # To calculate remaining time, you would need the total forging time for the item_id.
                    # This data is not typically in the profile API.
                    # For simplicity, we'll just show the item and start time for now.
                    # You could potentially add a lookup for item forging times if you have that data elsewhere.
                    # For this response, we'll just list the item ID and slot.
                    forge_items.append(f"Slot {slot}: {item_id} ({item_type})")

            if not forge_items:
                 await interaction.followup.send(f"Keine aktiven Gegenstände in der Forge auf Profil '{profile_cute_name}' von '{username}'.")
                 return


            response_message = f"Aktuelle Items in der Forge auf Profil '{profile_cute_name}' von '{username}':\n"
            response_message += "\n".join(forge_items)

            await interaction.followup.send(response_message)

        except Exception as e:
            print(f"Error processing forge data for {username}: {e}")
            await interaction.followup.send(f"Ein interner Fehler ist beim Abrufen der Forge-Daten aufgetreten.")


async def setup(bot: commands.Bot):
    # Fügt eine Instanz des Cogs zum Bot hinzu
    await bot.add_cog(SkyblockCog(bot))

# Benötigt für die simulierte API-Verzögerung in den Beispielen oben
# import asyncio # Already imported